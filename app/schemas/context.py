from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.artifacts import ArtifactRef
from app.schemas.common import CouncilMode
from app.schemas.memory import MemoryRef

FORBIDDEN_CONTEXT_KEYS = {
    "messages",
    "chat_history",
    "recruitment_state",
    "node_history",
    "agent_runs",
    "all_artifacts",
    "all_memories",
}


class ContextPack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context_pack_id: UUID = Field(default_factory=uuid4)
    thread_id: UUID
    agent_name: str
    task_brief: str
    node_goal: str
    council_mode: CouncilMode
    mode_reason: str
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    memory_refs: list[MemoryRef] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    budget_remaining: "BudgetRemaining" = Field(default_factory=lambda: BudgetRemaining())
    excluded_context_reason: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def reject_forbidden_context_keys(cls, data):
        if isinstance(data, dict):
            forbidden = sorted(FORBIDDEN_CONTEXT_KEYS.intersection(data))
            if forbidden:
                raise ValueError(f"ContextPack cannot include forbidden keys: {forbidden}")
        return data


class BudgetRemaining(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_context_tokens: int = Field(default=0, ge=0)
    estimated_context_tokens: int = Field(default=0, ge=0)
    max_tool_calls: int | None = Field(default=None, ge=0)
    used_tool_calls: int | None = Field(default=None, ge=0)
