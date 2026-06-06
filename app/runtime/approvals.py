from typing import Any
from uuid import UUID

from sqlalchemy import desc, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.schemas.approval import (
    ApprovalDecision,
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalDetailResponse,
)
from app.storage.models import ActionProposal, HumanApproval
from app.storage.repositories import (
    FeishuOutboxWriteRepository,
    OutboxPayloadConflictError,
    PayloadRepository,
)


class ApprovalNotFoundError(RuntimeError):
    pass


class ApprovalConflictError(RuntimeError):
    pass


class ApprovalService:
    def __init__(
        self,
        session: Session,
        *,
        outbox_writer: FeishuOutboxWriteRepository | None = None,
    ):
        self.session = session
        self.payload_repository = PayloadRepository(session)
        self.outbox_writer = outbox_writer or FeishuOutboxWriteRepository(session)

    def get_approval(self, interrupt_id: UUID) -> ApprovalDetailResponse:
        proposal = self._proposal_for_interrupt(interrupt_id)
        if proposal is None:
            raise ApprovalNotFoundError(f"approval interrupt {interrupt_id} not found")
        return _detail_response(proposal)

    def submit_decision(
        self,
        interrupt_id: UUID,
        request: ApprovalDecisionRequest,
    ) -> ApprovalDecisionResponse:
        try:
            with self.session.begin():
                proposal = self._proposal_for_interrupt(interrupt_id)
                if proposal is None:
                    raise ApprovalNotFoundError(
                        f"approval interrupt {interrupt_id} not found"
                    )
                if proposal.status != "pending":
                    return _already_handled_response(proposal, request.decision)

                approval_payload = _build_resume_payload(
                    proposal=proposal,
                    request=request,
                )
                existing_approval = self._existing_human_approval(
                    proposal.idempotency_key
                )
                if existing_approval is not None:
                    if existing_approval.decision != request.decision.value:
                        raise ApprovalConflictError(
                            "ActionProposal already has a different HumanApproval decision"
                        )
                    if request.decision == ApprovalDecision.edit:
                        self._ensure_existing_edit_payload_matches(
                            existing_approval=existing_approval,
                            requested_edited_payload=request.edited_payload,
                        )
                    return _duplicate_response(proposal, request.decision)

                outbox_payload_ref = self.outbox_writer.enqueue_json(
                    kind="resume",
                    idempotency_key=f"resume:{proposal.idempotency_key}",
                    payload=approval_payload,
                    thread_id=proposal.thread_id,
                )
                self.session.execute(
                    insert(HumanApproval).values(
                        interrupt_id=proposal.interrupt_id,
                        action_id=proposal.id,
                        thread_id=proposal.thread_id,
                        approver=approval_payload["human_approval"]["approver"],
                        decision=request.decision.value,
                        edited_payload_ref=(
                            outbox_payload_ref
                            if request.decision == ApprovalDecision.edit
                            else None
                        ),
                        idempotency_key=proposal.idempotency_key,
                    )
                )
        except OutboxPayloadConflictError as exc:
            raise ApprovalConflictError(str(exc)) from exc
        except IntegrityError as exc:
            raise ApprovalConflictError(
                "ActionProposal approval was submitted concurrently"
            ) from exc

        return ApprovalDecisionResponse(
            status="queued",
            action_id=proposal.id,
            interrupt_id=proposal.interrupt_id,
            thread_id=proposal.thread_id,
            decision=request.decision,
            idempotency_key=proposal.idempotency_key,
            outbox_payload_ref=outbox_payload_ref,
            next_actions=[
                "已写入 durable resume outbox。",
                "启动 lietou-outbox-worker 后会用 Command(resume=HumanApproval) 继续 graph。",
                "本接口不会直接执行业务副作用；业务写入仍由 ActionExecutor 二次校验后排队。",
            ],
        )

    def _proposal_for_interrupt(self, interrupt_id: UUID) -> ActionProposal | None:
        return (
            self.session.execute(
                select(ActionProposal)
                .where(ActionProposal.interrupt_id == interrupt_id)
                .order_by(desc(ActionProposal.created_at))
                .limit(1)
            )
            .scalars()
            .first()
        )

    def _ensure_existing_edit_payload_matches(
        self,
        *,
        existing_approval: HumanApproval,
        requested_edited_payload: dict[str, Any] | None,
    ) -> None:
        existing_payload = self._existing_edit_payload(existing_approval)
        if existing_payload != requested_edited_payload:
            raise ApprovalConflictError(
                "ActionProposal already has an edit HumanApproval with a different "
                "edited_payload"
            )

    def _existing_edit_payload(
        self,
        existing_approval: HumanApproval,
    ) -> dict[str, Any] | None:
        if not existing_approval.edited_payload_ref:
            return None
        try:
            payload = self.payload_repository.get_json_payload(
                existing_approval.edited_payload_ref
            )
        except KeyError as exc:
            raise ApprovalConflictError(
                "Existing edit HumanApproval payload is unavailable for comparison"
            ) from exc
        return _extract_edited_payload(payload)

    def _existing_human_approval(self, idempotency_key: str) -> HumanApproval | None:
        return (
            self.session.execute(
                select(HumanApproval)
                .where(HumanApproval.idempotency_key == idempotency_key)
                .limit(1)
            )
            .scalars()
            .first()
        )


