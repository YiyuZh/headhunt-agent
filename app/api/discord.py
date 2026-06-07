import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.auth import require_internal_admin
from app.discord.commands import (
    DiscordCommandConfigurationError,
    DiscordCommandRegistrationError,
    DiscordCommandRegistry,
)
from app.discord.interactions import (
    INTERACTION_PING,
    DiscordInteractionError,
    DiscordModelInteractionHandler,
    ephemeral_message,
    is_interaction_allowed,
)
from app.discord.security import (
    DiscordSignatureConfigurationError,
    DiscordSignatureVerificationError,
    DiscordSignatureVerifier,
)
from app.model_profiles.service import ModelProfileService
from app.schemas.discord_commands import (
    DiscordCommandRegisterRequest,
    DiscordCommandRegisterResponse,
)
from app.storage.database import get_session

router = APIRouter(prefix="/discord", tags=["discord"])


def build_model_profile_service(request: Request, session: Session) -> ModelProfileService:
    return ModelProfileService(session=session, settings=request.app.state.settings)


def build_discord_command_registry(request: Request) -> DiscordCommandRegistry:
    return DiscordCommandRegistry(settings=request.app.state.settings)


@router.post("/interactions", operation_id="receive_discord_interaction")
async def receive_discord_interaction(
    request: Request,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    raw_body = await request.body()
    settings = request.app.state.settings
    try:
        DiscordSignatureVerifier(settings).verify(raw_body=raw_body, headers=request.headers)
    except DiscordSignatureConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except DiscordSignatureVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Discord interaction body must be JSON",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Discord interaction body must be an object",
        )
    if payload.get("type") == INTERACTION_PING:
        return {"type": 1}

    if not is_interaction_allowed(
        payload,
        guild_ids=settings.discord_allowed_guild_ids,
        channel_ids=settings.discord_allowed_channel_ids,
    ):
        return ephemeral_message("当前 Discord guild/channel 未在允许列表中。")

    try:
        service = build_model_profile_service(request, session)
        return DiscordModelInteractionHandler(service=service).handle(payload)
    except DiscordInteractionError as exc:
        return ephemeral_message(str(exc))
    except RuntimeError as exc:
        return ephemeral_message(str(exc))


@router.post(
    "/commands/register",
    operation_id="register_discord_commands",
    dependencies=[Depends(require_internal_admin)],
)
def register_discord_commands(
    payload: DiscordCommandRegisterRequest,
    request: Request,
) -> DiscordCommandRegisterResponse:
    registry = build_discord_command_registry(request)
    command_names = registry.command_names()
    if payload.dry_run:
        return DiscordCommandRegisterResponse(
            dry_run=True,
            command_names=command_names,
            registered=[],
            message="Dry run only; no Discord API request was sent.",
        )
    try:
        registered = registry.register_commands(
            guild_ids=payload.guild_ids,
            include_global=payload.include_global,
        )
    except DiscordCommandConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except DiscordCommandRegistrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    return DiscordCommandRegisterResponse(
        dry_run=False,
        command_names=command_names,
        registered=registered,
        message="Discord commands registered.",
    )
