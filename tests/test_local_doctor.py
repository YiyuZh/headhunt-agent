import json
import subprocess
from pathlib import Path

import pytest

from app.runtime import local_doctor


def test_local_doctor_reports_missing_env_without_secrets(tmp_path: Path) -> None:
    report = local_doctor.build_local_doctor_report(
        repo_root=tmp_path,
        check_docker=False,
    )

    assert report["status"] == "not_ready"
    assert report["env"]["exists"] is False
    assert "Create .env" in report["blocking_issues"][0]
    assert report["commands"]["start"] == "docker compose up -d --build"
    assert "scripts\\lietou-oneclick.ps1" in report["next_steps"][0]
    assert "sk-live" not in json.dumps(report).lower()


def test_local_doctor_init_env_copies_example_without_overwrite(tmp_path: Path) -> None:
    example = tmp_path / ".env.example"
    env_file = tmp_path / ".env"
    example.write_text("INTERNAL_ADMIN_API_KEY=change-this-admin-key\n", encoding="utf-8")

    first_report = local_doctor.build_local_doctor_report(
        repo_root=tmp_path,
        init_env=True,
        check_docker=False,
    )
    env_file.write_text("INTERNAL_ADMIN_API_KEY=custom-value\n", encoding="utf-8")
    second_report = local_doctor.build_local_doctor_report(
        repo_root=tmp_path,
        init_env=True,
        check_docker=False,
    )

    assert first_report["env"]["created"] is True
    assert second_report["env"]["created"] is False
    assert env_file.read_text(encoding="utf-8") == "INTERNAL_ADMIN_API_KEY=custom-value\n"


def test_local_doctor_can_generate_local_runtime_secrets_without_printing_values(
    tmp_path: Path,
) -> None:
    (tmp_path / ".env.example").write_text(
        "\n".join(
            [
                "INTERNAL_ADMIN_API_KEY=change-this-admin-key",
                "POSTGRES_PASSWORD=change-this-password",
                "MODEL_SECRET_ENCRYPTION_KEY=change-this-32-byte-or-base64-secret",
                "FEISHU_APP_ID=",
                "FEISHU_APP_SECRET=",
            ]
        ),
        encoding="utf-8",
    )

    report = local_doctor.build_local_doctor_report(
        repo_root=tmp_path,
        init_env=True,
        generate_local_secrets=True,
        check_docker=False,
    )
    env_values = local_doctor.load_env_file(tmp_path / ".env")
    report_text = json.dumps(report)

    assert report["env"]["created"] is True
    assert report["env"]["generated_local_secrets"] == [
        "INTERNAL_ADMIN_API_KEY",
        "MODEL_SECRET_ENCRYPTION_KEY",
        "POSTGRES_PASSWORD",
    ]
    for key in [
        "INTERNAL_ADMIN_API_KEY",
        "POSTGRES_PASSWORD",
        "MODEL_SECRET_ENCRYPTION_KEY",
    ]:
        assert env_values[key]
        assert not local_doctor._is_placeholder(key, env_values[key])
        assert env_values[key] not in report_text
    assert env_values["FEISHU_APP_ID"] == ""
    assert env_values["FEISHU_APP_SECRET"] == ""


def test_local_doctor_does_not_overwrite_existing_local_runtime_secrets(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "INTERNAL_ADMIN_API_KEY=admin-custom",
                "POSTGRES_PASSWORD=db-custom",
                "MODEL_SECRET_ENCRYPTION_KEY=model-custom",
            ]
        ),
        encoding="utf-8",
    )

    report = local_doctor.build_local_doctor_report(
        repo_root=tmp_path,
        generate_local_secrets=True,
        check_docker=False,
    )

    assert report["env"]["generated_local_secrets"] == []
    assert local_doctor.load_env_file(env_file) == {
        "INTERNAL_ADMIN_API_KEY": "admin-custom",
        "POSTGRES_PASSWORD": "db-custom",
        "MODEL_SECRET_ENCRYPTION_KEY": "model-custom",
    }


def test_local_doctor_marks_placeholders_and_blank_feishu_values(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "INTERNAL_ADMIN_API_KEY=change-this-admin-key",
                "POSTGRES_PASSWORD=real-db-password",
                "MODEL_SECRET_ENCRYPTION_KEY=change-this-32-byte-or-base64-secret",
                "FEISHU_APP_ID=cli_test",
                "FEISHU_APP_SECRET=",
                "FEISHU_VERIFICATION_TOKEN=verify-token",
                "FEISHU_ENCRYPT_KEY=encrypt-key",
                "FEISHU_DEFAULT_CHAT_ID=oc_test",
                "FEISHU_BITABLE_APP_TOKEN=app_token",
                "FEISHU_BITABLE_REQUISITION_TABLE_ID=tbl_req",
                "FEISHU_BITABLE_CANDIDATE_TABLE_ID=tbl_cand",
                "FEISHU_BITABLE_TALENT_MAP_TABLE_ID=tbl_map",
                "FEISHU_BITABLE_REPORT_TABLE_ID=tbl_report",
            ]
        ),
        encoding="utf-8",
    )

    report = local_doctor.build_local_doctor_report(
        repo_root=tmp_path,
        check_docker=False,
    )

    assert report["status"] == "not_ready"
    assert report["env"]["runtime_required"]["placeholder"] == [
        "INTERNAL_ADMIN_API_KEY",
        "MODEL_SECRET_ENCRYPTION_KEY",
    ]
    assert report["env"]["feishu_required"]["placeholder"] == ["FEISHU_APP_SECRET"]


