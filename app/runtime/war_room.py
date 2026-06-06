import hashlib
import json
from uuid import UUID

from app.feishu.cards import build_agent_run_card, build_approval_card, build_question_card
from app.schemas.agent import AgentLLMOutput
from app.schemas.artifacts import ArtifactRef
from app.schemas.context import ContextPack
from app.schemas.memory import MemoryRef
from app.storage.repositories import FeishuOutboxWriteRepository


class WarRoomNotifier:
    def __init__(
        self,
        *,
        outbox_writer: FeishuOutboxWriteRepository,
        default_chat_id: str | None = None,
    ):
        self.outbox_writer = outbox_writer
        self.default_chat_id = default_chat_id

    def enqueue_agent_run_card(
        self,
        *,
        thread_id: UUID,
        run_id: UUID,
        context_pack: ContextPack,
        output: AgentLLMOutput,
        artifact_refs: list[ArtifactRef],
        memory_refs: list[MemoryRef],
        token_estimate: int,
        chat_id: str | None = None,
    ) -> str | None:
        resolved_chat_id = chat_id or self.default_chat_id
        if not resolved_chat_id:
            return None
        card = build_agent_run_card(
            thread_id=thread_id,
            title=f"{context_pack.agent_name} 运行结果",
            context_pack=context_pack,
            output_summary=output.summary,
            artifact_refs=artifact_refs,
            memory_refs=memory_refs,
            token_estimate=token_estimate,
            requires_human_confirmation=output.requires_human_confirmation,
        )
        return self.outbox_writer.enqueue_json(
            kind="card_send",
            idempotency_key=f"{thread_id}:agent_run:{run_id}:card",
            thread_id=thread_id,
            payload={"chat_id": resolved_chat_id, "card": card},
        )

    def enqueue_question_card(
        self,
        *,
        thread_id: UUID,
        chat_id: str | None,
        council_mode: str,
        mode_reason: str,
        questions: list[str],
    ) -> str | None:
        resolved_chat_id = chat_id or self.default_chat_id
        if not resolved_chat_id:
            return None
        card = build_question_card(
            thread_id=thread_id,
            council_mode=council_mode,
            mode_reason=mode_reason,
            questions=questions,
        )
        return self.outbox_writer.enqueue_json(
            kind="card_send",
            idempotency_key=f"{thread_id}:questions:{_stable_list_key(questions)}",
            thread_id=thread_id,
            payload={"chat_id": resolved_chat_id, "card": card},
        )

    def enqueue_approval_card(
        self,
        *,
        thread_id: UUID,
        chat_id: str | None,
        interrupt_id: UUID,
        action_id: UUID,
        idempotency_key: str,
        payload_ref: str | None = None,
        action_type: str,
        payload_summary: str,
        council_mode: str,
        mode_reason: str,
        artifact_refs: list[ArtifactRef],
    ) -> str | None:
        resolved_chat_id = chat_id or self.default_chat_id
        if not resolved_chat_id:
            return None
        card = build_approval_card(
            thread_id=thread_id,
            interrupt_id=interrupt_id,
            action_id=action_id,
            idempotency_key=idempotency_key,
            payload_ref=payload_ref,
            action_type=action_type,
            payload_summary=payload_summary,
            council_mode=council_mode,
            mode_reason=mode_reason,
            artifact_refs=artifact_refs,
        )
        return self.outbox_writer.enqueue_json(
            kind="card_send",
            idempotency_key=f"{thread_id}:approval:{action_id}:card",
            thread_id=thread_id,
            payload={"chat_id": resolved_chat_id, "card": card},
        )


def _stable_list_key(values: list[str]) -> str:
    return hashlib.sha256(
        json.dumps(values, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
