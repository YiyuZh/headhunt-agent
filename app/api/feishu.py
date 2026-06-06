from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.feishu.callbacks import (
    FeishuCallbackConfigurationError,
    FeishuCallbackPayloadError,
    FeishuCallbackVerificationError,
    FeishuCallbackVerifier,
)
from app.feishu.service import FeishuCallbackService
from app.schemas.feishu import (
    FeishuEventAck,
    FeishuUrlChallengeResponse,
)
from app.storage.database import get_session

router = APIRouter(prefix="/feishu", tags=["feishu"])


def get_feishu_callback_service(
    session: Session = Depends(get_session),
) -> FeishuCallbackService:
    return FeishuCallbackService(session)


@router.post(
    "/events",
    operation_id="receive_feishu_event",
)
async def receive_feishu_event(
    request: Request,
    settings: Settings = Depends(get_settings),
    service: FeishuCallbackService = Depends(get_feishu_callback_service),
) -> FeishuUrlChallengeResponse | FeishuEventAck:
    callback = _verify_event_request(request, await request.body(), settings)
    if callback.is_challenge and callback.challenge:
        return FeishuUrlChallengeResponse(challenge=callback.challenge)

    if callback.event_type == "card.action.trigger":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="card.action.trigger callbacks must use /feishu/card-actions",
        )

    result = service.enqueue_event(callback)
    return FeishuEventAck(status=result.status, mode="ack")


@router.post(
    "/card-actions",
    operation_id="receive_feishu_card_action",
)
async def receive_feishu_card_action(
    request: Request,
    settings: Settings = Depends(get_settings),
    service: FeishuCallbackService = Depends(get_feishu_callback_service),
) -> dict[str, Any] | FeishuUrlChallengeResponse:
    callback = _verify_card_request(request, await request.body(), settings)
    if callback.is_challenge and callback.challenge:
        return FeishuUrlChallengeResponse(challenge=callback.challenge)

    try:
        result = service.enqueue_card_action(callback)
    except FeishuCallbackPayloadError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    content = "已收到，正在继续任务" if result.status == "queued" else "已处理过该操作"
    return {"toast": {"type": "info", "content": content}}


def _verify_event_request(
    request: Request,
    raw_body: bytes,
    settings: Settings,
):
    try:
        return FeishuCallbackVerifier(settings).verify_event(raw_body, request.headers)
    except FeishuCallbackConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except FeishuCallbackVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    except FeishuCallbackPayloadError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


def _verify_card_request(
    request: Request,
    raw_body: bytes,
    settings: Settings,
):
    try:
        return FeishuCallbackVerifier(settings).verify_card_action(raw_body, request.headers)
    except FeishuCallbackConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except FeishuCallbackVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    except FeishuCallbackPayloadError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
