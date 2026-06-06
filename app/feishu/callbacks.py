import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from Crypto.Cipher import AES
from pydantic import SecretStr
from starlette.datastructures import Headers

from app.core.config import Settings

FEISHU_SIGNATURE_HEADER = "X-Lark-Signature"
FEISHU_TIMESTAMP_HEADER = "X-Lark-Request-Timestamp"
FEISHU_NONCE_HEADER = "X-Lark-Request-Nonce"
FEISHU_SIGNATURE_MAX_AGE_SECONDS = 300


class FeishuCallbackError(RuntimeError):
    pass


class FeishuCallbackConfigurationError(FeishuCallbackError):
    pass


class FeishuCallbackVerificationError(FeishuCallbackError):
    pass


class FeishuCallbackPayloadError(FeishuCallbackError):
    pass


@dataclass(frozen=True)
class VerifiedFeishuCallback:
    payload: dict[str, Any]
    raw_body: bytes
    payload_hash: str
    is_challenge: bool
    challenge: str | None
    event_id: str | None
    event_type: str
    tenant_key: str | None
    app_id: str | None
    message_id: str | None


@dataclass(frozen=True)
class FeishuCardActionCommand:
    event_id: str
    thread_id: UUID
    action_id: UUID
    interrupt_id: UUID
    idempotency_key: str
    decision: str
    open_message_id: str | None
    open_chat_id: str | None
    card_update_token_ref: str
    operator_open_id: str | None
    edited_payload: dict[str, Any] | None


def calculate_feishu_signature(
    *,
    timestamp: str,
    nonce: str,
    encrypt_key: str,
    raw_body: bytes,
) -> str:
    sign_bytes = (timestamp + nonce + encrypt_key).encode("utf-8") + raw_body
    return hashlib.sha256(sign_bytes).hexdigest()


def calculate_feishu_card_signature(
    *,
    timestamp: str,
    nonce: str,
    verification_token: str,
    raw_body: bytes,
) -> str:
    sign_bytes = (timestamp + nonce + verification_token).encode("utf-8") + raw_body
    return hashlib.sha1(sign_bytes).hexdigest()


