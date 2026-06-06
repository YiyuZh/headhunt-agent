from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator


class ApprovalDecision(StrEnum):
    approve = "approve"
    edit = "edit"
    reject = "reject"


class ActionProposal(BaseModel):
    action_id: UUID = Field(default_factory=uuid4)
    interrupt_id: UUID | None = None
    idempotency_key: str
    action: str
    thread_id: UUID
    artifact_refs: list[str] = Field(default_factory=list)
    preview: dict = Field(default_factory=dict)
    risk_level: str = "medium"


class HumanApproval(BaseModel):
    interrupt_id: UUID
    action_id: UUID
    thread_id: UUID
    approver: dict = Field(default_factory=dict)
    decision: ApprovalDecision
    edited_payload: dict | None = None
    idempotency_key: str
    decided_at: datetime = Field(default_factory=datetime.utcnow)


class ApprovalDetailResponse(BaseModel):
    action_id: UUID
    interrupt_id: UUID
    thread_id: UUID
    action_type: str
    payload_summary: str
    payload_ref: str
    idempotency_key: str
    status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    can_decide: bool


class ApprovalDecisionRequest(BaseModel):
    decision: ApprovalDecision
    approver: dict[str, Any] = Field(default_factory=dict)
    edited_payload: dict[str, Any] | None = None

    @model_validator(mode="after")
    def require_edited_payload_for_edit(self) -> "ApprovalDecisionRequest":
        if self.decision == ApprovalDecision.edit and not self.edited_payload:
            raise ValueError("edited_payload is required when decision is edit")
        return self


class ApprovalDecisionResponse(BaseModel):
    status: Literal[
        "queued",
        "duplicate",
        "already_approved",
        "already_rejected",
        "already_executed",
    ]
    action_id: UUID
    interrupt_id: UUID
    thread_id: UUID
    decision: ApprovalDecision
    idempotency_key: str
    outbox_payload_ref: str | None = None
    next_actions: list[str] = Field(default_factory=list)
