from fastapi import APIRouter, Depends

from app.api import approvals, council, discord, feishu, health, inspection, tasks
from app.api.auth import require_internal_admin

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(council.router, dependencies=[Depends(require_internal_admin)])
api_router.include_router(discord.router)
api_router.include_router(feishu.router)
api_router.include_router(tasks.router, dependencies=[Depends(require_internal_admin)])
api_router.include_router(inspection.router, dependencies=[Depends(require_internal_admin)])
api_router.include_router(approvals.router, dependencies=[Depends(require_internal_admin)])
