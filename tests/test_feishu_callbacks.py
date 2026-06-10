import base64
import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from Crypto.Cipher import AES
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from app.api.feishu import get_feishu_callback_service
from app.core.config import Settings
from app.feishu.callbacks import (
    FeishuCallbackPayloadError,
    FeishuCallbackVerificationError,
    FeishuCallbackVerifier,
    calculate_feishu_signature,
    parse_card_action_command,
    parse_model_profile_setup_command,
    parse_task_confirmation_command,
)
from app.feishu.service import FeishuEnqueueResult
from app.main import create_app


def _signed_card_headers(
    raw_body: bytes, *, encrypt_key: str = "encrypt-key"
) -> dict[str, str]:
    timestamp = str(int(datetime.now(UTC).timestamp()))
    nonce = "nonce"
    signature = calculate_feishu_signature(
        timestamp=timestamp,
        nonce=nonce,
        encrypt_key=encrypt_key,
        raw_body=raw_body,
    )
    return {
        "Content-Type": "application/json",
        "X-Lark-Request-Timestamp": timestamp,
        "X-Lark-Request-Nonce": nonce,
        "X-Lark-Signature": signature,
    }


def _legacy_sha1_card_headers(raw_body: bytes, *, token: str = "test-token") -> dict[str, str]:
    timestamp = str(int(datetime.now(UTC).timestamp()))
    nonce = "nonce"
    signature = hashlib.sha1((timestamp + nonce + token).encode("utf-8") + raw_body).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Lark-Request-Timestamp": timestamp,
        "X-Lark-Request-Nonce": nonce,
        "X-Lark-Signature": signature,
    }


def _signed_event_headers(raw_body: bytes, *, encrypt_key: str = "encrypt-key") -> dict[str, str]:
    timestamp = str(int(datetime.now(UTC).timestamp()))
    nonce = "nonce"
    signature = calculate_feishu_signature(
        timestamp=timestamp,
        nonce=nonce,
        encrypt_key=encrypt_key,
        raw_body=raw_body,
    )
    return {
        "Content-Type": "application/json",
        "X-Lark-Request-Timestamp": timestamp,
        "X-Lark-Request-Nonce": nonce,
        "X-Lark-Signature": signature,
    }


def _verify_signed_card_body(raw_body: bytes):
    return FeishuCallbackVerifier(
        Settings(
            feishu_verification_token="test-token",
            feishu_encrypt_key="encrypt-key",
        )
    ).verify_card_action(raw_body, Headers(_signed_card_headers(raw_body)))


def _encrypted_card_body(payload: dict, *, encrypt_key: str = "encrypt-key") -> bytes:
    plaintext = json.dumps(payload).encode("utf-8")
    padding_len = AES.block_size - (len(plaintext) % AES.block_size)
    padded = plaintext + bytes([padding_len]) * padding_len
    iv = b"0123456789abcdef"
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    cipher = AES.new(key, AES.MODE_CBC, iv)
    encrypted = iv + cipher.encrypt(padded)
    return json.dumps({"encrypt": base64.b64encode(encrypted).decode("utf-8")}).encode(
        "utf-8"
    )


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
    app = create_app(
        settings=Settings(
            feishu_verification_token="test-token",
            feishu_encrypt_key="encrypt-key",
        )
    )
    app.dependency_overrides[get_feishu_callback_service] = lambda: fake_service
    client = TestClient(app)
    body = json.dumps(
        {
            "schema": "2.0",
            "header": {
                "event_id": "evt_1",
                "event_type": "im.message.receive_v1",
                "token": "test-token",
                "tenant_key": "tenant_1",
                "app_id": "cli_1",
            },
            "event": {"message": {"message_id": "om_1"}},
        }
    ).encode("utf-8")

    response = client.post(
        "/feishu/events",
        content=body,
        headers=_signed_event_headers(body),
    )

    assert response.status_code == 200
    assert response.json() == {"status": "queued", "mode": "ack"}
    assert fake_service.event_callback.event_id == "evt_1"
    assert fake_service.event_callback.message_id == "om_1"


def test_feishu_event_with_encrypt_key_requires_fresh_valid_signature() -> None:
    fake_service = FakeFeishuCallbackService()
    app = create_app(
        settings=Settings(
            feishu_verification_token="test-token",
            feishu_encrypt_key="encrypt-key",
        )
    )
    app.dependency_overrides[get_feishu_callback_service] = lambda: fake_service
    client = TestClient(app)
    body = json.dumps(
        {
            "schema": "2.0",
            "header": {
                "event_id": "evt_1",
                "event_type": "im.message.receive_v1",
                "token": "test-token",
            },
            "event": {"message": {"message_id": "om_1"}},
        }
    ).encode("utf-8")

    response = client.post(
        "/feishu/events",
        content=body,
        headers=_signed_event_headers(body),
    )

    assert response.status_code == 200
    assert fake_service.event_callback.event_id == "evt_1"


