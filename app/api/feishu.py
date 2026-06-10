import hashlib
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.feishu.callbacks import (
    FEISHU_NONCE_HEADER,
    FEISHU_SIGNATURE_HEADER,
    FEISHU_TIMESTAMP_HEADER,
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
logger = logging.getLogger(__name__)


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
        _log_feishu_callback_rejection("event", request, raw_body, exc)
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
        _log_feishu_callback_rejection("card_action", request, raw_body, exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    except FeishuCallbackPayloadError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


def _log_feishu_callback_rejection(
    callback_kind: str,
    request: Request,
    raw_body: bytes,
    exc: FeishuCallbackVerificationError,
) -> None:
    logger.warning(
        "Feishu %s callback rejected: reason=%s body_sha256=%s body_bytes=%s "
        "headers=%s payload=%s",
        callback_kind,
        str(exc),
        hashlib.sha256(raw_body).hexdigest(),
        len(raw_body),
        _feishu_header_presence(request),
        _feishu_payload_summary(raw_body),
    )


def _feishu_header_presence(request: Request) -> str:
    present = []
    missing = []
    for label, header_name in (
        ("signature", FEISHU_SIGNATURE_HEADER),
        ("timestamp", FEISHU_TIMESTAMP_HEADER),
        ("nonce", FEISHU_NONCE_HEADER),
    ):
        target = present if request.headers.get(header_name) else missing
        target.append(label)
    return f"present={','.join(present) or '-'} missing={','.join(missing) or '-'}"


def _feishu_payload_summary(raw_body: bytes) -> str:
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "json=invalid"
    if not isinstance(payload, dict):
        return "json=non_object"

    token_locations = []
    if isinstance(payload.get("token"), str):
        token_locations.append("top")
    header = payload.get("header")
    if isinstance(header, dict) and isinstance(header.get("token"), str):
        token_locations.append("header")
    event = payload.get("event")
    if isinstance(event, dict) and isinstance(event.get("token"), str):
        token_locations.append("event")
    if isinstance(payload.get("encrypt"), str):
        token_locations.append("encrypted")

    event_type = "-"
    if isinstance(header, dict) and isinstance(header.get("event_type"), str):
        event_type = header["event_type"]
    elif isinstance(payload.get("type"), str):
        event_type = payload["type"]
    elif isinstance(payload.get("event_type"), str):
        event_type = payload["event_type"]

    return f"event_type={event_type} token_locations={','.join(token_locations) or '-'}"
