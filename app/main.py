from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    expose_docs = resolved_settings.app_env.lower() not in {"docker", "prod", "production"}
    app = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
        description="AI headhunter war room runtime API.",
        docs_url="/docs" if expose_docs else None,
        redoc_url="/redoc" if expose_docs else None,
        openapi_url="/openapi.json" if expose_docs else None,
    )
    app.state.settings = resolved_settings
    app.include_router(api_router)
    return app


app = create_app()