def _detail_response(proposal: ActionProposal) -> ApprovalDetailResponse:
    return ApprovalDetailResponse(
        action_id=proposal.id,
        interrupt_id=proposal.interrupt_id,
        thread_id=proposal.thread_id,
        action_type=proposal.action_type,
        payload_summary=proposal.payload_summary,
        payload_ref=proposal.payload_ref,
        idempotency_key=proposal.idempotency_key,
        status=proposal.status,
        created_at=proposal.created_at,
        updated_at=proposal.updated_at,
        can_decide=proposal.status == "pending",
    )


def _build_resume_payload(
    *,
    proposal: ActionProposal,
    request: ApprovalDecisionRequest,
) -> dict[str, Any]:
    approver = dict(request.approver)
    approver.setdefault("source", "internal")
    human_approval: dict[str, Any] = {
        "thread_id": str(proposal.thread_id),
        "action_id": str(proposal.id),
        "interrupt_id": str(proposal.interrupt_id),
        "idempotency_key": proposal.idempotency_key,
        "decision": request.decision.value,
        "approver": approver,
        "payload_ref": proposal.payload_ref,
    }
    if request.edited_payload:
        human_approval["edited_payload"] = request.edited_payload
    return {"human_approval": human_approval}


def _extract_edited_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    direct = payload.get("edited_payload")
    if isinstance(direct, dict):
        return direct

    human_approval = payload.get("human_approval")
    if isinstance(human_approval, dict) and isinstance(
        human_approval.get("edited_payload"),
        dict,
    ):
        return human_approval["edited_payload"]

    event = payload.get("event")
    if not isinstance(event, dict):
        return None
    action = event.get("action")
    if not isinstance(action, dict):
        return None
    value = _coerce_action_value(action.get("value"))
    edited_payload = value.get("edited_payload") or value.get("form_value")
    return edited_payload if isinstance(edited_payload, dict) else None


def _coerce_action_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        import json

        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _already_handled_response(
    proposal: ActionProposal,
    requested_decision: ApprovalDecision,
) -> ApprovalDecisionResponse:
    status_map = {
        "approved": "already_approved",
        "rejected": "already_rejected",
        "executed": "already_executed",
    }
    return ApprovalDecisionResponse(
        status=status_map.get(proposal.status, "already_executed"),
        action_id=proposal.id,
        interrupt_id=proposal.interrupt_id,
        thread_id=proposal.thread_id,
        decision=requested_decision,
        idempotency_key=proposal.idempotency_key,
        next_actions=[
            f"ActionProposal 当前状态为 {proposal.status}，未重复写入 resume outbox。"
        ],
    )


def _duplicate_response(
    proposal: ActionProposal,
    requested_decision: ApprovalDecision,
) -> ApprovalDecisionResponse:
    return ApprovalDecisionResponse(
        status="duplicate",
        action_id=proposal.id,
        interrupt_id=proposal.interrupt_id,
        thread_id=proposal.thread_id,
        decision=requested_decision,
        idempotency_key=proposal.idempotency_key,
        next_actions=["该 ActionProposal 已收到相同 HumanApproval，未重复写入。"],
    )
