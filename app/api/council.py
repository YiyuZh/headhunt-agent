from fastapi import APIRouter

from app.policy.engine import PolicyEngine
from app.schemas.council import CouncilDecision, CouncilDeliberateRequest

router = APIRouter(tags=["council"])


@router.post(
    "/council/deliberate",
    response_model=CouncilDecision,
    operation_id="deliberate_council",
)
def deliberate_council(request: CouncilDeliberateRequest) -> CouncilDecision:
    task_plan = PolicyEngine().create_task_plan(request)
    return CouncilDecision.from_task_plan(task_plan)

