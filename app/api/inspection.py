from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.runtime.inspection import InspectionNotFoundError, InspectionService
from app.schemas.inspection import AgentRunInspectionResponse, ThreadInspectionResponse
from app.storage.database import get_session

router = APIRouter(tags=["inspection"])


def get_inspection_service(session: Session = Depends(get_session)) -> InspectionService:
    return InspectionService(session)


@router.get(
    "/threads/{thread_id}",
    response_model=ThreadInspectionResponse,
    operation_id="get_thread_inspection",
)
def get_thread_inspection(
    thread_id: UUID,
    service: InspectionService = Depends(get_inspection_service),
) -> ThreadInspectionResponse:
    try:
        return service.get_thread(thread_id)
    except InspectionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="thread not found") from exc


@router.get(
    "/runs/{run_id}",
    response_model=AgentRunInspectionResponse,
    operation_id="get_run_inspection",
)
def get_run_inspection(
    run_id: UUID,
    service: InspectionService = Depends(get_inspection_service),
) -> AgentRunInspectionResponse:
    try:
        return service.get_run(run_id)
    except InspectionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
