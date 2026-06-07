from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.artifacts import ArtifactRef
from app.schemas.common import CouncilMode, MemoryScope, PiiLevel
from app.schemas.context import ContextPack
from app.schemas.memory import MemoryRef


class AgentPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_name: str
    allowed_artifact_types_read: list[str] = Field(default_factory=list)
    allowed_artifact_types_write: list[str] = Field(default_factory=list)
    allowed_memory_scopes: list[MemoryScope] = Field(default_factory=lambda: [MemoryScope.run])
    max_memory_items: int = Field(default=3, ge=0)
    max_context_tokens: int = Field(default=3000, ge=0)
    max_output_tokens: int = Field(default=1200, ge=0)
    can_read_memory_content: bool = False
    can_read_artifact_content: bool = False
    pii_access_level: PiiLevel = PiiLevel.none
    allowed_side_effects: list[str] = Field(default_factory=list)
    model_profile_id: UUID | None = None
    policy_version: str = "v1"


class AgentTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: UUID = Field(default_factory=uuid4)
    thread_id: UUID
    node_name: str
    agent_name: str
    node_goal: str
    task_brief: str
    council_mode: CouncilMode
    mode_reason: str
    output_artifact_type: str
    source: str = "runtime"
    source_ref: str | None = None
    task_type: str = "unknown"
    feishu_chat_id: str | None = None
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    model_profile_id: UUID | None = None
    model_owner_user_id: str | None = None
    model_guild_id: str | None = None
    model_tenant_id: str | None = None
    embedding_profile_id: UUID | None = None
    policy: AgentPolicy

    @model_validator(mode="after")
    def policy_matches_agent(self) -> "AgentTask":
        if self.policy.agent_name != self.agent_name:
            raise ValueError("AgentTask policy.agent_name must match agent_name")
        if self.output_artifact_type not in self.policy.allowed_artifact_types_write:
            raise ValueError("AgentTask output_artifact_type must be writable by policy")
        return self


class AgentLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1)
    artifact_payload: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    pii_level: PiiLevel = PiiLevel.none
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    requires_human_confirmation: bool = False
    next_actions: list[str] = Field(default_factory=list)


class AgentRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    context_pack: ContextPack
    artifact: ArtifactRef
    memory_refs: list[MemoryRef] = Field(default_factory=list)
    token_estimate: int = 0
