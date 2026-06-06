import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.artifacts.repository import PostgresArtifactStore
from app.gateways.llm import LLMGateway
from app.harness.context_pack import ContextPackBuilder, ContextSnapshot
from app.memory.gateway import PostgresMemoryGateway
from app.runtime.war_room import WarRoomNotifier
from app.schemas.agent import AgentLLMOutput, AgentRunResult, AgentTask
from app.schemas.artifacts import AgentArtifact, ArtifactRef
from app.schemas.common import MemoryScope, MemoryStatus
from app.schemas.context import BudgetRemaining, ContextPack
from app.schemas.memory import MemoryItem
from app.storage.models import AgentRun, ArtifactBlob, GraphThread


class AgentHarness:
    def __init__(
        self,
        *,
        session: Session,
        llm_gateway: LLMGateway,
        memory_gateway: PostgresMemoryGateway,
        artifact_store: PostgresArtifactStore | None = None,
        context_builder: ContextPackBuilder | None = None,
        war_room_notifier: WarRoomNotifier | None = None,
    ):
        self.session = session
        self.llm_gateway = llm_gateway
        self.memory_gateway = memory_gateway
        self.artifact_store = artifact_store or PostgresArtifactStore(session)
        self.context_builder = context_builder or ContextPackBuilder()
        self.war_room_notifier = war_room_notifier

    def build_context_pack(self, task: AgentTask) -> ContextPack:
        policy = task.policy
        memory_refs = self.memory_gateway.retrieve(
            agent_name=task.agent_name,
            task_brief=task.task_brief,
            memory_scopes=[scope.value for scope in policy.allowed_memory_scopes],
            filters={"thread_id": task.thread_id, "task_type": task.task_type},
            top_k=policy.max_memory_items,
            max_tokens=policy.max_context_tokens,
            policy=policy.model_dump(mode="json"),
        )
        tokens_estimate = _estimate_context_tokens(
            task_brief=task.task_brief,
            artifact_refs=task.artifact_refs,
            memory_refs=memory_refs,
            source_refs=task.source_refs,
        )
        return self.context_builder.build(
            ContextSnapshot(
                thread_id=task.thread_id,
                agent_name=task.agent_name,
                task_brief=task.task_brief,
                node_goal=task.node_goal,
                council_mode=task.council_mode,
                mode_reason=task.mode_reason,
                artifact_refs=task.artifact_refs,
                memory_refs=memory_refs,
                source_refs=task.source_refs,
                budget_remaining=BudgetRemaining(
                    max_context_tokens=policy.max_context_tokens,
                    estimated_context_tokens=tokens_estimate,
                ),
                excluded_context_reason=[
                    "full chat history, raw RecruitmentState, node_history, AgentRuns, "
                    "full artifacts, and all long-term memories are excluded by allowlist"
                ],
            )
        )

    def run_agent(self, task: AgentTask) -> AgentRunResult:
        self._ensure_graph_thread(task)
        run_id = uuid4()
        context_pack = self.build_context_pack(task)
        context_pack_ref = self._write_context_pack_blob(run_id, context_pack)
        run = AgentRun(
            id=run_id,
            thread_id=task.thread_id,
            node_name=task.node_name,
            agent_name=task.agent_name,
            council_mode=task.council_mode.value,
            context_pack_ref=context_pack_ref,
            input_summary=task.task_brief[:500],
            memory_refs=[item.model_dump(mode="json") for item in context_pack.memory_refs],
            source_refs=list(task.source_refs),
            token_estimate=context_pack.budget_remaining.estimated_context_tokens,
            status="running",
            started_at=datetime.now(UTC),
        )
        self.session.add(run)
        self.session.flush()

        try:
            llm_payload = self.llm_gateway.generate_structured(
                agent_name=task.agent_name,
                context_pack=context_pack,
                output_schema=AgentLLMOutput.model_json_schema(),
                schema_name="agent_llm_output",
                max_output_tokens=task.policy.max_output_tokens,
            )
            output = AgentLLMOutput.model_validate(llm_payload)
            artifact = self._write_agent_artifact(
                task=task,
                run_id=run_id,
                output=output,
            )
            memory_ref = self._write_run_memory(task=task, run_id=run_id, output=output)
            run.output_summary = output.summary
            run.artifact_refs = [artifact.model_dump(mode="json")]
            if memory_ref:
                run.memory_refs = [
                    *[item.model_dump(mode="json") for item in context_pack.memory_refs],
                    {"memory_ref": memory_ref, "scope": MemoryScope.run.value},
                ]
            run.status = "succeeded"
            run.ended_at = datetime.now(UTC)
            self.session.flush()
            self._enqueue_war_room_card(task, run_id, context_pack, output, artifact)
            return AgentRunResult(
                run_id=run_id,
                context_pack=context_pack,
                artifact=artifact,
                memory_refs=context_pack.memory_refs,
                token_estimate=context_pack.budget_remaining.estimated_context_tokens,
            )
        except Exception as exc:
            run.status = "failed"
            run.error = str(exc)
            run.ended_at = datetime.now(UTC)
            self.session.flush()
            raise

    def _ensure_graph_thread(self, task: AgentTask) -> None:
        self.session.execute(
            pg_insert(GraphThread)
            .values(
                id=task.thread_id,
                source=task.source,
                source_ref=task.source_ref,
                task_type=task.task_type,
                council_mode=task.council_mode.value,
                mode_reason=task.mode_reason,
                status="running",
                state_summary={
                    "agent_name": task.agent_name,
                    "node_name": task.node_name,
                    "task_brief": task.task_brief[:240],
                },
            )
            .on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "task_type": task.task_type,
                    "council_mode": task.council_mode.value,
                    "mode_reason": task.mode_reason,
                    "updated_at": datetime.now(UTC),
                },
            )
        )

    def _write_context_pack_blob(self, run_id: UUID, context_pack: ContextPack) -> str:
        content_ref = f"artifact://context-pack/{run_id}/v1"
        payload = context_pack.model_dump(mode="json")
        raw_text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.session.execute(
            pg_insert(ArtifactBlob)
            .values(
                content_ref=content_ref,
                media_type="application/json",
                content_json=payload,
                content_text=raw_text,
                sha256=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
            )
            .on_conflict_do_nothing(index_elements=["content_ref"])
        )
        return content_ref

    def _write_agent_artifact(
        self,
        *,
        task: AgentTask,
        run_id: UUID,
        output: AgentLLMOutput,
    ) -> ArtifactRef:
        artifact_id = uuid4()
        content_ref = f"artifact://agent-output/{task.thread_id}/{run_id}/v1"
        artifact = AgentArtifact(
            artifact_id=artifact_id,
            run_id=run_id,
            thread_id=task.thread_id,
            producer_agent=task.agent_name,
            artifact_type=task.output_artifact_type,
            summary=output.summary,
            content_ref=content_ref,
            evidence_refs=output.evidence_refs,
            source_refs=output.source_refs,
            pii_level=output.pii_level,
            version=1,
            size_tokens_estimate=_estimate_text_tokens(json.dumps(output.artifact_payload)),
        )
        self.artifact_store.write(
            artifact,
            task.policy.model_dump(mode="json"),
            payload=output.artifact_payload,
        )
        return ArtifactRef(
            artifact_id=artifact_id,
            kind=task.output_artifact_type,
            summary=output.summary,
            content_ref=content_ref,
            evidence_refs=output.evidence_refs,
            source_refs=output.source_refs,
            version=1,
            size_tokens_estimate=artifact.size_tokens_estimate,
        )

    def _write_run_memory(
        self,
        *,
        task: AgentTask,
        run_id: UUID,
        output: AgentLLMOutput,
    ) -> str | None:
        if not output.summary:
            return None
        item = MemoryItem(
            scope=MemoryScope.run,
            owner_agent=task.agent_name,
            summary=output.summary[:500],
            content_ref=f"memory://run/{run_id}/summary/v1",
            source_run_id=run_id,
            thread_id=task.thread_id,
            pii_level=output.pii_level,
            status=MemoryStatus.active,
            confidence=output.confidence,
            metadata={
                "thread_id": str(task.thread_id),
                "node_name": task.node_name,
                "agent_name": task.agent_name,
            },
        )
        return self.memory_gateway.propose_update(task.agent_name, item)

    def _enqueue_war_room_card(
        self,
        task: AgentTask,
        run_id: UUID,
        context_pack: ContextPack,
        output: AgentLLMOutput,
        artifact: ArtifactRef,
    ) -> None:
        if self.war_room_notifier is None:
            return
        self.war_room_notifier.enqueue_agent_run_card(
            thread_id=task.thread_id,
            run_id=run_id,
            context_pack=context_pack,
            output=output,
            artifact_refs=[artifact],
            memory_refs=context_pack.memory_refs,
            token_estimate=context_pack.budget_remaining.estimated_context_tokens,
            chat_id=task.feishu_chat_id,
        )


def _estimate_context_tokens(
    *,
    task_brief: str,
    artifact_refs: list[ArtifactRef],
    memory_refs: list,
    source_refs: list[str],
) -> int:
    return (
        _estimate_text_tokens(task_brief)
        + sum(item.size_tokens_estimate for item in artifact_refs)
        + sum(item.tokens_estimate for item in memory_refs)
        + sum(_estimate_text_tokens(item) for item in source_refs)
    )


def _estimate_text_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0
