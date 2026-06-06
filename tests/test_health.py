from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

ADMIN_HEADERS = {"X-Internal-Admin-Key": "test-admin"}


def test_health_endpoint() -> None:
    client = TestClient(create_app())
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_uses_postgres_main_path() -> None:
    client = TestClient(create_app(settings=Settings(internal_admin_api_key="test-admin")))
    response = client.get("/ready", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    body = response.json()
    checks = body["checks"]
    assert body["status"] == "not_ready"
    assert checks["database_configured"] is True
    assert checks["checkpoint_configured"] is True
    assert checks["vector_store_provider"] == "pgvector"
    assert "LLM_API_KEY" in body["missing_required"]
    assert "FEISHU_APP_ID" not in body["missing_required"]
    assert any(
        detail["name"] == "feishu_openapi" and detail["status"] == "warning"
        for detail in body["details"]
    )


def test_ready_reports_degraded_until_discord_interactions_are_implemented() -> None:
    settings = Settings(
        internal_admin_api_key="test-admin",
        embedding_provider="openai",
        embedding_model="text-embedding-3-small",
        llm_provider="openai_responses",
        llm_model="gpt-4.1-mini",
        llm_api_key="sk-test",
        discord_public_key="discord-public",
        discord_bot_token="discord-token",
        discord_application_id="discord-app",
        discord_allowed_guild_ids="guild-1",
        discord_allowed_channel_ids="channel-1",
        feishu_app_id="cli_app",
        feishu_app_secret="secret",
        feishu_verification_token="verify",
        feishu_encrypt_key="encrypt",
        feishu_default_chat_id="oc_test",
        feishu_bitable_app_token="app_token",
        feishu_bitable_requisition_table_id="tbl_req",
        feishu_bitable_candidate_table_id="tbl_cand",
        feishu_bitable_talent_map_table_id="tbl_map",
        feishu_bitable_report_table_id="tbl_report",
    )
    client = TestClient(create_app(settings=settings))
    response = client.get("/ready", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["missing_required"] == []
    assert body["checks"]["llm_configured"] is True
    assert body["checks"]["feishu_bitable_configured"] is True
    assert body["checks"]["discord_interactions_implemented"] is False
    assert any(
        detail["name"] == "discord_interactions_implementation"
        and detail["status"] == "warning"
        for detail in body["details"]
    )


def test_ready_requires_internal_admin_key() -> None:
    client = TestClient(create_app(settings=Settings(internal_admin_api_key="test-admin")))

    assert client.get("/ready").status_code == 403


def test_ready_fails_closed_when_internal_admin_key_is_not_configured() -> None:
    client = TestClient(create_app())

    assert client.get("/ready", headers=ADMIN_HEADERS).status_code == 503


def test_openapi_docs_are_disabled_in_docker_runtime() -> None:
    client = TestClient(
        create_app(
            settings=Settings(app_env="docker", internal_admin_api_key="test-admin")
        )
    )

    assert client.get("/docs", headers=ADMIN_HEADERS).status_code == 404
    assert client.get("/openapi.json", headers=ADMIN_HEADERS).status_code == 404


def test_feishu_event_challenge_echo() -> None:
    client = TestClient(create_app())
    response = client.post("/feishu/events", json={"challenge": "test_challenge"})

    assert response.status_code == 200
    assert response.json() == {"challenge": "test_challenge"}


def test_feishu_event_does_not_fake_ack_real_callbacks() -> None:
    client = TestClient(create_app())
    response = client.post("/feishu/events", json={"event": {"type": "im.message.receive_v1"}})

    assert response.status_code == 503
    assert "FEISHU_VERIFICATION_TOKEN" in response.json()["detail"]


def test_feishu_card_action_disabled_until_idempotent_resume_exists() -> None:
    client = TestClient(create_app())
    response = client.post("/feishu/card-actions", json={"action": {"idempotency_key": "k"}})

    assert response.status_code == 503
    assert "FEISHU_VERIFICATION_TOKEN" in response.json()["detail"]
