import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.storage.models import ActionProposal, GraphThread
from app.storage.repositories import FeishuOutboxWriteRepository, PayloadRepository


class ActionExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ActionExecutionResult:
    status: str
    action_id: str
    decision: str
    outbox_payload_ref: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


class ActionExecutor:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        outbox_writer: FeishuOutboxWriteRepository | None = None,
    ):
        self.session = session
        self.settings = settings or get_settings()
        self.payload_repository = PayloadRepository(session)
        self.outbox_writer = outbox_writer or FeishuOutboxWriteRepository(session)

    def execute(self, approval: dict[str, Any]) -> ActionExecutionResult:
        action_id = _required_uuid(approval, "action_id")
        thread_id = _required_uuid(approval, "thread_id")
        decision = _required_str(approval, "decision")
        proposal = self.session.get(ActionProposal, action_id)
        if proposal is None:
            raise ActionExecutionError(f"ActionProposal not found: {action_id}")
        if proposal.thread_id != thread_id:
            raise ActionExecutionError("HumanApproval thread_id does not match ActionProposal")
        interrupt_id = approval.get("interrupt_id")
        if interrupt_id and str(proposal.interrupt_id) != str(interrupt_id):
            raise ActionExecutionError("HumanApproval interrupt_id does not match ActionProposal")
        idempotency_key = _required_str(approval, "idempotency_key")
        if proposal.idempotency_key != idempotency_key:
            raise ActionExecutionError(
                "HumanApproval idempotency_key does not match ActionProposal"
            )

        if proposal.status in {"approved", "executed", "rejected"}:
            return ActionExecutionResult(
                status=f"already_{proposal.status}",
                action_id=str(action_id),
                decision=decision,
                message="ActionProposal was already handled.",
            )

        if decision == "reject":
            self._mark_proposal(proposal, "rejected")
            return ActionExecutionResult(
                status="rejected",
                action_id=str(action_id),
                decision=decision,
                message="Business side effect was rejected by human approver.",
            )
        if decision not in {"approve", "edit"}:
            raise ActionExecutionError(f"Unsupported approval decision: {decision}")
        edited_payload = approval.get("edited_payload")
        if decision == "edit" and (
            not isinstance(edited_payload, dict) or not edited_payload
        ):
            raise ActionExecutionError("HumanApproval edit requires edited_payload")

        proposal_payload = self.payload_repository.get_json_payload(proposal.payload_ref)
        outbox_payload = self._build_bitable_outbox_payload(
            proposal=proposal,
            proposal_payload=proposal_payload,
            approval=approval,
        )
        outbox_payload_ref = self.outbox_writer.enqueue_json(
            kind="bitable_write",
            idempotency_key=f"bitable:{proposal.idempotency_key}:{proposal.id}",
            payload=outbox_payload,
            thread_id=thread_id,
        )
        self._mark_proposal(proposal, "approved")
        return ActionExecutionResult(
            status="queued",
            action_id=str(action_id),
            decision=decision,
            outbox_payload_ref=outbox_payload_ref,
            message="Approved business side effect was queued to durable Feishu outbox.",
        )

    def _build_bitable_outbox_payload(
        self,
        *,
        proposal: ActionProposal,
        proposal_payload: dict[str, Any],
        approval: dict[str, Any],
    ) -> dict[str, Any]:
        payload = proposal_payload.get("payload")
        if not isinstance(payload, dict):
            raise ActionExecutionError("ActionProposal payload is missing payload object")
        business_kind = _required_str(payload, "business_kind")
        app_token = self.settings.feishu_bitable_app_token
        table_id = self._table_id_for_business_kind(business_kind)
        if not app_token or not table_id:
            raise ActionExecutionError(
                f"Feishu Bitable app_token/table_id is not configured for {business_kind}"
            )

        record_fields = payload.get("record_fields")
        if not isinstance(record_fields, dict):
            raise ActionExecutionError("ActionProposal payload.record_fields must be an object")
        if approval.get("decision") == "edit" and isinstance(approval.get("edited_payload"), dict):
            record_fields = {
                **record_fields,
                "人工编辑": json.dumps(
                    approval["edited_payload"],
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            }
        records = [{"fields": record_fields}]
        client_token = payload.get("client_token")
        if not isinstance(client_token, str) or not client_token:
            client_token = str(uuid4())
        entity_refs = payload.get("entity_refs")
        if not isinstance(entity_refs, list):
            entity_refs = []
        raw_records = json.dumps(
            records,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return {
            "app_token": app_token,
            "table_id": table_id,
            "client_token": client_token,
            "records": records,
            "entity_refs": entity_refs,
            "action_id": str(proposal.id),
            "chunk_index": 0,
            "payload_hash": hashlib.sha256(raw_records.encode("utf-8")).hexdigest(),
        }

    def _table_id_for_business_kind(self, business_kind: str) -> str | None:
        mapping = {
            "RequisitionCalibrationDraft": self.settings.feishu_bitable_requisition_table_id,
            "TalentMapDraft": self.settings.feishu_bitable_talent_map_table_id,
            "CandidateMatchDraft": self.settings.feishu_bitable_candidate_table_id,
            "ReportDraft": self.settings.feishu_bitable_report_table_id,
            "FollowupReviewDraft": self.settings.feishu_bitable_report_table_id,
            "CaseDataDraft": self.settings.feishu_bitable_requisition_table_id,
        }
        return mapping.get(business_kind)

    def _mark_proposal(self, proposal: ActionProposal, status: str) -> None:
        self.session.execute(
            update(ActionProposal).where(ActionProposal.id == proposal.id).values(status=status)
        )
        self.session.execute(
            update(GraphThread)
            .where(GraphThread.id == proposal.thread_id)
            .values(
                status="completed",
                state_summary={
                    "last_action_id": str(proposal.id),
                    "last_action_status": status,
                    "last_action_type": proposal.action_type,
                },
            )
        )
        self.session.flush()


def _required_uuid(payload: dict[str, Any], key: str) -> UUID:
    value = payload.get(key)
    if isinstance(value, UUID):
        return value
    if isinstance(value, str) and value:
        return UUID(value)
    raise ActionExecutionError(f"HumanApproval missing {key}")


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if isinstance(value, str) and value:
        return value
    raise ActionExecutionError(f"HumanApproval missing {key}")
