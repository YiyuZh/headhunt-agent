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
    assert checks["channel_gateway_provider"] == "feishu"
    assert "MODEL_SECRET_ENCRYPTION_KEY" in body["missing_required"]
    assert "LLM_API_KEY" not in body["missing_required"]
    assert "FEISHU_APP_ID" in body["missing_required"]
    assert "FEISHU_VERIFICATION_TOKEN" in body["missing_required"]
    assert any(
        detail["name"] == "feishu_openapi" and detail["status"] == "missing"
        for detail in body["details"]
    )


def test_ready_reports_ok_when_required_runtime_is_configured() -> None:
    settings = Settings(
        internal_admin_api_key="test-admin",
        model_secret_encryption_key="model-secret",
        embedding_provider="openai",
        embedding_model="text-embedding-3-small",
        embedding_api_key="embedding-secret",
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
    assert body["status"] == "ok"
    assert body["missing_required"] == []
    assert body["checks"]["llm_configured"] is True
    assert body["checks"]["user_model_profiles_enabled"] is True
    assert body["checks"]["feishu_bitable_configured"] is True
    assert body["checks"]["feishu_task_flow_implemented"] is True
    assert body["checks"]["feishu_byok_cards_implemented"] is True
    assert body["checks"]["review_gate_implemented"] is True
    assert body["checks"]["agent_sop_registry_implemented"] is True
    assert body["checks"]["memory_retention_implemented"] is True
    assert any(
        detail["name"] == "feishu_openapi"
        and detail["status"] == "ok"
        for detail in body["details"]
    )
    assert any(
        detail["name"] == "feishu_task_flow_implementation"
        and detail["status"] == "ok"
        for detail in body["details"]
    )
    assert any(
        detail["name"] == "feishu_byok_card_implementation"
        and detail["status"] == "ok"
        for detail in body["details"]
    )
    assert any(
        detail["name"] == "agent_sop_registry_implementation"
        and detail["status"] == "ok"
        for detail in body["details"]
    )
    assert any(
        detail["name"] == "review_gate_implementation"
        and detail["status"] == "ok"
        for detail in body["details"]
    )
    assert any(
        detail["name"] == "memory_retention_implementation"
        and detail["status"] == "ok"
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
    assert "FEISHU_" in response.json()["detail"]


def test_feishu_card_action_disabled_until_idempotent_resume_exists() -> None:
    client = TestClient(create_app())
    response = client.post("/feishu/card-actions", json={"action": {"idempotency_key": "k"}})

    assert response.status_code == 503
    assert "FEISHU_VERIFICATION_TOKEN" in response.json()["detail"]
