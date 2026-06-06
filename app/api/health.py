from fastapi import APIRouter, Depends, Request

from app.api.auth import require_internal_admin
from app.core.config import Settings
from app.core.readiness import build_readiness_report
from app.schemas.system import HealthResponse, ReadinessResponse, VersionResponse

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse, operation_id="get_health")
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    operation_id="get_readiness",
    dependencies=[Depends(require_internal_admin)],
)
def ready(request: Request) -> ReadinessResponse:
    settings: Settings = request.app.state.settings
    report = build_readiness_report(settings)
    return ReadinessResponse(
        status=report.status,
        checks=report.checks,
        details=report.details,
        missing_required=report.missing_required,
        next_steps=report.next_steps,
    )


@router.get(
    "/version",
    response_model=VersionResponse,
    operation_id="get_version",
    dependencies=[Depends(require_internal_admin)],
)
def version(request: Request) -> VersionResponse:
    settings: Settings = request.app.state.settings
    return VersionResponse(app_name=settings.app_name, app_version=settings.app_version)
