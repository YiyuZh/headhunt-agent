import json

import httpx
import pytest

from app.core.config import Settings
from app.feishu.gateways import (
    FeishuAuthProvider,
    FeishuGatewayError,
    FeishuHttpBitableGateway,
    FeishuHttpGateway,
    FeishuRateLimitError,
)


class RecordingTransport:
    def __init__(
        self,
        responses: list[dict | str | tuple[int, dict | str, dict[str, str]]],
    ):
        self.responses = responses
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, tuple):
            status_code, payload, headers = response
        else:
            status_code, payload, headers = 200, response, {}
        if isinstance(payload, str):
            return httpx.Response(status_code, text=payload, headers=headers, request=request)
        return httpx.Response(status_code, json=payload, headers=headers, request=request)


def make_client(transport: RecordingTransport) -> httpx.Client:
    return httpx.Client(
        base_url="https://open.feishu.cn",
        transport=httpx.MockTransport(transport),
    )


def make_settings() -> Settings:
    return Settings(
        feishu_app_id="cli_test",
        feishu_app_secret="secret",
    )


def test_auth_provider_fetches_and_caches_tenant_token() -> None:
    transport = RecordingTransport(
        [
            {"code": 0, "msg": "success", "tenant_access_token": "t-1", "expire": 7200},
        ]
    )
    auth = FeishuAuthProvider(settings=make_settings(), client=make_client(transport))

    assert auth.get_tenant_access_token() == "t-1"
    assert auth.get_tenant_access_token() == "t-1"

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.url.path == "/open-apis/auth/v3/tenant_access_token/internal/"
    assert json.loads(request.content) == {"app_id": "cli_test", "app_secret": "secret"}


def test_send_card_uses_interactive_message_payload() -> None:
    transport = RecordingTransport(
        [
            {"code": 0, "tenant_access_token": "t-1", "expire": 7200},
            {"code": 0, "data": {"message_id": "om_1"}},
        ]
    )
    client = make_client(transport)
    auth = FeishuAuthProvider(settings=make_settings(), client=client)
    gateway = FeishuHttpGateway(auth_provider=auth, client=client)

    message_id = gateway.send_card(
        chat_id="oc_1",
        card={"config": {"wide_screen_mode": True}},
        idempotency_key="card-send-1",
    )

    assert message_id == "om_1"
    request = transport.requests[1]
    assert request.method == "POST"
    assert request.url.path == "/open-apis/im/v1/messages"
    assert request.url.params["receive_id_type"] == "chat_id"
    assert request.headers["authorization"] == "Bearer t-1"
    assert request.headers["x-request-id"] == "card-send-1"
    body = json.loads(request.content)
    assert body["receive_id"] == "oc_1"
    assert body["msg_type"] == "interactive"
    assert body["uuid"] == "card-send-1"
    assert json.loads(body["content"]) == {"config": {"wide_screen_mode": True}}


def test_update_card_uses_patch_content_only_payload() -> None:
    transport = RecordingTransport(
        [
            {"code": 0, "tenant_access_token": "t-1", "expire": 7200},
            {"code": 0, "data": {}},
        ]
    )
    client = make_client(transport)
    auth = FeishuAuthProvider(settings=make_settings(), client=client)
    gateway = FeishuHttpGateway(auth_provider=auth, client=client)

    assert gateway.update_card("om_1", {"elements": []}, "card-update-1") == "om_1"

    request = transport.requests[1]
    assert request.method == "PATCH"
    assert request.url.path == "/open-apis/im/v1/messages/om_1"
    body = json.loads(request.content)
    assert body == {"content": json.dumps({"elements": []}, ensure_ascii=False)}


def test_get_chat_info_uses_read_only_chat_api() -> None:
    transport = RecordingTransport(
        [
            {"code": 0, "tenant_access_token": "t-1", "expire": 7200},
            {"code": 0, "data": {"chat_id": "oc_1", "name": "War Room"}},
        ]
    )
    client = make_client(transport)
    auth = FeishuAuthProvider(settings=make_settings(), client=client)
    gateway = FeishuHttpGateway(auth_provider=auth, client=client)

    data = gateway.get_chat_info("oc_1", "chat-smoke-1")

    assert data["chat_id"] == "oc_1"
    request = transport.requests[1]
    assert request.method == "GET"
    assert request.url.path == "/open-apis/im/v1/chats/oc_1"
    assert request.headers["x-request-id"] == "chat-smoke-1"


