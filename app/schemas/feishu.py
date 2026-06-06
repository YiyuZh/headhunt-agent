from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class FeishuCallbackType(StrEnum):
    event = "event"
    card_action = "card_action"


class FeishuEnvelopeStatus(StrEnum):
    received = "received"
    duplicate = "duplicate"
    queued = "queued"
    claimed = "claimed"
    running = "running"
    succeeded = "succeeded"
    retrying = "retrying"
    failed = "failed"
    dead_letter = "dead_letter"


class FeishuEventEnvelope(BaseModel):
    event_log_id: UUID = Field(default_factory=uuid4)
    event_id: str | None = None
    message_id: str | None = None
    dedupe_key: str
    callback_type: FeishuCallbackType
    event_type: str
    tenant_key: str | None = None
    chat_id: str | None = None
    open_id: str | None = None
    thread_id: UUID | None = None
    idempotency_key: str
    raw_payload_ref: str
    status: FeishuEnvelopeStatus = FeishuEnvelopeStatus.received
    received_at: datetime = Field(default_factory=datetime.utcnow)
    ack_at: datetime | None = None
    dispatched_at: datetime | None = None
    finished_at: datetime | None = None
    claim_count: int = 0
    next_retry_at: datetime | None = None
    error: dict | None = None


class FeishuUrlChallengeResponse(BaseModel):
    challenge: str


class FeishuEventAck(BaseModel):
    status: str
    mode: str


class FeishuCardActionAck(BaseModel):
    status: str
    idempotency_key: str | None = None
    toast: dict

