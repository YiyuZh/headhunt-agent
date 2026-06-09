import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx

from app.core.config import Settings

TOKEN_REFRESH_WINDOW_SECONDS = 1800
TOKEN_EXPIRED_CODES = {99991663, 99991664, 99991668}
BITABLE_BATCH_CREATE_MAX_RECORDS = 1000


class FeishuGatewayError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: int | None = None,
        retry_after_seconds: int | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.retry_after_seconds = retry_after_seconds


class FeishuRateLimitError(FeishuGatewayError):
    pass


@dataclass
class CachedTenantToken:
    token: str
    expires_at: datetime


class FeishuAuthProvider:
    def __init__(self, *, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client or httpx.Client(base_url=settings.feishu_base_url, timeout=10.0)
        self._cached_token: CachedTenantToken | None = None
        self._lock = threading.Lock()

    def get_tenant_access_token(self, *, force_refresh: bool = False) -> str:
        with self._lock:
            if not force_refresh and self._cached_token and not self._should_refresh():
                return self._cached_token.token

            token, expire_seconds = self._fetch_tenant_access_token()
            self._cached_token = CachedTenantToken(
                token=token,
                expires_at=datetime.now(UTC) + timedelta(seconds=expire_seconds),
            )
            return token

    def _should_refresh(self) -> bool:
        if self._cached_token is None:
            return True
        refresh_at = self._cached_token.expires_at - timedelta(
            seconds=TOKEN_REFRESH_WINDOW_SECONDS
        )
        return datetime.now(UTC) >= refresh_at

    def _fetch_tenant_access_token(self) -> tuple[str, int]:
        if not self.settings.feishu_app_id or not self.settings.feishu_app_secret:
            raise FeishuGatewayError("FEISHU_APP_ID and FEISHU_APP_SECRET are required")

        response = self.client.post(
            "/open-apis/auth/v3/tenant_access_token/internal/",
            json={
                "app_id": self.settings.feishu_app_id,
                "app_secret": self.settings.feishu_app_secret.get_secret_value(),
            },
        )
        payload = _parse_response_json(response)
        _raise_for_feishu_error(response, payload)

        token = payload.get("tenant_access_token")
        expire_seconds = payload.get("expire")
        if not isinstance(token, str) or not token:
            raise FeishuGatewayError("Feishu token response missing tenant_access_token")
        if not isinstance(expire_seconds, int) or expire_seconds <= 0:
            raise FeishuGatewayError("Feishu token response missing expire")
        return token, expire_seconds


class FeishuHttpGateway:
    def __init__(self, *, auth_provider: FeishuAuthProvider, client: httpx.Client | None = None):
        self.auth_provider = auth_provider
        self.client = client or auth_provider.client

    def send_card(self, chat_id: str, card: dict, idempotency_key: str) -> str:
        payload = {
            "receive_id": chat_id,
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False),
            "uuid": _message_uuid(idempotency_key),
        }
        data = self._request_json(
            "POST",
            "/open-apis/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            json_payload=payload,
            idempotency_key=idempotency_key,
        )
        message_id = _nested_str(data, "data", "message_id")
        if not message_id:
            raise FeishuGatewayError("Feishu send_card response missing message_id")
        return message_id

    def update_card(self, open_message_id: str, card: dict, idempotency_key: str) -> str:
        payload = {"content": json.dumps(card, ensure_ascii=False)}
        data = self._request_json(
            "PATCH",
            f"/open-apis/im/v1/messages/{open_message_id}",
            json_payload=payload,
            idempotency_key=idempotency_key,
        )
        return _nested_str(data, "data", "message_id") or open_message_id

    def get_chat_info(self, chat_id: str, idempotency_key: str) -> dict[str, Any]:
        return self._request_json(
            "GET",
            f"/open-apis/im/v1/chats/{chat_id}",
            idempotency_key=idempotency_key,
        ).get("data", {})

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_payload: dict | None = None,
        idempotency_key: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        token = self.auth_provider.get_tenant_access_token()
        response = self.client.request(
            method,
            path,
            params=params,
            headers=_auth_headers(token, idempotency_key),
            json=json_payload,
        )
        payload = _parse_response_json(response)

        if _needs_token_refresh(response, payload):
            token = self.auth_provider.get_tenant_access_token(force_refresh=True)
            response = self.client.request(
                method,
                path,
                params=params,
                headers=_auth_headers(token, idempotency_key),
                json=json_payload,
            )
            payload = _parse_response_json(response)

        _raise_for_feishu_error(response, payload)
        return payload


