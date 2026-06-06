from app.policy.engine import FULL_COUNCIL_AGENTS, PolicyEngine
from app.schemas.common import CouncilMode
from app.schemas.council import CouncilDeliberateRequest


def test_user_can_force_full_council() -> None:
    plan = PolicyEngine().create_task_plan(
        CouncilDeliberateRequest(request_text="请用三省六部完整会审这个岗位")
    )

    assert plan.council_mode == CouncilMode.full_council
    assert plan.user_forced_full_council is True
    assert plan.required_agents == FULL_COUNCIL_AGENTS


def test_short_input_uses_triage() -> None:
    plan = PolicyEngine().create_task_plan(CouncilDeliberateRequest(request_text="招后端"))

    assert plan.council_mode == CouncilMode.triage