def test_gateway_refreshes_token_once_on_token_error() -> None:
    transport = RecordingTransport(
        [
            {"code": 0, "tenant_access_token": "old-token", "expire": 7200},
            {"code": 99991663, "msg": "token expired"},
            {"code": 0, "tenant_access_token": "new-token", "expire": 7200},
            {"code": 0, "data": {"message_id": "om_2"}},
        ]
    )
    client = make_client(transport)
    auth = FeishuAuthProvider(settings=make_settings(), client=client)
    gateway = FeishuHttpGateway(auth_provider=auth, client=client)

    assert gateway.send_card("oc_1", {"elements": []}, "card-send-2") == "om_2"

    assert transport.requests[1].headers["authorization"] == "Bearer old-token"
    assert transport.requests[3].headers["authorization"] == "Bearer new-token"


def test_gateway_exposes_retry_after_on_rate_limit() -> None:
    transport = RecordingTransport(
        [
            {"code": 0, "tenant_access_token": "t-1", "expire": 7200},
            (429, {"code": 99991400, "msg": "rate limit"}, {"Retry-After": "45"}),
        ]
    )
    client = make_client(transport)
    auth = FeishuAuthProvider(settings=make_settings(), client=client)
    gateway = FeishuHttpGateway(auth_provider=auth, client=client)

    with pytest.raises(FeishuRateLimitError) as exc:
        gateway.send_card("oc_1", {"elements": []}, "card-send-3")

    assert exc.value.retry_after_seconds == 45


def test_gateway_preserves_retry_after_for_non_json_rate_limit() -> None:
    transport = RecordingTransport(
        [
            {"code": 0, "tenant_access_token": "t-1", "expire": 7200},
            (429, "too many requests", {"Retry-After": "12"}),
        ]
    )
    client = make_client(transport)
    auth = FeishuAuthProvider(settings=make_settings(), client=client)
    gateway = FeishuHttpGateway(auth_provider=auth, client=client)

    with pytest.raises(FeishuRateLimitError) as exc:
        gateway.send_card("oc_1", {"elements": []}, "card-send-4")

    assert exc.value.retry_after_seconds == 12


def test_bitable_batch_create_uses_client_token_query_param() -> None:
    transport = RecordingTransport(
        [
            {"code": 0, "tenant_access_token": "t-1", "expire": 7200},
            {
                "code": 0,
                "data": {
                    "records": [
                        {"record_id": "rec_1"},
                        {"record_id": "rec_2"},
                    ]
                },
            },
        ]
    )
    client = make_client(transport)
    auth = FeishuAuthProvider(settings=make_settings(), client=client)
    gateway = FeishuHttpBitableGateway(auth_provider=auth, client=client)

    record_ids = gateway.batch_create(
        app_token="app_token",
        table_id="tbl_1",
        records=[{"fields": {"name": "A"}}, {"fields": {"name": "B"}}],
        client_token="fe599b60-450f-46ff-b2ef-9f6675625b97",
    )

    assert record_ids == ["rec_1", "rec_2"]
    request = transport.requests[1]
    assert request.url.path == (
        "/open-apis/bitable/v1/apps/app_token/tables/tbl_1/records/batch_create"
    )
    assert request.url.params["client_token"] == "fe599b60-450f-46ff-b2ef-9f6675625b97"
    assert json.loads(request.content)["records"][0]["fields"]["name"] == "A"


def test_bitable_batch_create_requires_uuid4_client_token() -> None:
    transport = RecordingTransport([])
    client = make_client(transport)
    auth = FeishuAuthProvider(settings=make_settings(), client=client)
    gateway = FeishuHttpBitableGateway(auth_provider=auth, client=client)

    with pytest.raises(FeishuGatewayError):
        gateway.batch_create(
            app_token="app_token",
            table_id="tbl_1",
            records=[{"fields": {"name": "A"}}],
            client_token="not-a-uuid",
        )

    assert transport.requests == []


def test_bitable_search_records_uses_read_only_search_api() -> None:
    transport = RecordingTransport(
        [
            {"code": 0, "tenant_access_token": "t-1", "expire": 7200},
            {"code": 0, "data": {"items": [], "has_more": False}},
        ]
    )
    client = make_client(transport)
    auth = FeishuAuthProvider(settings=make_settings(), client=client)
    gateway = FeishuHttpBitableGateway(auth_provider=auth, client=client)

    data = gateway.search_records(
        app_token="app_token",
        table_id="tbl_1",
        page_size=1,
        idempotency_key="bitable-search-smoke",
    )

    assert data["items"] == []
    request = transport.requests[1]
    assert request.method == "POST"
    assert request.url.path == "/open-apis/bitable/v1/apps/app_token/tables/tbl_1/records/search"
    assert request.url.params["page_size"] == "1"
    assert request.url.params["user_id_type"] == "open_id"
    assert json.loads(request.content) == {"automatic_fields": False}