def test_feishu_event_rejects_stale_signature_timestamp() -> None:
    app = create_app(
        settings=Settings(
            feishu_verification_token="test-token",
            feishu_encrypt_key="encrypt-key",
        )
    )
    app.dependency_overrides[get_feishu_callback_service] = FakeFeishuCallbackService
    client = TestClient(app)
    body = json.dumps(
        {
            "header": {
                "event_id": "evt_1",
                "event_type": "im.message.receive_v1",
                "token": "test-token",
            },
            "event": {},
        }
    ).encode("utf-8")
    timestamp = "1710000000"
    signature = calculate_feishu_signature(
        timestamp=timestamp,
        nonce="nonce",
        encrypt_key="encrypt-key",
        raw_body=body,
    )

    response = client.post(
        "/feishu/events",
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


def test_feishu_event_rejects_invalid_token() -> None:
    app = create_app(
        settings=Settings(
            feishu_verification_token="test-token",
            feishu_encrypt_key="encrypt-key",
        )
    )
    app.dependency_overrides[get_feishu_callback_service] = FakeFeishuCallbackService
    client = TestClient(app)

    body = json.dumps(
        {
            "header": {
                "event_id": "evt_1",
                "event_type": "im.message.receive_v1",
                "token": "wrong-token",
            },
            "event": {},
        }
    ).encode("utf-8")

    response = client.post(
        "/feishu/events",
        content=body,
        headers=_signed_event_headers(body),
    )

    assert response.status_code == 401
    assert "verification token" in response.json()["detail"]


def test_feishu_card_action_returns_official_toast_response() -> None:
    fake_service = FakeFeishuCallbackService()
    app = create_app(
        settings=Settings(
            feishu_verification_token="test-token",
            feishu_encrypt_key="encrypt-key",
        )
    )
    app.dependency_overrides[get_feishu_callback_service] = lambda: fake_service
    client = TestClient(app)

    thread_id = str(uuid4())
    action_id = str(uuid4())
    interrupt_id = str(uuid4())

    payload = {
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
    body = _encrypted_card_body(payload)

    response = client.post(
        "/feishu/card-actions",
        content=body,
        headers=_signed_card_headers(body),
    )

    assert response.status_code == 200
    assert response.json() == {
        "toast": {"type": "info", "content": "已确认，正在继续任务"}
    }
    assert fake_service.card_callback.event_type == "card.action.trigger"
    assert fake_service.card_callback.event_id == "card_evt_1"


def test_feishu_card_action_accepts_encrypted_url_verification_challenge() -> None:
    app = create_app(
        settings=Settings(
            feishu_verification_token="test-token",
            feishu_encrypt_key="encrypt-key",
        )
    )
    app.dependency_overrides[get_feishu_callback_service] = FakeFeishuCallbackService
    client = TestClient(app)
    body = _encrypted_card_body(
        {
            "token": "test-token",
            "challenge": "card-url-check",
            "type": "url_verification",
        }
    )

    response = client.post(
        "/feishu/card-actions",
        content=body,
        headers=_signed_card_headers(body),
    )

    assert response.status_code == 200
    assert response.json() == {"challenge": "card-url-check"}


def test_feishu_card_action_accepts_token_verified_payload_without_signature_headers() -> None:
    app = create_app(
        settings=Settings(
            feishu_verification_token="test-token",
            feishu_encrypt_key="encrypt-key",
        )
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

    assert response.status_code == 200
    assert response.json() == {
        "toast": {"type": "info", "content": "已确认，正在继续任务"}
    }


def test_feishu_card_action_rejects_token_verified_fallback_with_wrong_token() -> None:
    app = create_app(
        settings=Settings(
            feishu_verification_token="test-token",
            feishu_encrypt_key="encrypt-key",
        )
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
                "token": "wrong-token",
            },
            "event": {"action": {"value": {"idempotency_key": "card-action-1"}}},
        },
    )

    assert response.status_code == 401
    assert "verification token" in response.json()["detail"]


def test_feishu_card_action_rejects_stale_signature_timestamp() -> None:
    app = create_app(
        settings=Settings(
            feishu_verification_token="test-token",
            feishu_encrypt_key="encrypt-key",
        )
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
    signature = calculate_feishu_signature(
        timestamp=timestamp,
        nonce="nonce",
        encrypt_key="encrypt-key",
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


def test_feishu_card_action_rejects_legacy_sha1_token_signature() -> None:
    app = create_app(
        settings=Settings(
            feishu_verification_token="test-token",
            feishu_encrypt_key="encrypt-key",
        )
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
        headers=_legacy_sha1_card_headers(body),
    )

    assert response.status_code == 401
    assert "signature" in response.json()["detail"]


def test_feishu_card_action_rejects_wrong_callback_type() -> None:
    app = create_app(
        settings=Settings(
            feishu_verification_token="test-token",
            feishu_encrypt_key="encrypt-key",
        )
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
    raw_body = json.dumps(
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
    ).encode("utf-8")
    callback = _verify_signed_card_body(raw_body)

    command = parse_card_action_command(callback, payload_ref="artifact://payload")

    assert str(command.thread_id) == thread_id
    assert str(command.action_id) == action_id
    assert str(command.interrupt_id) == interrupt_id
    assert command.decision == "edit"
    assert command.edited_payload == {"note": "ok"}


def test_parse_card_action_command_rejects_invalid_decision() -> None:
    raw_body = json.dumps(
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
    ).encode("utf-8")
    callback = _verify_signed_card_body(raw_body)

    with pytest.raises(FeishuCallbackPayloadError):
        parse_card_action_command(callback, payload_ref="artifact://payload")


def test_parse_card_action_command_rejects_edit_without_payload() -> None:
    raw_body = json.dumps(
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
    ).encode("utf-8")
    callback = _verify_signed_card_body(raw_body)

    with pytest.raises(FeishuCallbackPayloadError, match="edit requires"):
        parse_card_action_command(callback, payload_ref="artifact://payload")


def test_parse_task_confirmation_command_accepts_double_check_action() -> None:
    thread_id = str(uuid4())
    task_id = str(uuid4())
    raw_body = json.dumps(
        {
            "header": {
                "event_id": "task_card_evt_1",
                "event_type": "card.action.trigger",
                "token": "test-token",
            },
            "event": {
                "operator": {"open_id": "ou_1"},
                "action": {
                    "value": {
                        "action_kind": "task_double_check",
                        "thread_id": thread_id,
                        "task_id": task_id,
                        "task_payload_ref": "artifact://task",
                        "source_ref": "feishu://message/tenant/oc/om",
                        "idempotency_key": "task_confirm:1",
                        "decision": "approve",
                    }
                },
                "context": {"open_message_id": "om_card"},
            },
        }
    ).encode("utf-8")
    callback = _verify_signed_card_body(raw_body)

    command = parse_task_confirmation_command(callback, payload_ref="artifact://callback")

    assert str(command.thread_id) == thread_id
    assert str(command.task_id) == task_id
    assert command.task_payload_ref == "artifact://task"
    assert command.decision == "approve"
    assert command.operator_open_id == "ou_1"


def test_parse_model_profile_setup_command_reads_form_value() -> None:
    thread_id = str(uuid4())
    raw_body = json.dumps(
        {
            "header": {
                "event_id": "model_card_evt_1",
                "event_type": "card.action.trigger",
                "token": "test-token",
            },
            "event": {
                "operator": {"open_id": "ou_1", "union_id": "on_1"},
                "action": {
                    "value": {
                        "action_kind": "model_profile_setup",
                        "thread_id": thread_id,
                        "source_ref": "feishu://message/tenant/oc/om",
                        "event_payload_ref": "artifact://event",
                        "chat_id": "oc_1",
                        "model_owner_user_id": "ou_1",
                        "model_owner_id_type": "open_id",
                        "provider": "openai",
                        "usage": "chat",
                        "idempotency_key": "model_setup:1",
                        "make_default": "false",
                    },
                    "form_value": {
                        "display_name": "work-openai",
                        "model_name": "gpt-4.1-mini",
                        "api_key": "sk-user-secret",
                        "base_url": "https://api.openai.com",
                    },
                },
                "context": {"open_message_id": "om_card", "open_chat_id": "oc_1"},
            },
        }
    ).encode("utf-8")
    callback = _verify_signed_card_body(raw_body)

    command = parse_model_profile_setup_command(callback, payload_ref="artifact://callback")

    assert str(command.thread_id) == thread_id
    assert command.provider == "openai"
    assert command.model_name == "gpt-4.1-mini"
    assert command.api_key == "sk-user-secret"
    assert command.display_name == "work-openai"
    assert command.model_owner_user_id == "ou_1"
    assert command.model_owner_id_type == "open_id"
    assert command.operator_open_id == "ou_1"
    assert command.make_default is False


def test_parse_model_profile_setup_command_reads_official_form_submit_shape() -> None:
    thread_id = str(uuid4())
    raw_body = json.dumps(
        {
            "schema": "2.0",
            "header": {
                "event_id": "model_card_evt_1",
                "event_type": "card.action.trigger",
                "token": "test-token",
                "tenant_key": "tenant_1",
                "app_id": "cli_1",
            },
            "event": {
                "operator": {
                    "tenant_key": "tenant_1",
                    "open_id": "ou_1",
                    "user_id": "u_1",
                },
                "token": "card-update-token",
                "action": {
                    "value": json.dumps(
                        {
                            "action_kind": "model_profile_setup",
                            "thread_id": thread_id,
                            "source_ref": "feishu://message/tenant/oc/om",
                            "event_payload_ref": "artifact://event",
                            "chat_id": "oc_1",
                            "model_owner_user_id": "ou_1",
                            "model_owner_id_type": "open_id",
                            "provider": "openai",
                            "usage": "chat",
                            "idempotency_key": "model_setup:1",
                            "make_default": "true",
                        }
                    ),
                    "tag": "button",
                    "name": "model_profile_setup_openai_submit",
                    "form_value": {
                        "display_name": {"value": "work-openai"},
                        "model_name": {"value": "gpt-4.1-mini"},
                        "api_key": {"value": "sk-user-secret"},
                        "base_url": {"value": "https://api.openai.com"},
                    },
                },
                "host": "im_message",
                "context": {"open_message_id": "om_card", "open_chat_id": "oc_1"},
            },
        }
    ).encode("utf-8")
    callback = _verify_signed_card_body(raw_body)

    command = parse_model_profile_setup_command(callback, payload_ref="artifact://callback")

    assert str(command.thread_id) == thread_id
    assert command.api_key == "sk-user-secret"
    assert command.display_name == "work-openai"
    assert command.base_url == "https://api.openai.com"
    assert command.operator_open_id == "ou_1"
    assert command.operator_user_id == "u_1"
    assert command.make_default is True


def test_parse_model_profile_setup_command_reads_nested_form_value_from_action_value() -> None:
    thread_id = str(uuid4())
    raw_body = json.dumps(
        {
            "header": {
                "event_id": "model_card_evt_1",
                "event_type": "card.action.trigger",
                "token": "test-token",
            },
            "event": {
                "operator": {"open_id": "ou_1"},
                "action": {
                    "value": {
                        "action_kind": "model_profile_setup",
                        "thread_id": thread_id,
                        "source_ref": "feishu://message/tenant/oc/om",
                        "event_payload_ref": "artifact://event",
                        "chat_id": "oc_1",
                        "model_owner_user_id": "ou_1",
                        "model_owner_id_type": "open_id",
                        "provider": "deepseek",
                        "usage": "chat",
                        "idempotency_key": "model_setup:1",
                        "form_value": {
                            "api_key": {"value": "sk-deepseek-secret"},
                            "display_name": {"value": "work-deepseek"},
                        },
                    },
                },
            },
        }
    ).encode("utf-8")
    callback = _verify_signed_card_body(raw_body)

    command = parse_model_profile_setup_command(callback, payload_ref="artifact://callback")

    assert command.provider == "deepseek"
    assert command.model_name == "deepseek-v4-pro"
    assert command.api_key == "sk-deepseek-secret"
    assert command.display_name == "work-deepseek"


def test_parse_model_profile_setup_command_rejects_unsafe_base_url() -> None:
    thread_id = str(uuid4())
    raw_body = json.dumps(
        {
            "header": {
                "event_id": "model_card_evt_1",
                "event_type": "card.action.trigger",
                "token": "test-token",
            },
            "event": {
                "operator": {"open_id": "ou_1"},
                "action": {
                    "value": {
                        "action_kind": "model_profile_setup",
                        "thread_id": thread_id,
                        "source_ref": "feishu://message/tenant/oc/om",
                        "event_payload_ref": "artifact://event",
                        "chat_id": "oc_1",
                        "model_owner_user_id": "ou_1",
                        "model_owner_id_type": "open_id",
                        "provider": "openai",
                        "usage": "chat",
                        "idempotency_key": "model_setup:1",
                    },
                    "form_value": {
                        "api_key": "sk-user-secret",
                        "base_url": "https://127.0.0.1:11434",
                    },
                },
            },
        }
    ).encode("utf-8")
    callback = _verify_signed_card_body(raw_body)

    with pytest.raises(FeishuCallbackPayloadError, match="base_url host is not allowed"):
        parse_model_profile_setup_command(callback, payload_ref="artifact://callback")
