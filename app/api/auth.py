import hmac

from fastapi import Depends, Header, HTTPException, Request, status
from pydantic import SecretStr

from app.core.config import Settings

INTERNAL_ADMIN_HEADER = "X-Internal-Admin-Key"


def require_internal_admin(
    request: Request,
    x_internal_admin_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> None:
    settings: Settings = request.app.state.settings
    expected = _secret_value(settings.internal_admin_api_key)
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="INTERNAL_ADMIN_API_KEY is required for internal control APIs",
        )

    provided = x_internal_admin_key or _bearer_token(authorization)
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="internal admin authorization required",
        )


def internal_admin_dependency() -> Depends:
    return Depends(require_internal_admin)


def _secret_value(value: SecretStr | None) -> str:
    if value is None:
        return ""
    return value.get_secret_value()


def _bearer_token(value: str | None) -> str | None:
    if not value:
        return None
    prefix = "Bearer "
    if not value.startswith(prefix):
        return None
    token = value[len(prefix) :].strip()
    return token or None
