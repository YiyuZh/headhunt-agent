import argparse
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Literal

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from app.core.config import Settings, get_settings
from app.runtime.graph_factory import RuntimeGraphFactory

SmokeStatus = Literal["ok", "failed", "skipped"]
ReportStatus = Literal["ok", "failed", "partial"]


@dataclass(frozen=True)
class SmokeCheck:
    name: str
    status: SmokeStatus
    message: str


@dataclass(frozen=True)
class PostgresSmokeReport:
    status: ReportStatus
    checks: list[SmokeCheck]


EngineFactory = Callable[[str], object]
CheckpointerProbe = Callable[[Settings], None]


def run_postgres_smoke_check(
    *,
    settings: Settings | None = None,
    engine_factory: EngineFactory | None = None,
    checkpointer_probe: CheckpointerProbe | None = None,
    run_checkpointer_setup: bool = True,
) -> PostgresSmokeReport:
    resolved_settings = settings or get_settings()
    checks: list[SmokeCheck] = []

    if not resolved_settings.database_url.startswith("postgresql+psycopg://"):
        checks.append(
            SmokeCheck(
                name="database_url",
                status="failed",
                message="DATABASE_URL must use postgresql+psycopg:// for smoke checks.",
            )
        )
        checks.extend(_skipped_database_checks())
        return _report(checks)

    engine = None
    try:
        engine = (engine_factory or _create_engine)(resolved_settings.database_url)
        with engine.connect() as connection:
            _scalar(connection, "SELECT 1")
            checks.append(
                SmokeCheck(
                    name="database_connect",
                    status="ok",
                    message="Connected to PostgreSQL and executed SELECT 1.",
                )
            )
            checks.append(_check_pgvector(connection))
            checks.append(_check_alembic_head(connection))
    except Exception as exc:
        checks.append(
            SmokeCheck(
                name="database_connect",
                status="failed",
                message=f"PostgreSQL smoke check failed: {exc}",
            )
        )
        checks.extend(_skipped_database_checks())
        return _report(checks)
    finally:
        if engine is not None:
            dispose = getattr(engine, "dispose", None)
            if dispose:
                dispose()

    if run_checkpointer_setup:
        checks.append(
            _check_checkpointer_setup(
                resolved_settings,
                checkpointer_probe or _default_checkpointer_probe,
            )
        )
    else:
        checks.append(
            SmokeCheck(
                name="checkpointer_setup",
                status="skipped",
                message="LangGraph checkpointer setup skipped by CLI flag.",
            )
        )

    return _report(checks)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run PostgreSQL, pgvector, Alembic, and LangGraph checkpointer smoke checks."
    )
    parser.add_argument(
        "--skip-checkpointer-setup",
        action="store_true",
        help="Skip LangGraph PostgresSaver.setup(); useful when only checking database access.",
    )
    args = parser.parse_args(argv)

    report = run_postgres_smoke_check(
        run_checkpointer_setup=not args.skip_checkpointer_setup,
    )
    print(_report_json(report))
    if report.status == "failed":
        raise SystemExit(1)


def _create_engine(database_url: str):
    return create_engine(database_url, pool_pre_ping=True)


def _scalar(connection, sql: str):
    return connection.execute(text(sql)).scalar()


def _check_pgvector(connection) -> SmokeCheck:
    extname = _scalar(
        connection,
        "SELECT extname FROM pg_extension WHERE extname = 'vector'",
    )
    if extname == "vector":
        return SmokeCheck(
            name="pgvector_extension",
            status="ok",
            message="pgvector extension is installed.",
        )
    return SmokeCheck(
        name="pgvector_extension",
        status="failed",
        message="pgvector extension is not installed; run Alembic migration or create extension.",
    )


def _check_alembic_head(connection) -> SmokeCheck:
    current = _scalar(connection, "SELECT version_num FROM alembic_version LIMIT 1")
    expected = _expected_alembic_head()
    if current == expected:
        return SmokeCheck(
            name="alembic_head",
            status="ok",
            message=f"Alembic is at expected head {expected}.",
        )
    return SmokeCheck(
        name="alembic_head",
        status="failed",
        message=f"Alembic version is {current!r}; expected {expected!r}.",
    )


def _check_checkpointer_setup(
    settings: Settings,
    checkpointer_probe: CheckpointerProbe,
) -> SmokeCheck:
    if not settings.checkpoint_db_url.startswith("postgresql+psycopg://"):
        return SmokeCheck(
            name="checkpointer_setup",
            status="failed",
            message="CHECKPOINT_DB_URL must use postgresql+psycopg://.",
        )
    try:
        checkpointer_probe(settings)
    except Exception as exc:
        return SmokeCheck(
            name="checkpointer_setup",
            status="failed",
            message=f"LangGraph checkpointer setup failed: {exc}",
        )
    return SmokeCheck(
        name="checkpointer_setup",
        status="ok",
        message="LangGraph PostgresSaver.setup() completed.",
    )


def _default_checkpointer_probe(settings: Settings) -> None:
    with RuntimeGraphFactory(settings=settings).checkpointer():
        return


def _expected_alembic_head() -> str:
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    if len(heads) != 1:
        return ",".join(sorted(heads))
    return heads[0]


def _skipped_database_checks() -> list[SmokeCheck]:
    return [
        SmokeCheck(
            name="pgvector_extension",
            status="skipped",
            message="Skipped because PostgreSQL connection did not pass.",
        ),
        SmokeCheck(
            name="alembic_head",
            status="skipped",
            message="Skipped because PostgreSQL connection did not pass.",
        ),
        SmokeCheck(
            name="checkpointer_setup",
            status="skipped",
            message="Skipped because PostgreSQL connection did not pass.",
        ),
    ]


def _report(checks: list[SmokeCheck]) -> PostgresSmokeReport:
    statuses = {check.status for check in checks}
    if "failed" in statuses:
        status: ReportStatus = "failed"
    elif "skipped" in statuses:
        status = "partial"
    else:
        status = "ok"
    return PostgresSmokeReport(status=status, checks=checks)


def _report_json(report: PostgresSmokeReport) -> str:
    return json.dumps(asdict(report), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
