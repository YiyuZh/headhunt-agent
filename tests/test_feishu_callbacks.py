import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from app.api.feishu import get_feishu_callback_service
from app.core.config import Settings, get_settings
from app.feishu.callbacks import (
    FeishuCallbackPayloadError,
    FeishuCallbackVerificationError,
    FeishuCallbackVerifier,
    calculate_feishu_card_signature,
    calculate_feishu_signature,
    parse_card_action_command,
)
from app.feishu.service import FeishuEnqueueResult
from app.main import create_app


def _signed_card_headers(raw_body: bytes, *, token: str = "test-token") -> dict[str, str]:
    timestamp = str(int(datetime.now(UTC).timestamp()))
    nonce = "nonce"
    signature = calculate_feishu_card_signature(
        timestamp=timestamp,
        nonce=nonce,
        verification_token=token,
        raw_body=raw_body,
    )
    return {
        "Content-Type": "application/json",
        "X-Lark-Request-Timestamp": timestamp,
        "X-Lark-Request-Nonce": nonce,
        "X-Lark-Signature": signature,
    }


class FakeFeishuCallbackService:
    def __init__(self):
        self.event_callback = None
        self.card_callback = None

    def enqueue_event(self, callback):
        self.event_callback = callback
        return FeishuEnqueueResult(
            status="queued",
            idempotency_key="event-key",
            payload_ref="artifact://event",
        )

    def enqueue_card_action(self, callback):
        self.card_callback = callback
        return FeishuEnqueueResult(
            status="queued",
            idempotency_key="card-key",
            payload_ref="artifact://card",
        )


class PayloadErrorFeishuCallbackService:
    def enqueue_card_action(self, callback):
        raise FeishuCallbackPayloadError("Feishu callback is not card.action.trigger")


def test_feishu_event_with_valid_token_acks_after_enqueue() -> None:
    fake_service = FakeFeishuCallbackService()
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        feishu_verification_token="test-token"
    )
    app.dependency_overrides[get_feishu_callback_service] = lambda: fake_service
    client = TestClient(app)

    response = client.post(
        "/feishu/events",
        json={
            "schema": "2.0",
            "header": {
                "event_id": "evt_1",
                "event_type": "im.message.receive_v1",
                "token": "test-token",
                "tenant_key": "tenant_1",
                "app_id": "cli_1",
            },
            "event": {"message": {"message_id": "om_1"}},
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "queued", "mode": "ack"}
    assert fake_service.event_callback.event_id == "evt_1"
    assert fake_service.event_callback.message_id == "om_1"


def test_feishu_event_rejects_invalid_token() -> None:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        feishu_verification_token="test-token"
    )
    app.dependency_overrides[get_feishu_callback_service] = FakeFeishuCallbackService
    client = TestClient(app)

    response = client.post(
        "/feishu/events",
        json={
            "header": {
                "event_id": "evt_1",
                "event_type": "im.message.receive_v1",
                "token": "wrong-token",
            },
            "event": {},
        },
    )

    assert response.status_code == 401
    assert "verification token" in response.json()["detail"]


def test_feishu_card_action_returns_official_toast_response() -> None:
    fake_service = FakeFeishuCallbackService()
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        feishu_verification_token="test-token"
    )
    app.dependency_overrides[get_feishu_callback_service] = lambda: fake_service
    client = TestClient(app)

    thread_id = str(uuid4())
    action_id = str(uuid4())
    interrupt_id = str(uuid4())

    body = json.dumps(
        {
            "schema": "2.0",
            "header": {
                "event_id": "card_evt_1",
                "event_type": "card.action.trigger",
                "token": "test-token",
            },
            "event": {
                "operator": {"open_id": "ou_1"},
                "token": "card-update-token",
                "action": {
                    "value": {
                        "thread_id": thread_id,
                        "action_id": action_id,
                        "interrupt_id": interrupt_id,
                        "idempotency_key": "card-action-1",
                        "decision": "approve",
                    }
                },
                "context": {
                    "open_message_id": "om_1",
                    "open_chat_id": "oc_1",
                },
            },
        }
    ).encode("utf-8")

    response = client.post(
        "/feishu/card-actions",
        content=body,
        headers=_signed_card_headers(body),
    )

    assert response.status_code == 200
    assert response.json() == {
        "toast": {"type": "info", "content": "已收到，正在继续任务"}
    }
    assert fake_service.card_callback.event_type == "card.action.trigger"


