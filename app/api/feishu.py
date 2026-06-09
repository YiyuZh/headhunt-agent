from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import Settings
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


def get_feishu_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_feishu_callback_service(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_feishu_settings),
) -> FeishuCallbackService:
    return FeishuCallbackService(session, settings=settings)


@router.post(
    "/events",
    operation_id="receive_feishu_event",
)
async def receive_feishu_event(
    request: Request,
    settings: Settings = Depends(get_feishu_settings),
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
    settings: Settings = Depends(get_feishu_settings),
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
    if result.status == "model_setup_saved":
        return {
            "toast": {
                "type": "info",
                "content": result.message or "模型已保存并设为默认，已继续发送任务确认卡",
            }
        }
    if result.status == "model_setup_failed":
        return {
            "toast": {
                "type": "error",
                "content": result.message or "模型配置失败，未启动任务",
            }
        }
    content_by_status = {
        "queued": "已确认，正在继续任务",
        "rejected": "已拒绝，未启动任务",
        "duplicate": "已处理过该操作",
    }
    content = content_by_status.get(result.status, "已收到")
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