def test_local_doctor_tells_user_to_fill_feishu_before_strict_when_only_feishu_blocks(
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "INTERNAL_ADMIN_API_KEY=admin-key",
                "POSTGRES_PASSWORD=db-password",
                "MODEL_SECRET_ENCRYPTION_KEY=model-key",
                "FEISHU_APP_ID=",
                "FEISHU_APP_SECRET=",
                "FEISHU_VERIFICATION_TOKEN=",
                "FEISHU_ENCRYPT_KEY=",
                "FEISHU_DEFAULT_CHAT_ID=",
                "FEISHU_BITABLE_APP_TOKEN=",
                "FEISHU_BITABLE_REQUISITION_TABLE_ID=",
                "FEISHU_BITABLE_CANDIDATE_TABLE_ID=",
                "FEISHU_BITABLE_TALENT_MAP_TABLE_ID=",
                "FEISHU_BITABLE_REPORT_TABLE_ID=",
            ]
        ),
        encoding="utf-8",
    )

    report = local_doctor.build_local_doctor_report(
        repo_root=tmp_path,
        check_docker=False,
    )

    assert report["status"] == "not_ready"
    assert report["next_steps"][0].startswith("Before running strict preflight again")
    assert "飞书接入操作手册.md" in report["next_steps"][0]
    assert report["next_steps"][1] == "Run `python -m app.runtime.local_doctor --strict` again."
    assert report["next_steps"][2] == "After `status` is `ok`, run `docker compose up -d --build`."


def test_local_doctor_ok_when_required_values_and_docker_are_available(tmp_path: Path) -> None:
    _write_ready_env(tmp_path / ".env")

    report = local_doctor.build_local_doctor_report(
        repo_root=tmp_path,
        docker_runner=_successful_docker_runner,
        which=lambda command: "C:\\Docker\\docker.exe" if command == "docker" else None,
    )

    assert report["status"] == "ok"
    assert report["blocking_issues"] == []
    assert report["docker"]["compose_available"] is True
    assert report["commands"]["start"] == "docker compose up -d --build"
    assert report["commands"]["docker_compose_start"] == "docker compose up -d --build"
    assert report["next_steps"][0] == "Run `docker compose up -d --build`."


def test_local_doctor_uses_posix_oneclick_commands_on_linux(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(local_doctor.os, "name", "posix")

    report = local_doctor.build_local_doctor_report(
        repo_root=tmp_path,
        check_docker=False,
    )

    assert report["commands"]["start"] == "docker compose up -d --build"
    assert report["commands"]["oneclick_start_windows"] == local_doctor.ONECLICK_START_COMMAND
    assert report["commands"]["oneclick_start_posix"] == local_doctor.POSIX_ONECLICK_START_COMMAND
    assert "bash scripts/lietou-oneclick.sh" in report["next_steps"][0]


def test_local_doctor_strict_exits_when_not_ready(tmp_path: Path, capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        local_doctor.main(["--repo-root", str(tmp_path), "--skip-docker", "--strict"])

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "not_ready"


def _write_ready_env(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "INTERNAL_ADMIN_API_KEY=admin-key",
                "POSTGRES_PASSWORD=db-password",
                "MODEL_SECRET_ENCRYPTION_KEY=model-key",
                "FEISHU_APP_ID=cli_test",
                "FEISHU_APP_SECRET=app-secret",
                "FEISHU_VERIFICATION_TOKEN=verify-token",
                "FEISHU_ENCRYPT_KEY=encrypt-key",
                "FEISHU_DEFAULT_CHAT_ID=oc_test",
                "FEISHU_BITABLE_APP_TOKEN=app_token",
                "FEISHU_BITABLE_REQUISITION_TABLE_ID=tbl_req",
                "FEISHU_BITABLE_CANDIDATE_TABLE_ID=tbl_cand",
                "FEISHU_BITABLE_TALENT_MAP_TABLE_ID=tbl_map",
                "FEISHU_BITABLE_REPORT_TABLE_ID=tbl_report",
                "EMBEDDING_API_KEY=embedding-key",
            ]
        ),
        encoding="utf-8",
    )


def _successful_docker_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=args[0],
        returncode=0,
        stdout="Docker Compose version v2.31.0\n",
        stderr="",
    )
