from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ArtifactSummary(BaseModel):
    artifact_id: UUID
    run_id: UUID | None = None
    kind: str
    summary: str
    content_ref: str
    evidence_refs: list[dict[str, Any] | str] = Field(default_factory=list)
    source_refs: list[dict[str, Any] | str] = Field(default_factory=list)
    pii_level: str
    version: int
    size_tokens_estimate: int | None = None
    created_at: datetime | None = None


class AgentRunSummary(BaseModel):
    run_id: UUID
    node_name: str
    agent_name: str
    council_mode: str | None = None
    model_profile_id: UUID | None = None
    model_provider: str | None = None
    model_name: str | None = None
    model_owner_user_id: str | None = None
    status: str
    input_summary: str | None = None
    output_summary: str | None = None
    token_estimate: int | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None


class PendingInterruptSummary(BaseModel):
    action_id: UUID
    interrupt_id: UUID
    action_type: str
    payload_summary: str
    payload_ref: str
    idempotency_key: str
    status: str
    created_at: datetime | None = None


class ThreadInspectionResponse(BaseModel):
    thread_id: UUID
    source: str
    source_ref: str | None = None
    task_type: str
    council_mode: str | None = None
    mode_reason: str | None = None
    status: str
    state_summary: dict[str, Any] = Field(default_factory=dict)
    artifact_refs: list[ArtifactSummary] = Field(default_factory=list)
    memory_refs: list[dict[str, Any] | str] = Field(default_factory=list)
    pending_interrupts: list[PendingInterruptSummary] = Field(default_factory=list)
    recent_runs: list[AgentRunSummary] = Field(default_factory=list)


class AgentRunInspectionResponse(BaseModel):
    run_id: UUID
    thread_id: UUID
    node_name: str
    agent_name: str
    council_mode: str | None = None
    model_profile_id: UUID | None = None
    model_provider: str | None = None
    model_name: str | None = None
    model_owner_user_id: str | None = None
    status: str
    context_pack_ref: str
    input_summary: str | None = None
    output_summary: str | None = None
    memory_refs: list[dict[str, Any] | str] = Field(default_factory=list)
    artifact_refs: list[dict[str, Any] | str] = Field(default_factory=list)
    source_refs: list[dict[str, Any] | str] = Field(default_factory=list)
    token_estimate: int | None = None
    error: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
