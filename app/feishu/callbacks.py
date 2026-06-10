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
from app.gateways.url_safety import BaseUrlSafetyError, validate_https_base_url

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


@dataclass(frozen=True)
class FeishuTaskConfirmationCommand:
    event_id: str
    thread_id: UUID
    task_id: UUID
    idempotency_key: str
    decision: str
    source_ref: str
    task_payload_ref: str
    open_message_id: str | None
    open_chat_id: str | None
    card_update_token_ref: str
    operator_open_id: str | None


@dataclass(frozen=True)
class FeishuModelProfileSetupCommand:
    event_id: str
    thread_id: UUID
    source_ref: str
    event_payload_ref: str
    chat_id: str
    model_owner_user_id: str
    model_owner_id_type: str
    provider: str
    model_name: str
    api_key: str
    display_name: str | None
    base_url: str | None
    usage: str
    make_default: bool
    idempotency_key: str
    open_message_id: str | None
    open_chat_id: str | None
    card_update_token_ref: str
    operator_open_id: str | None
    operator_user_id: str | None
    operator_union_id: str | None


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

        if not is_challenge:
            if not self.encrypt_key:
                raise FeishuCallbackConfigurationError("FEISHU_ENCRYPT_KEY is required")
            if not signature_valid:
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
        body = _loads_json(raw_body)
        signature_valid = self._validate_card_signature_if_present(raw_body, headers)
        payload = self._decrypt_if_needed(body)

        challenge = payload.get("challenge")
        is_challenge = isinstance(challenge, str)

        if not is_challenge:
            if not signature_valid:
                self._verify_token(payload, is_challenge=is_challenge)
                return self._verified_callback(payload, raw_body, is_challenge=False)

        if not signature_valid or _callback_token(payload):
            self._verify_token(payload, is_challenge=is_challenge)

        return self._verified_callback(payload, raw_body, is_challenge=is_challenge)

    def _verified_callback(
        self,
        payload: dict[str, Any],
        raw_body: bytes,
        *,
        is_challenge: bool,
    ) -> VerifiedFeishuCallback:
        challenge = payload.get("challenge")

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

        _validate_timestamp_window(timestamp)
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

        try:
            _validate_timestamp_window(timestamp)
        except FeishuCallbackVerificationError as exc:
            if str(exc) == "Invalid Feishu callback timestamp":
                return False
            raise
        candidate_signatures = [
            calculate_feishu_card_signature(
                timestamp=timestamp,
                nonce=nonce,
                verification_token=self.verification_token,
                raw_body=raw_body,
            )
        ]
        if self.encrypt_key:
            candidate_signatures.append(
                calculate_feishu_signature(
                    timestamp=timestamp,
                    nonce=nonce,
                    encrypt_key=self.encrypt_key,
                    raw_body=raw_body,
                )
            )
        return any(hmac.compare_digest(actual, expected) for actual in candidate_signatures)

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
        actual = _callback_token(payload)
        if self.verification_token:
            if not actual:
                raise FeishuCallbackVerificationError(
                    "Invalid Feishu verification token: missing callback token"
                )
            if not hmac.compare_digest(actual, self.verification_token):
                raise FeishuCallbackVerificationError(
                    "Invalid Feishu verification token: callback token mismatch"
                )
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
    value = _extract_card_action_value(callback.payload)
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


def is_task_confirmation_callback(payload: dict[str, Any]) -> bool:
    try:
        value = _extract_card_action_value(payload)
    except FeishuCallbackPayloadError:
        return False
    return value.get("action_kind") == "task_double_check"


def is_model_profile_setup_callback(payload: dict[str, Any]) -> bool:
    try:
        value = _extract_card_action_value(payload)
    except FeishuCallbackPayloadError:
        return False
    return value.get("action_kind") == "model_profile_setup"


def parse_task_confirmation_command(
    callback: VerifiedFeishuCallback,
    *,
    payload_ref: str,
) -> FeishuTaskConfirmationCommand:
    if callback.event_type != "card.action.trigger":
        raise FeishuCallbackPayloadError("Feishu callback is not card.action.trigger")

    event = _payload_event(callback.payload)
    value = _extract_card_action_value(callback.payload)
    if value.get("action_kind") != "task_double_check":
        raise FeishuCallbackPayloadError("Feishu card action is not task_double_check")

    decision = _required_str(value, "decision")
    if decision not in {"approve", "reject"}:
        raise FeishuCallbackPayloadError("Feishu task confirmation decision is invalid")

    context = event.get("context") if isinstance(event.get("context"), dict) else {}
    operator = event.get("operator") if isinstance(event.get("operator"), dict) else {}
    idempotency_key = _required_str(value, "idempotency_key")
    event_id = callback.event_id or f"task-confirm:{idempotency_key}"

    return FeishuTaskConfirmationCommand(
        event_id=event_id,
        thread_id=_required_uuid(value, "thread_id"),
        task_id=_required_uuid(value, "task_id"),
        idempotency_key=idempotency_key,
        decision=decision,
        source_ref=_required_str(value, "source_ref"),
        task_payload_ref=_required_str(value, "task_payload_ref"),
        open_message_id=_first_str(context.get("open_message_id")),
        open_chat_id=_first_str(context.get("open_chat_id")),
        card_update_token_ref=payload_ref,
        operator_open_id=_first_str(operator.get("open_id")),
    )