class FeishuHttpBitableGateway:
    def __init__(self, *, auth_provider: FeishuAuthProvider, client: httpx.Client | None = None):
        self.auth_provider = auth_provider
        self.client = client or auth_provider.client

    def batch_create(
        self,
        app_token: str,
        table_id: str,
        records: list[dict],
        client_token: str,
    ) -> list[str]:
        _validate_uuid4_client_token(client_token)
        _validate_bitable_batch_records(records)
        token = self.auth_provider.get_tenant_access_token()
        response = self.client.post(
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
            params={
                "client_token": client_token,
                "ignore_consistency_check": "true",
                "user_id_type": "open_id",
            },
            headers=_auth_headers(token, client_token),
            json={"records": records},
        )
        payload = _parse_response_json(response)

        if _needs_token_refresh(response, payload):
            token = self.auth_provider.get_tenant_access_token(force_refresh=True)
            response = self.client.post(
                f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
                params={
                    "client_token": client_token,
                    "ignore_consistency_check": "true",
                    "user_id_type": "open_id",
                },
                headers=_auth_headers(token, client_token),
                json={"records": records},
            )
            payload = _parse_response_json(response)

        _raise_for_feishu_error(response, payload)
        response_records = payload.get("data", {}).get("records", [])
        if not isinstance(response_records, list):
            raise FeishuGatewayError("Feishu Bitable response missing records")

        record_ids = [
            record["record_id"]
            for record in response_records
            if isinstance(record, dict) and isinstance(record.get("record_id"), str)
        ]
        if len(record_ids) != len(records):
            raise FeishuGatewayError("Feishu Bitable response record count mismatch")
        return record_ids

    def search_records(
        self,
        app_token: str,
        table_id: str,
        *,
        page_size: int = 1,
        idempotency_key: str,
    ) -> dict[str, Any]:
        token = self.auth_provider.get_tenant_access_token()
        response = self.client.post(
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search",
            params={
                "page_size": str(page_size),
                "user_id_type": "open_id",
            },
            headers=_auth_headers(token, idempotency_key),
            json={"automatic_fields": False},
        )
        payload = _parse_response_json(response)

        if _needs_token_refresh(response, payload):
            token = self.auth_provider.get_tenant_access_token(force_refresh=True)
            response = self.client.post(
                f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search",
                params={
                    "page_size": str(page_size),
                    "user_id_type": "open_id",
                },
                headers=_auth_headers(token, idempotency_key),
                json={"automatic_fields": False},
            )
            payload = _parse_response_json(response)

        _raise_for_feishu_error(response, payload)
        data = payload.get("data", {})
        if not isinstance(data, dict):
            raise FeishuGatewayError("Feishu Bitable search response missing data")
        return data


def _auth_headers(token: str, idempotency_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
        "X-Request-Id": idempotency_key,
    }


def _message_uuid(idempotency_key: str) -> str:
    if len(idempotency_key) <= 50:
        return idempotency_key
    return hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:50]


def _parse_response_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        retry_after = _retry_after_seconds(response)
        if response.status_code == 429:
            raise FeishuRateLimitError(
                "Feishu API returned non-JSON rate limit response",
                status_code=response.status_code,
                retry_after_seconds=retry_after,
            ) from exc
        raise FeishuGatewayError(
            "Feishu API returned non-JSON response",
            status_code=response.status_code,
            retry_after_seconds=retry_after,
        ) from exc
    if not isinstance(payload, dict):
        raise FeishuGatewayError(
            "Feishu API returned non-object response",
            status_code=response.status_code,
        )
    return payload


def _needs_token_refresh(response: httpx.Response, payload: dict[str, Any]) -> bool:
    code = payload.get("code")
    return response.status_code == 401 or code in TOKEN_EXPIRED_CODES


def _raise_for_feishu_error(response: httpx.Response, payload: dict[str, Any]) -> None:
    retry_after = _retry_after_seconds(response)
    code = payload.get("code")
    message = str(payload.get("msg") or payload.get("message") or "Feishu API error")

    if response.status_code == 429:
        raise FeishuRateLimitError(
            message,
            status_code=response.status_code,
            code=code if isinstance(code, int) else None,
            retry_after_seconds=retry_after,
        )
    if response.status_code >= 400:
        raise FeishuGatewayError(
            message,
            status_code=response.status_code,
            code=code if isinstance(code, int) else None,
            retry_after_seconds=retry_after,
        )
    if isinstance(code, int) and code != 0:
        error_cls = FeishuRateLimitError if retry_after else FeishuGatewayError
        raise error_cls(
            message,
            status_code=response.status_code,
            code=code,
            retry_after_seconds=retry_after,
        )


def _retry_after_seconds(response: httpx.Response) -> int | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(int(value), 0)
    except ValueError:
        return None


def _nested_str(payload: dict[str, Any], *path: str) -> str | None:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, str) and current else None


def _validate_uuid4_client_token(client_token: str) -> None:
    try:
        parsed = UUID(client_token)
    except ValueError as exc:
        raise FeishuGatewayError("Bitable client_token must be a UUIDv4 string") from exc
    if parsed.version != 4 or str(parsed) != client_token.lower():
        raise FeishuGatewayError("Bitable client_token must be a UUIDv4 string")


def _validate_bitable_batch_records(records: list[dict]) -> None:
    if not records or len(records) > BITABLE_BATCH_CREATE_MAX_RECORDS:
        raise FeishuGatewayError(
            "Bitable batch_create records must contain "
            f"1-{BITABLE_BATCH_CREATE_MAX_RECORDS} objects"
        )
    if not all(isinstance(record, dict) for record in records):
        raise FeishuGatewayError("Bitable batch_create records must be objects")
