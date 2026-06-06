from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.runtime.task_authorization import (
    TaskAuthorizationConflictError,
    TaskAuthorizationService,
)
from app.schemas.tasks import TaskAuthorizeRequest, TaskAuthorizeResponse
from app.storage.database import get_session

router = APIRouter(prefix="/tasks", tags=["tasks"])


def get_task_authorization_service(
    session: Session = Depends(get_session),
) -> TaskAuthorizationService:
    return TaskAuthorizationService(session)


@router.post(
    "/authorize",
    response_model=TaskAuthorizeResponse,
    operation_id="authorize_task",
)
def authorize_task(
    request: TaskAuthorizeRequest,
    service: TaskAuthorizationService = Depends(get_task_authorization_service),
) -> TaskAuthorizeResponse:
    try:
        return service.authorize(request)
    except TaskAuthorizationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