def parse_model_profile_setup_command(
    callback: VerifiedFeishuCallback,
    *,
    payload_ref: str,
) -> FeishuModelProfileSetupCommand:
    if callback.event_type != "card.action.trigger":
        raise FeishuCallbackPayloadError("Feishu callback is not card.action.trigger")

    event = _payload_event(callback.payload)
    value = _extract_card_action_value(callback.payload)
    if value.get("action_kind") != "model_profile_setup":
        raise FeishuCallbackPayloadError("Feishu card action is not model_profile_setup")

    provider = _required_str(value, "provider").lower()
    if provider not in {"openai", "deepseek"}:
        raise FeishuCallbackPayloadError("Feishu model setup provider is invalid")
    usage = str(value.get("usage") or "chat").lower()
    if usage != "chat":
        raise FeishuCallbackPayloadError("Feishu model setup currently supports chat profiles")

    form_values = _extract_card_form_values(callback.payload)
    api_key = _form_value_str(form_values, "api_key")
    if not api_key:
        raise FeishuCallbackPayloadError("Feishu model setup requires api_key")
    model_name = _form_value_str(form_values, "model_name") or _default_model(provider)
    display_name = _form_value_str(form_values, "display_name")
    base_url = _safe_optional_base_url(_form_value_str(form_values, "base_url"))
    model_owner_id_type = _identity_type_str(value.get("model_owner_id_type")) or "open_id"

    context = event.get("context") if isinstance(event.get("context"), dict) else {}
    operator = event.get("operator") if isinstance(event.get("operator"), dict) else {}
    idempotency_key = _required_str(value, "idempotency_key")
    event_id = callback.event_id or f"model-setup:{idempotency_key}"

    return FeishuModelProfileSetupCommand(
        event_id=event_id,
        thread_id=_required_uuid(value, "thread_id"),
        source_ref=_required_str(value, "source_ref"),
        event_payload_ref=_required_str(value, "event_payload_ref"),
        chat_id=_required_str(value, "chat_id"),
        model_owner_user_id=_required_str(value, "model_owner_user_id"),
        model_owner_id_type=model_owner_id_type,
        provider=provider,
        model_name=model_name,
        api_key=api_key,
        display_name=display_name,
        base_url=base_url,
        usage=usage,
        make_default=_optional_bool(value.get("make_default"), default=True),
        idempotency_key=idempotency_key,
        open_message_id=_first_str(context.get("open_message_id")),
        open_chat_id=_first_str(context.get("open_chat_id")),
        card_update_token_ref=payload_ref,
        operator_open_id=_first_str(operator.get("open_id")),
        operator_user_id=_first_str(operator.get("user_id")),
        operator_union_id=_first_str(operator.get("union_id")),
    )


def _extract_card_action_value(payload: dict[str, Any]) -> dict[str, Any]:
    event = _payload_event(payload)
    action = event.get("action")
    if not isinstance(action, dict):
        raise FeishuCallbackPayloadError("Feishu card action missing event.action")
    return _coerce_action_value(action.get("value"))


def _extract_card_form_values(payload: dict[str, Any]) -> dict[str, Any]:
    event = _payload_event(payload)
    action = event.get("action")
    if not isinstance(action, dict):
        return {}
    candidates = [
        action.get("form_value"),
        action.get("form_values"),
        action.get("input_value"),
        action.get("value"),
        event.get("form_value"),
    ]
    merged: dict[str, Any] = {}
    for candidate in candidates:
        form_value = _coerce_optional_mapping(candidate)
        nested = _coerce_optional_mapping(form_value.get("form_value"))
        if nested:
            form_value = {**form_value, **nested}
        for key, value in form_value.items():
            if key not in merged:
                merged[key] = value
    return merged


def _edited_payload_from_value(value: dict[str, Any]) -> dict[str, Any] | None:
    edited_payload = value.get("edited_payload")
    if isinstance(edited_payload, dict):
        return edited_payload
    form_value = value.get("form_value")
    if isinstance(form_value, dict):
        return form_value
    return None


def _form_value_str(form_values: dict[str, Any], name: str) -> str | None:
    value = form_values.get(name)
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, dict):
        for key in ("value", "text", "content"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
    return None


def _default_model(provider: str) -> str:
    if provider == "deepseek":
        return "deepseek-v4-pro"
    return "gpt-4.1-mini"


def _optional_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
    raise FeishuCallbackPayloadError("Feishu model setup make_default is invalid")


def _safe_optional_base_url(base_url: str | None) -> str | None:
    if not base_url:
        return None
    try:
        return validate_https_base_url(base_url, resolve_dns=False)
    except BaseUrlSafetyError as exc:
        raise FeishuCallbackPayloadError(f"Feishu model setup {exc}") from exc


def _identity_type_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in {"open_id", "user_id", "union_id"}:
        return normalized
    raise FeishuCallbackPayloadError("Feishu model setup owner id type is invalid")


def _callback_token(payload: dict[str, Any]) -> str | None:
    return _first_str(payload.get("token"), _payload_header(payload).get("token"))


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


def _coerce_optional_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


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
