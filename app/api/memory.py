from dataclasses import asdict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.memory.retention import MemoryRetentionService
from app.schemas.memory_retention import (
    MemoryRetentionRunRequest,
    MemoryRetentionRunResponse,
)
from app.storage.database import get_session

router = APIRouter(prefix="/memory", tags=["memory"])


def get_memory_retention_service(
    session: Session = Depends(get_session),
) -> MemoryRetentionService:
    return MemoryRetentionService(session)


@router.post(
    "/retention/run",
    response_model=MemoryRetentionRunResponse,
    operation_id="run_memory_retention",
)
def run_memory_retention(
    request: MemoryRetentionRunRequest | None = None,
    session: Session = Depends(get_session),
) -> MemoryRetentionRunResponse:
    resolved_request = request or MemoryRetentionRunRequest()
    with session.begin():
        summary = MemoryRetentionService(session).run(
            now=resolved_request.now,
            dry_run=resolved_request.dry_run,
        )
    return MemoryRetentionRunResponse(**asdict(summary))
