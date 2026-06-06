from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.schemas.inspection import (
    AgentRunInspectionResponse,
    AgentRunSummary,
    ArtifactSummary,
    PendingInterruptSummary,
    ThreadInspectionResponse,
)
from app.storage.models import ActionProposal, AgentArtifact, AgentRun, GraphThread


class InspectionNotFoundError(RuntimeError):
    pass


class InspectionService:
    def __init__(
        self,
        session: Session,
        *,
        recent_run_limit: int = 10,
        artifact_limit: int = 20,
    ):
        self.session = session
        self.recent_run_limit = recent_run_limit
        self.artifact_limit = artifact_limit

    def get_thread(self, thread_id: UUID) -> ThreadInspectionResponse:
        thread = self.session.get(GraphThread, thread_id)
        if thread is None:
            raise InspectionNotFoundError(f"thread {thread_id} not found")

        recent_runs = self._recent_runs(thread_id)
        artifacts = self._artifacts(thread_id)
        pending_interrupts = self._pending_interrupts(thread_id)

        return ThreadInspectionResponse(
            thread_id=thread.id,
            source=thread.source,
            source_ref=thread.source_ref,
            task_type=thread.task_type,
            council_mode=thread.council_mode,
            mode_reason=thread.mode_reason,
            status=thread.status,
            state_summary=thread.state_summary or {},
            artifact_refs=[_artifact_summary(artifact) for artifact in artifacts],
            memory_refs=_dedupe_memory_refs(recent_runs),
            pending_interrupts=[
                _pending_interrupt_summary(action) for action in pending_interrupts
            ],
            recent_runs=[_agent_run_summary(run) for run in recent_runs],
        )

    def get_run(self, run_id: UUID) -> AgentRunInspectionResponse:
        run = self.session.get(AgentRun, run_id)
        if run is None:
            raise InspectionNotFoundError(f"run {run_id} not found")

        return AgentRunInspectionResponse(
            run_id=run.id,
            thread_id=run.thread_id,
            node_name=run.node_name,
            agent_name=run.agent_name,
            council_mode=run.council_mode,
            status=run.status,
            context_pack_ref=run.context_pack_ref,
            input_summary=run.input_summary,
            output_summary=run.output_summary,
            memory_refs=run.memory_refs or [],
            artifact_refs=run.artifact_refs or [],
            source_refs=run.source_refs or [],
            token_estimate=run.token_estimate,
            error=run.error,
            started_at=run.started_at,
            ended_at=run.ended_at,
        )

    def _recent_runs(self, thread_id: UUID) -> list[AgentRun]:
        return list(
            self.session.execute(
                select(AgentRun)
                .where(AgentRun.thread_id == thread_id)
                .order_by(desc(AgentRun.started_at))
                .limit(self.recent_run_limit)
            )
            .scalars()
            .all()
        )

    def _artifacts(self, thread_id: UUID) -> list[AgentArtifact]:
        return list(
            self.session.execute(
                select(AgentArtifact)
                .where(AgentArtifact.thread_id == thread_id)
                .order_by(desc(AgentArtifact.created_at))
                .limit(self.artifact_limit)
            )
            .scalars()
            .all()
        )

    def _pending_interrupts(self, thread_id: UUID) -> list[ActionProposal]:
        return list(
            self.session.execute(
                select(ActionProposal)
                .where(
                    ActionProposal.thread_id == thread_id,
                    ActionProposal.status == "pending",
                )
                .order_by(desc(ActionProposal.created_at))
            )
            .scalars()
            .all()
        )


def _agent_run_summary(run: AgentRun) -> AgentRunSummary:
    return AgentRunSummary(
        run_id=run.id,
        node_name=run.node_name,
        agent_name=run.agent_name,
        council_mode=run.council_mode,
        status=run.status,
        input_summary=run.input_summary,
        output_summary=run.output_summary,
        token_estimate=run.token_estimate,
        started_at=run.started_at,
        ended_at=run.ended_at,
    )


def _artifact_summary(artifact: AgentArtifact) -> ArtifactSummary:
    return ArtifactSummary(
        artifact_id=artifact.id,
        run_id=artifact.run_id,
        kind=artifact.kind,
        summary=artifact.summary,
        content_ref=artifact.content_ref,
        evidence_refs=artifact.evidence_refs or [],
        source_refs=artifact.source_refs or [],
        pii_level=artifact.pii_level,
        version=artifact.version,
        size_tokens_estimate=artifact.size_tokens_estimate,
        created_at=artifact.created_at,
    )


def _pending_interrupt_summary(action: ActionProposal) -> PendingInterruptSummary:
    return PendingInterruptSummary(
        action_id=action.id,
        interrupt_id=action.interrupt_id,
        action_type=action.action_type,
        payload_summary=action.payload_summary,
        payload_ref=action.payload_ref,
        idempotency_key=action.idempotency_key,
        status=action.status,
        created_at=action.created_at,
    )


def _dedupe_memory_refs(runs: list[AgentRun]) -> list[dict | str]:
    memory_refs: list[dict | str] = []
    seen: set[str] = set()
    for run in runs:
        for memory_ref in run.memory_refs or []:
            key = _memory_ref_key(memory_ref)
            if key in seen:
                continue
            seen.add(key)
            memory_refs.append(memory_ref)
    return memory_refs


def _memory_ref_key(memory_ref: dict | str) -> str:
    if isinstance(memory_ref, dict):
        return str(memory_ref.get("memory_id") or memory_ref.get("content_ref") or memory_ref)
    return memory_ref