class FeishuCallbackVerifier:
    def __init__(self, settings: Settings):
        self.verification_token = _secret_value(settings.feishu_verification_token)
        self.encrypt_key = _secret_value(settings.feishu_encrypt_key)

    def verify_event(
        self,
        raw_body: bytes,
        headers: Headers | dict[str, str],
    ) -> VerifiedFeishuCallback:
        body = _loads_json(raw_body)
        signature_valid = self._validate_signature_if_present(raw_body, headers)
        payload = self._decrypt_if_needed(body)

        challenge = payload.get("challenge")
        is_challenge = isinstance(challenge, str)

        if self.encrypt_key and not signature_valid and not is_challenge:
            raise FeishuCallbackVerificationError("Feishu signature headers are required")

        self._verify_token(payload, is_challenge=is_challenge)

        header = _payload_header(payload)
        event = _payload_event(payload)
        event_type = _first_str(
            header.get("event_type"),
            payload.get("type"),
            payload.get("event_type"),
        )
        if not event_type and is_challenge:
            event_type = "url_verification"
        if not event_type:
            raise FeishuCallbackPayloadError("Feishu callback missing event_type")

        event_id = _first_str(header.get("event_id"), payload.get("uuid"), payload.get("event_id"))
        tenant_key = _first_str(header.get("tenant_key"), event.get("tenant_key"))
        app_id = _first_str(header.get("app_id"), payload.get("app_id"))

        return VerifiedFeishuCallback(
            payload=payload,
            raw_body=raw_body,
            payload_hash=hashlib.sha256(raw_body).hexdigest(),
            is_challenge=is_challenge,
            challenge=challenge if is_challenge else None,
            event_id=event_id,
            event_type=event_type,
            tenant_key=tenant_key,
            app_id=app_id,
            message_id=_extract_message_id(event),
        )

    def verify_card_action(
        self,
        raw_body: bytes,
        headers: Headers | dict[str, str],
    ) -> VerifiedFeishuCallback:
        payload = _loads_json(raw_body)
        challenge = payload.get("challenge")
        is_challenge = isinstance(challenge, str)

        self._verify_token(payload, is_challenge=is_challenge)
        if not is_challenge:
            self._validate_card_signature_required(raw_body, headers)

        header = _payload_header(payload)
        event = _payload_event(payload)
        event_type = _first_str(
            header.get("event_type"),
            payload.get("type"),
            payload.get("event_type"),
        )
        if not event_type and is_challenge:
            event_type = "url_verification"
        if not event_type:
            raise FeishuCallbackPayloadError("Feishu callback missing event_type")

        event_id = _first_str(header.get("event_id"), payload.get("uuid"), payload.get("event_id"))
        tenant_key = _first_str(header.get("tenant_key"), event.get("tenant_key"))
        app_id = _first_str(header.get("app_id"), payload.get("app_id"))

        return VerifiedFeishuCallback(
            payload=payload,
            raw_body=raw_body,
            payload_hash=hashlib.sha256(raw_body).hexdigest(),
            is_challenge=is_challenge,
            challenge=challenge if is_challenge else None,
            event_id=event_id,
            event_type=event_type,
            tenant_key=tenant_key,
            app_id=app_id,
            message_id=_extract_message_id(event),
        )

    def verify(self, raw_body: bytes, headers: Headers | dict[str, str]) -> VerifiedFeishuCallback:
        return self.verify_event(raw_body, headers)

    def _validate_signature_if_present(
        self,
        raw_body: bytes,
        headers: Headers | dict[str, str],
    ) -> bool:
        if not self.encrypt_key:
            return False

        timestamp = headers.get(FEISHU_TIMESTAMP_HEADER)
        nonce = headers.get(FEISHU_NONCE_HEADER)
        expected = headers.get(FEISHU_SIGNATURE_HEADER)
        if not (timestamp and nonce and expected):
            return False

        actual = calculate_feishu_signature(
            timestamp=timestamp,
            nonce=nonce,
            encrypt_key=self.encrypt_key,
            raw_body=raw_body,
        )
        if not hmac.compare_digest(actual, expected):
            raise FeishuCallbackVerificationError("Invalid Feishu callback signature")
        return True

    def _validate_card_signature_if_present(
        self,
        raw_body: bytes,
        headers: Headers | dict[str, str],
    ) -> bool:
        if not self.verification_token:
            return False

        timestamp = headers.get(FEISHU_TIMESTAMP_HEADER)
        nonce = headers.get(FEISHU_NONCE_HEADER)
        expected = headers.get(FEISHU_SIGNATURE_HEADER)
        if not (timestamp and nonce and expected):
            return False

        actual = calculate_feishu_card_signature(
            timestamp=timestamp,
            nonce=nonce,
            verification_token=self.verification_token,
            raw_body=raw_body,
        )
        if not hmac.compare_digest(actual, expected):
            raise FeishuCallbackVerificationError("Invalid Feishu card callback signature")
        return True

    def _validate_card_signature_required(
        self,
        raw_body: bytes,
        headers: Headers | dict[str, str],
    ) -> None:
        if not self.verification_token:
            raise FeishuCallbackConfigurationError("FEISHU_VERIFICATION_TOKEN is required")

        timestamp = headers.get(FEISHU_TIMESTAMP_HEADER)
        nonce = headers.get(FEISHU_NONCE_HEADER)
        expected = headers.get(FEISHU_SIGNATURE_HEADER)
        if not (timestamp and nonce and expected):
            raise FeishuCallbackVerificationError(
                "Feishu card callback signature headers are required"
            )
        _validate_timestamp_window(timestamp)

        actual = calculate_feishu_card_signature(
            timestamp=timestamp,
            nonce=nonce,
            verification_token=self.verification_token,
            raw_body=raw_body,
        )
        if not hmac.compare_digest(actual, expected):
            raise FeishuCallbackVerificationError("Invalid Feishu card callback signature")

    def _decrypt_if_needed(self, body: dict[str, Any]) -> dict[str, Any]:
        encrypted = body.get("encrypt")
        if not isinstance(encrypted, str):
            return body
        if not self.encrypt_key:
            raise FeishuCallbackConfigurationError("FEISHU_ENCRYPT_KEY is required")

        try:
            encrypted_bytes = base64.b64decode(encrypted)
            iv = encrypted_bytes[: AES.block_size]
            cipher_text = encrypted_bytes[AES.block_size :]
            key = hashlib.sha256(self.encrypt_key.encode("utf-8")).digest()
            cipher = AES.new(key, AES.MODE_CBC, iv)
            decrypted = _unpad(cipher.decrypt(cipher_text))
            return _loads_json(decrypted)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            raise FeishuCallbackVerificationError("Failed to decrypt Feishu callback") from exc

    def _verify_token(self, payload: dict[str, Any], *, is_challenge: bool) -> None:
        actual = _first_str(payload.get("token"), _payload_header(payload).get("token"))
        if self.verification_token:
            if not actual or not hmac.compare_digest(actual, self.verification_token):
                raise FeishuCallbackVerificationError("Invalid Feishu verification token")
            return

        if not is_challenge:
            raise FeishuCallbackConfigurationError("FEISHU_VERIFICATION_TOKEN is required")


def build_event_dedupe_key(callback: VerifiedFeishuCallback) -> str:
    identity = callback.message_id or callback.event_id or callback.payload_hash
    return ":".join(
        [
            callback.tenant_key or "unknown_tenant",
            callback.event_type,
            identity,
        ]
    )


