from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import CouncilMode


class TaskAuthorizeRequest(BaseModel):
    request_text: str = Field(min_length=1)
    source: str = Field(default="api", min_length=1)
    source_ref: str | None = None
    thread_id: UUID | None = None
    model_profile_id: UUID | None = None
    model_owner_user_id: str | None = None
    model_guild_id: str | None = None
    model_tenant_id: str | None = None
    embedding_profile_id: UUID | None = None
    approved: bool = False
    approver: dict[str, Any] = Field(default_factory=dict)


class TaskAuthorizeResponse(BaseModel):
    status: Literal["queued", "rejected"]
    task_id: UUID
    thread_id: UUID
    source_ref: str
    task_type: str
    council_mode: CouncilMode
    mode_reason: str
    required_agents: list[str]
    optional_agents: list[str] = Field(default_factory=list)
    user_forced_full_council: bool = False
    model_profile_id: UUID | None = None
    idempotency_key: str | None = None
    outbox_payload_ref: str | None = None
    next_actions: list[str] = Field(default_factory=list)
