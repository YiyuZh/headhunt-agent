import json

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.runtime import external_smoke
from app.runtime.external_smoke import run_external_smoke_check


class FeishuRecordingTransport:
    def __init__(self):
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/open-apis/auth/v3/tenant_access_token/internal/":
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "t-1", "expire": 7200},
                request=request,
            )
        if request.url.path == "/open-apis/im/v1/chats/oc_test":
            return httpx.Response(
                200,
                json={"code": 0, "data": {"chat_id": "oc_test"}},
                request=request,
            )
        if request.url.path.endswith("/records/search"):
            return httpx.Response(
                200,
                json={"code": 0, "data": {"items": [], "has_more": False}},
                request=request,
            )
        raise AssertionError(f"Unexpected Feishu path: {request.url.path}")


class OpenAIFakeClient:
    def __init__(self):
        self.posts = []

    def post(self, url, headers, json):
        self.posts.append((url, headers, json))
        if url.endswith("/v1/embeddings"):
            return FakeResponse({"data": [{"index": 0, "embedding": [0.1, 0.2]}]})
        if url.endswith("/v1/responses"):
            return FakeResponse({"output_text": "{\"ok\": true}"})
        if url.endswith("/chat/completions"):
            return FakeResponse({"choices": [{"message": {"content": "{\"ok\": true}"}}]})
        raise AssertionError(f"Unexpected OpenAI URL: {url}")


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def complete_settings() -> Settings:
    return Settings(
        embedding_provider="openai",
        embedding_model="text-embedding-3-small",
        embedding_api_key="sk-embedding",
        llm_provider="openai_responses",
        llm_model="gpt-4.1-mini",
        llm_api_key="sk-test",
        feishu_app_id="cli_app",
        feishu_app_secret="secret",
        feishu_default_chat_id="oc_test",
        feishu_bitable_app_token="app_token",
        feishu_bitable_requisition_table_id="tbl_req",
        feishu_bitable_candidate_table_id="tbl_cand",
        feishu_bitable_talent_map_table_id="tbl_map",
        feishu_bitable_report_table_id="tbl_report",
    )


def test_external_smoke_passes_with_fake_feishu_and_openai() -> None:
    feishu_transport = FeishuRecordingTransport()
    openai_client = OpenAIFakeClient()

    report = run_external_smoke_check(
        settings=complete_settings(),
        feishu_client=httpx.Client(
            base_url="https://open.feishu.cn",
            transport=httpx.MockTransport(feishu_transport),
        ),
        openai_client=openai_client,
    )

    assert report.status == "ok"
    check_names = [check.name for check in report.checks]
    assert "feishu_token" in check_names
    assert "feishu_chat" in check_names
    assert "openai_embedding" in check_names
    assert "openai_llm" in check_names
    bitable_requests = [
        request
        for request in feishu_transport.requests
        if request.url.path.endswith("/records/search")
    ]
    assert len(bitable_requests) == 4
    assert len(openai_client.posts) == 2


def test_external_smoke_can_include_deepseek_when_key_is_configured() -> None:
    openai_client = OpenAIFakeClient()
    settings = complete_settings()
    settings.deepseek_api_key = SecretStr("sk-deepseek")

    report = run_external_smoke_check(
        settings=settings,
        include_feishu=False,
        include_openai=False,
        include_deepseek=True,
        openai_client=openai_client,
    )

    assert any(check.name == "deepseek_llm" and check.status == "ok" for check in report.checks)
    assert any(url.endswith("/chat/completions") for url, _headers, _json in openai_client.posts)


def test_external_smoke_fails_when_required_config_is_missing() -> None:
    report = run_external_smoke_check(settings=Settings())

    assert report.status == "failed"
    assert any(check.name == "feishu_token" and check.status == "failed" for check in report.checks)
    assert any(
        check.name == "openai_embedding" and check.status == "failed"
        for check in report.checks
    )


def test_external_smoke_cli_exits_nonzero_on_failed_report(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        external_smoke,
        "run_external_smoke_check",
        lambda **_kwargs: external_smoke.ExternalSmokeReport(
            status="failed",
            checks=[
                external_smoke.ExternalSmokeCheck(
                    name="feishu_token",
                    status="failed",
                    message="missing config",
                )
            ],
        ),
    )

    with pytest.raises(SystemExit) as exc_info:
        external_smoke.main([])

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed"
