from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.schemas.common import CouncilMode


class CouncilDeliberateRequest(BaseModel):
    request_text: str = Field(min_length=1)
    source: str = "manual"
    thread_id: UUID | None = None


class TaskPlan(BaseModel):
    task_id: UUID = Field(default_factory=uuid4)
    thread_id: UUID = Field(default_factory=uuid4)
    request_text: str
    task_type: str
    council_mode: CouncilMode
    mode_reason: str
    required_agents: list[str]
    optional_agents: list[str] = Field(default_factory=list)
    user_forced_full_council: bool = False
    allowed_gateways: list[str] = Field(default_factory=list)
    token_budget: int = 8000


class CouncilDecision(BaseModel):
    decision_id: UUID = Field(default_factory=uuid4)
    thread_id: UUID
    council_mode: CouncilMode
    mode_reason: str
    intent_summary: str
    risk_flags: list[str] = Field(default_factory=list)
    recommended_business_subgraph: str
    required_agents: list[str]
    optional_agents: list[str] = Field(default_factory=list)
    next_questions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @classmethod
    def from_task_plan(cls, task_plan: TaskPlan) -> "CouncilDecision":
        return cls(
            thread_id=task_plan.thread_id,
            council_mode=task_plan.council_mode,
            mode_reason=task_plan.mode_reason,
            intent_summary=task_plan.request_text[:240],
            recommended_business_subgraph=task_plan.task_type,
            required_agents=task_plan.required_agents,
            optional_agents=task_plan.optional_agents,
            confidence=0.7,
        )

