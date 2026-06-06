from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.runtime.approvals import (
    ApprovalConflictError,
    ApprovalNotFoundError,
    ApprovalService,
)
from app.schemas.approval import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalDetailResponse,
)
from app.storage.database import get_session

router = APIRouter(prefix="/approvals", tags=["approvals"])


def get_approval_service(session: Session = Depends(get_session)) -> ApprovalService:
    return ApprovalService(session)


@router.get(
    "/{interrupt_id}",
    response_model=ApprovalDetailResponse,
    operation_id="get_approval_detail",
)
def get_approval_detail(
    interrupt_id: UUID,
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalDetailResponse:
    try:
        return service.get_approval(interrupt_id)
    except ApprovalNotFoundError as exc:
        raise HTTPException(status_code=404, detail="approval not found") from exc


@router.post(
    "/{interrupt_id}/decision",
    response_model=ApprovalDecisionResponse,
    operation_id="submit_approval_decision",
)
def submit_approval_decision(
    interrupt_id: UUID,
    request: ApprovalDecisionRequest,
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalDecisionResponse:
    try:
        return service.submit_decision(interrupt_id, request)
    except ApprovalNotFoundError as exc:
        raise HTTPException(status_code=404, detail="approval not found") from exc
    except ApprovalConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
