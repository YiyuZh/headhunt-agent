from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.feishu.callbacks import (
    FeishuCallbackPayloadError,
    VerifiedFeishuCallback,
    build_event_dedupe_key,
    build_event_idempotency_key,
    parse_card_action_command,
)
from app.runtime.outbox import feishu_payload_to_initial_state
from app.storage.repositories import (
    DuplicateCardActionError,
    DuplicateEventError,
    FeishuCardActionRepository,
    FeishuEventRepository,
    InvalidHumanApprovalError,
    PayloadRepository,
)


@dataclass(frozen=True)
class FeishuEnqueueResult:
    status: str
    idempotency_key: str
    payload_ref: str


class FeishuCallbackService:
    def __init__(self, session: Session):
        self.session = session

    def enqueue_event(self, callback: VerifiedFeishuCallback) -> FeishuEnqueueResult:
        payload_ref = build_payload_ref(callback)
        idempotency_key = build_event_idempotency_key(callback)
        initial_state = feishu_payload_to_initial_state(callback.payload)

        try:
            with self.session.begin():
                PayloadRepository(self.session).store_json_payload(
                    content_ref=payload_ref,
                    payload=callback.payload,
                    raw_text=callback.raw_body.decode("utf-8"),
                    sha256=callback.payload_hash,
                )
                FeishuEventRepository(self.session).record_event_and_enqueue(
                    event_id=callback.event_id or callback.payload_hash,
                    event_type=callback.event_type,
                    tenant_key=callback.tenant_key,
                    app_id=callback.app_id,
                    message_id=callback.message_id,
                    dedupe_key=build_event_dedupe_key(callback),
                    idempotency_key=idempotency_key,
                    payload_hash=callback.payload_hash,
                    payload_ref=payload_ref,
                    outbox_kind="graph_dispatch",
                    thread_id=UUID(initial_state["thread_id"]),
                )
        except DuplicateEventError:
            return FeishuEnqueueResult(
                status="duplicate",
                idempotency_key=idempotency_key,
                payload_ref=payload_ref,
            )

        return FeishuEnqueueResult(
            status="queued",
            idempotency_key=idempotency_key,
            payload_ref=payload_ref,
        )

    def enqueue_card_action(self, callback: VerifiedFeishuCallback) -> FeishuEnqueueResult:
        payload_ref = build_payload_ref(callback)
        command = parse_card_action_command(callback, payload_ref=payload_ref)
        approver = build_approver(callback.payload)

        try:
            with self.session.begin():
                PayloadRepository(self.session).store_json_payload(
                    content_ref=payload_ref,
                    payload=callback.payload,
                    raw_text=callback.raw_body.decode("utf-8"),
                    sha256=callback.payload_hash,
                )
                FeishuCardActionRepository(self.session).record_action_and_enqueue_resume(
                    event_id=command.event_id,
                    thread_id=command.thread_id,
                    action_id=command.action_id,
                    interrupt_id=command.interrupt_id,
                    idempotency_key=command.idempotency_key,
                    open_message_id=command.open_message_id,
                    open_chat_id=command.open_chat_id,
                    card_update_token_ref=command.card_update_token_ref,
                    operator_open_id=command.operator_open_id,
                    decision=command.decision,
                    edited_payload_ref=payload_ref if command.edited_payload else None,
                    payload_ref=payload_ref,
                    approver=approver,
                )
        except DuplicateCardActionError:
            return FeishuEnqueueResult(
                status="duplicate",
                idempotency_key=command.idempotency_key,
                payload_ref=payload_ref,
            )
        except InvalidHumanApprovalError as exc:
            raise FeishuCallbackPayloadError(str(exc)) from exc

        return FeishuEnqueueResult(
            status="queued",
            idempotency_key=command.idempotency_key,
            payload_ref=payload_ref,
        )


def build_payload_ref(callback: VerifiedFeishuCallback) -> str:
    return f"artifact://feishu-callback/{callback.payload_hash}"


def build_approver(payload: dict[str, Any]) -> dict[str, Any]:
    event = payload.get("event")
    if not isinstance(event, dict):
        return {"source": "feishu"}

    operator = event.get("operator")
    if not isinstance(operator, dict):
        return {"source": "feishu"}

    return {
        "source": "feishu",
        "open_id": operator.get("open_id"),
        "union_id": operator.get("union_id"),
        "user_id": operator.get("user_id"),
    }
