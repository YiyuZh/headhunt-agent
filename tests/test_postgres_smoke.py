import json

import pytest

from app.core.config import Settings
from app.runtime import postgres_smoke
from app.runtime.postgres_smoke import run_postgres_smoke_check


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


class FakeConnection:
    def __init__(self, *, alembic_version="20260610_0005", vector_installed=True):
        self.alembic_version = alembic_version
        self.vector_installed = vector_installed

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, statement):
        sql = str(statement)
        if "SELECT 1" in sql:
            return FakeResult(1)
        if "pg_extension" in sql:
            return FakeResult("vector" if self.vector_installed else None)
        if "alembic_version" in sql:
            return FakeResult(self.alembic_version)
        raise AssertionError(f"Unexpected SQL: {sql}")


class FakeEngine:
    def __init__(self, connection):
        self.connection = connection
        self.disposed = False

    def connect(self):
        return self.connection

    def dispose(self):
        self.disposed = True


def test_postgres_smoke_check_passes_with_database_and_checkpointer_probe() -> None:
    called = []

    report = run_postgres_smoke_check(
        settings=Settings(),
        engine_factory=lambda _url: FakeEngine(FakeConnection()),
        checkpointer_probe=lambda _settings: called.append("setup"),
    )

    assert report.status == "ok"
    assert [check.name for check in report.checks] == [
        "database_connect",
        "pgvector_extension",
        "alembic_head",
        "checkpointer_setup",
    ]
    assert called == ["setup"]


def test_postgres_smoke_check_fails_on_alembic_mismatch() -> None:
    report = run_postgres_smoke_check(
        settings=Settings(),
        engine_factory=lambda _url: FakeEngine(FakeConnection(alembic_version="old")),
        checkpointer_probe=lambda _settings: None,
    )

    assert report.status == "failed"
    assert any(check.name == "alembic_head" and check.status == "failed" for check in report.checks)


def test_postgres_smoke_check_rejects_non_postgres_url_without_probe() -> None:
    called = []

    report = run_postgres_smoke_check(
        settings=Settings(database_url="sqlite:///tmp.db"),
        engine_factory=lambda _url: FakeEngine(FakeConnection()),
        checkpointer_probe=lambda _settings: called.append("setup"),
    )

    assert report.status == "failed"
    assert called == []
    assert report.checks[0].name == "database_url"


def test_postgres_smoke_cli_exits_nonzero_on_failed_report(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        postgres_smoke,
        "run_postgres_smoke_check",
        lambda **_kwargs: postgres_smoke.PostgresSmokeReport(
            status="failed",
            checks=[
                postgres_smoke.SmokeCheck(
                    name="database_connect",
                    status="failed",
                    message="cannot connect",
                )
            ],
        ),
    )

    with pytest.raises(SystemExit) as exc_info:
        postgres_smoke.main([])

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed"
