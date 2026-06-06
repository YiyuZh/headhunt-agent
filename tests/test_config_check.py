import json

import pytest

from app.core.config import Settings
from app.runtime import config_check


def test_config_check_prints_readiness_without_secrets(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        config_check,
        "get_settings",
        lambda: Settings(
            internal_admin_api_key="admin-secret",
            embedding_provider="openai",
            embedding_model="text-embedding-3-small",
            llm_provider="openai_responses",
            llm_model="gpt-4.1-mini",
            llm_api_key="sk-secret",
            feishu_app_id="cli_app",
            feishu_app_secret="feishu-secret",
            feishu_verification_token="verify-secret",
            feishu_encrypt_key="encrypt-secret",
            feishu_default_chat_id="oc_test",
            feishu_bitable_app_token="app_token",
            feishu_bitable_requisition_table_id="tbl_req",
            feishu_bitable_candidate_table_id="tbl_cand",
            feishu_bitable_talent_map_table_id="tbl_map",
            feishu_bitable_report_table_id="tbl_report",
        ),
    )

    config_check.main([])

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["status"] == "degraded"
    assert payload["missing_required"] == []
    assert "sk-secret" not in output
    assert "feishu-secret" not in output
    assert "admin-secret" not in output


def test_config_check_strict_exits_when_required_config_missing(monkeypatch, capsys) -> None:
    monkeypatch.setattr(config_check, "get_settings", lambda: Settings())

    with pytest.raises(SystemExit) as exc_info:
        config_check.main(["--strict"])

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "not_ready"
    assert "LLM_API_KEY" in payload["missing_required"]
    assert "INTERNAL_ADMIN_API_KEY" in payload["missing_required"]
    assert "FEISHU_APP_ID" not in payload["missing_required"]


def test_config_check_strict_allows_deferred_adapter_warnings(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        config_check,
        "get_settings",
        lambda: Settings(
            internal_admin_api_key="admin-secret",
            embedding_provider="openai",
            embedding_model="text-embedding-3-small",
            llm_provider="openai_responses",
            llm_model="gpt-4.1-mini",
            llm_api_key="sk-secret",
        ),
    )

    config_check.main(["--strict"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "degraded"
    assert payload["missing_required"] == []
    assert any(
        detail["category"] == "feishu" and detail["status"] == "warning"
        for detail in payload["details"]
    )