def test_feishu_card_action_rejects_missing_signature_headers() -> None:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        feishu_verification_token="test-token"
    )
    app.dependency_overrides[get_feishu_callback_service] = FakeFeishuCallbackService
    client = TestClient(app)

    response = client.post(
        "/feishu/card-actions",
        json={
            "schema": "2.0",
            "header": {
                "event_id": "card_evt_1",
                "event_type": "card.action.trigger",
                "token": "test-token",
            },
            "event": {"action": {"value": {"idempotency_key": "card-action-1"}}},
        },
    )

    assert response.status_code == 401
    assert "signature headers" in response.json()["detail"]


def test_feishu_card_action_rejects_stale_signature_timestamp() -> None:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        feishu_verification_token="test-token"
    )
    app.dependency_overrides[get_feishu_callback_service] = FakeFeishuCallbackService
    client = TestClient(app)
    body = json.dumps(
        {
            "schema": "2.0",
            "header": {
                "event_id": "card_evt_1",
                "event_type": "card.action.trigger",
                "token": "test-token",
            },
            "event": {"action": {"value": {"idempotency_key": "card-action-1"}}},
        }
    ).encode("utf-8")
    timestamp = "1710000000"
    signature = calculate_feishu_card_signature(
        timestamp=timestamp,
        nonce="nonce",
        verification_token="test-token",
        raw_body=body,
    )

    response = client.post(
        "/feishu/card-actions",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Lark-Request-Timestamp": timestamp,
            "X-Lark-Request-Nonce": "nonce",
            "X-Lark-Signature": signature,
        },
    )

    assert response.status_code == 401
    assert "timestamp" in response.json()["detail"]


def test_feishu_card_action_uses_sha1_token_signature_not_encrypt_key() -> None:
    fake_service = FakeFeishuCallbackService()
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        feishu_verification_token="test-token",
        feishu_encrypt_key="event-encrypt-key",
    )
    app.dependency_overrides[get_feishu_callback_service] = lambda: fake_service
    client = TestClient(app)

    body = json.dumps(
        {
            "schema": "2.0",
            "header": {
                "event_id": "card_evt_1",
                "event_type": "card.action.trigger",
                "token": "test-token",
            },
            "event": {
                "operator": {"open_id": "ou_1"},
                "action": {
                    "value": {
                        "thread_id": str(uuid4()),
                        "action_id": str(uuid4()),
                        "idempotency_key": "card-action-1",
                        "decision": "approve",
                    }
                },
            },
        }
    ).encode("utf-8")
    response = client.post(
        "/feishu/card-actions",
        content=body,
        headers=_signed_card_headers(body),
    )

    assert response.status_code == 200
    assert fake_service.card_callback.event_type == "card.action.trigger"


def test_feishu_card_action_rejects_wrong_callback_type() -> None:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        feishu_verification_token="test-token"
    )
    app.dependency_overrides[get_feishu_callback_service] = (
        lambda: PayloadErrorFeishuCallbackService()
    )
    client = TestClient(app)

    body = json.dumps(
        {
            "schema": "2.0",
            "header": {
                "event_id": "evt_1",
                "event_type": "im.message.receive_v1",
                "token": "test-token",
            },
            "event": {},
        }
    ).encode("utf-8")

    response = client.post(
        "/feishu/card-actions",
        content=body,
        headers=_signed_card_headers(body),
    )

    assert response.status_code == 400
    assert "card.action.trigger" in response.json()["detail"]