def build_event_idempotency_key(callback: VerifiedFeishuCallback) -> str:
    return f"feishu:event:{build_event_dedupe_key(callback)}"


def parse_card_action_command(
    callback: VerifiedFeishuCallback,
    *,
    payload_ref: str,
) -> FeishuCardActionCommand:
    if callback.event_type != "card.action.trigger":
        raise FeishuCallbackPayloadError("Feishu callback is not card.action.trigger")

    event = _payload_event(callback.payload)
    action = event.get("action")
    if not isinstance(action, dict):
        raise FeishuCallbackPayloadError("Feishu card action missing event.action")

    value = _coerce_action_value(action.get("value"))
    idempotency_key = _required_str(value, "idempotency_key")
    decision = _required_str(value, "decision")
    if decision not in {"approve", "reject", "edit"}:
        raise FeishuCallbackPayloadError("Feishu card action decision is invalid")
    edited_payload = _edited_payload_from_value(value)
    if decision == "edit" and not edited_payload:
        raise FeishuCallbackPayloadError(
            "Feishu card action edit requires edited_payload or form_value"
        )

    context = event.get("context") if isinstance(event.get("context"), dict) else {}
    operator = event.get("operator") if isinstance(event.get("operator"), dict) else {}
    event_id = callback.event_id or f"card:{idempotency_key}"

    return FeishuCardActionCommand(
        event_id=event_id,
        thread_id=_required_uuid(value, "thread_id"),
        action_id=_required_uuid(value, "action_id"),
        interrupt_id=_required_uuid(value, "interrupt_id"),
        idempotency_key=idempotency_key,
        decision=decision,
        open_message_id=_first_str(context.get("open_message_id")),
        open_chat_id=_first_str(context.get("open_chat_id")),
        card_update_token_ref=payload_ref,
        operator_open_id=_first_str(operator.get("open_id")),
        edited_payload=edited_payload,
    )


def _edited_payload_from_value(value: dict[str, Any]) -> dict[str, Any] | None:
    edited_payload = value.get("edited_payload")
    if isinstance(edited_payload, dict):
        return edited_payload
    form_value = value.get("form_value")
    if isinstance(form_value, dict):
        return form_value
    return None


def _secret_value(secret: SecretStr | None) -> str | None:
    return secret.get_secret_value() if secret else None


def _loads_json(data: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FeishuCallbackPayloadError("Feishu callback body must be JSON") from exc
    if not isinstance(parsed, dict):
        raise FeishuCallbackPayloadError("Feishu callback body must be a JSON object")
    return parsed


def _validate_timestamp_window(timestamp: str) -> None:
    try:
        timestamp_seconds = int(timestamp)
    except ValueError as exc:
        raise FeishuCallbackVerificationError("Invalid Feishu callback timestamp") from exc
    now_seconds = int(datetime.now(UTC).timestamp())
    if abs(now_seconds - timestamp_seconds) > FEISHU_SIGNATURE_MAX_AGE_SECONDS:
        raise FeishuCallbackVerificationError("Feishu callback timestamp is outside allowed window")


def _unpad(data: bytes) -> bytes:
    if not data:
        raise ValueError("empty AES plaintext")
    padding = data[-1]
    if padding < 1 or padding > AES.block_size:
        raise ValueError("invalid PKCS7 padding")
    if data[-padding:] != bytes([padding]) * padding:
        raise ValueError("invalid PKCS7 padding bytes")
    return data[:-padding]


def _payload_header(payload: dict[str, Any]) -> dict[str, Any]:
    header = payload.get("header")
    return header if isinstance(header, dict) else {}


def _payload_event(payload: dict[str, Any]) -> dict[str, Any]:
    event = payload.get("event")
    return event if isinstance(event, dict) else {}


def _extract_message_id(event: dict[str, Any]) -> str | None:
    message = event.get("message")
    if isinstance(message, dict):
        message_id = _first_str(message.get("message_id"), message.get("open_message_id"))
        if message_id:
            return message_id
    return _first_str(event.get("message_id"), event.get("open_message_id"))


def _first_str(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def _coerce_action_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError as exc:
            raise FeishuCallbackPayloadError("Feishu card action value must be JSON") from exc
    raise FeishuCallbackPayloadError("Feishu card action value must be an object")


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise FeishuCallbackPayloadError(f"Feishu card action missing {key}")
    return value


def _required_uuid(payload: dict[str, Any], key: str) -> UUID:
    try:
        return UUID(_required_str(payload, key))
    except ValueError as exc:
        raise FeishuCallbackPayloadError(f"Feishu card action {key} must be a UUID") from exc


def _optional_uuid(value: Any) -> UUID | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise FeishuCallbackPayloadError("Feishu card interrupt_id must be a string")
    try:
        return UUID(value)
    except ValueError as exc:
        raise FeishuCallbackPayloadError("Feishu card interrupt_id must be a UUID") from exc
