from dataclasses import dataclass, field
from uuid import UUID

from app.schemas.artifacts import ArtifactRef
from app.schemas.common import CouncilMode
from app.schemas.context import BudgetRemaining, ContextPack, SOPRef
from app.schemas.memory import MemoryRef


@dataclass(frozen=True)
class ContextSnapshot:
    thread_id: UUID
    agent_name: str
    task_brief: str
    node_goal: str
    council_mode: CouncilMode
    mode_reason: str
    artifact_refs: list[ArtifactRef] = field(default_factory=list)
    memory_refs: list[MemoryRef] = field(default_factory=list)
    sop_refs: list[SOPRef] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)
    budget_remaining: BudgetRemaining = field(default_factory=BudgetRemaining)
    excluded_context_reason: list[str] = field(default_factory=list)


class ContextPackBuilder:
    def build(self, snapshot: ContextSnapshot) -> ContextPack:
        return ContextPack(
            thread_id=snapshot.thread_id,
            agent_name=snapshot.agent_name,
            task_brief=snapshot.task_brief,
            node_goal=snapshot.node_goal,
            council_mode=snapshot.council_mode,
            mode_reason=snapshot.mode_reason,
            artifact_refs=snapshot.artifact_refs,
            memory_refs=snapshot.memory_refs,
            sop_refs=snapshot.sop_refs,
            source_refs=snapshot.source_refs,
            budget_remaining=snapshot.budget_remaining,
            excluded_context_reason=snapshot.excluded_context_reason,
        )