def test_feishu_signature_uses_official_raw_body_formula() -> None:
    raw_body = b'{"hello":"world"}'
    signature = calculate_feishu_signature(
        timestamp="1710000000",
        nonce="nonce",
        encrypt_key="encrypt-key",
        raw_body=raw_body,
    )
    verifier = FeishuCallbackVerifier(
        Settings(
            feishu_verification_token="test-token",
            feishu_encrypt_key="encrypt-key",
        )
    )

    with pytest.raises(FeishuCallbackVerificationError):
        verifier.verify(
            b'{"header":{"token":"test-token","event_type":"im.message.receive_v1"}}',
            Headers(
                {
                    "X-Lark-Request-Timestamp": "1710000000",
                    "X-Lark-Request-Nonce": "nonce",
                    "X-Lark-Signature": signature,
                }
            ),
        )


def test_parse_card_action_command_accepts_json_string_value() -> None:
    thread_id = str(uuid4())
    action_id = str(uuid4())
    interrupt_id = str(uuid4())
    callback = FeishuCallbackVerifier(
        Settings(feishu_verification_token="test-token")
    ).verify(
        json.dumps(
            {
                "schema": "2.0",
                "header": {
                    "event_id": "card_evt_1",
                    "event_type": "card.action.trigger",
                    "token": "test-token",
                },
                "event": {
                    "operator": {"open_id": "ou_1"},
                    "action": {
                        "value": json.dumps(
                            {
                                "thread_id": thread_id,
                                "action_id": action_id,
                                "interrupt_id": interrupt_id,
                                "idempotency_key": "card-action-1",
                                "decision": "edit",
                                "edited_payload": {"note": "ok"},
                            }
                        )
                    },
                    "context": {"open_message_id": "om_1"},
                },
            }
        ).encode("utf-8"),
        Headers({}),
    )

    command = parse_card_action_command(callback, payload_ref="artifact://payload")

    assert str(command.thread_id) == thread_id
    assert str(command.action_id) == action_id
    assert str(command.interrupt_id) == interrupt_id
    assert command.decision == "edit"
    assert command.edited_payload == {"note": "ok"}


def test_parse_card_action_command_rejects_invalid_decision() -> None:
    callback = FeishuCallbackVerifier(
        Settings(feishu_verification_token="test-token")
    ).verify(
        json.dumps(
            {
                "header": {
                    "event_id": "card_evt_1",
                    "event_type": "card.action.trigger",
                    "token": "test-token",
                },
                "event": {
                    "action": {
                        "value": {
                            "thread_id": str(uuid4()),
                            "action_id": str(uuid4()),
                            "idempotency_key": "card-action-1",
                            "decision": "send",
                        }
                    }
                },
            }
        ).encode("utf-8"),
        Headers({}),
    )

    with pytest.raises(FeishuCallbackPayloadError):
        parse_card_action_command(callback, payload_ref="artifact://payload")


def test_parse_card_action_command_rejects_edit_without_payload() -> None:
    callback = FeishuCallbackVerifier(
        Settings(feishu_verification_token="test-token")
    ).verify(
        json.dumps(
            {
                "header": {
                    "event_id": "card_evt_1",
                    "event_type": "card.action.trigger",
                    "token": "test-token",
                },
                "event": {
                    "action": {
                        "value": {
                            "thread_id": str(uuid4()),
                            "action_id": str(uuid4()),
                            "interrupt_id": str(uuid4()),
                            "idempotency_key": "card-action-1",
                            "decision": "edit",
                        }
                    }
                },
            }
        ).encode("utf-8"),
        Headers({}),
    )

    with pytest.raises(FeishuCallbackPayloadError, match="edit requires"):
        parse_card_action_command(callback, payload_ref="artifact://payload")
