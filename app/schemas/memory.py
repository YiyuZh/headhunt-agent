from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.schemas.common import MemoryScope, MemoryStatus, PiiLevel


class MemoryItem(BaseModel):
    memory_id: UUID = Field(default_factory=uuid4)
    scope: MemoryScope
    owner_agent: str | None = None
    summary: str
    content_ref: str
    embedding_ref: str | None = None
    source_run_id: UUID | None = None
    tenant_id: str | None = None
    guild_id: str | None = None
    user_id: str | None = None
    project_id: str | None = None
    requisition_id: str | None = None
    candidate_id: str | None = None
    thread_id: UUID | None = None
    pii_level: PiiLevel = PiiLevel.none
    status: MemoryStatus = MemoryStatus.pending_review
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    version: int = 1
    expires_at: datetime | None = None
    metadata: dict = Field(default_factory=dict)


class MemoryRef(BaseModel):
    memory_id: UUID
    scope: MemoryScope
    summary: str
    content_ref: str
    source_run_id: UUID | None = None
    tenant_id: str | None = None
    guild_id: str | None = None
    user_id: str | None = None
    project_id: str | None = None
    requisition_id: str | None = None
    candidate_id: str | None = None
    thread_id: UUID | None = None
    relevance_score: float = Field(ge=0.0, le=1.0)
    reason: str
    tokens_estimate: int = Field(default=0, ge=0)
    pii_level: PiiLevel = PiiLevel.none
