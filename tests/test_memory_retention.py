from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.memory.retention import MemoryRetentionService, MemoryRetentionSummary
from app.runtime import memory_retention as runtime_memory_retention
from app.storage.database import get_session

ADMIN_HEADERS = {"X-Internal-Admin-Key": "test-admin"}


class FakeExecuteResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class FakeSession:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.statements = []
        self.flush_count = 0
        self.begin_count = 0

    def execute(self, statement):
        self.statements.append(statement)
        return FakeExecuteResult(self.rows)

    def flush(self):
        self.flush_count += 1

    def begin(self):
        self.begin_count += 1
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeRetentionService:
    def __init__(self):
        self.calls = []

    def run(self, *, now=None, dry_run=False):
        self.calls.append({"now": now, "dry_run": dry_run})
        return MemoryRetentionSummary(
            status="ok",
            scanned_count=3,
            default_expiry_count=1,
            expired_count=2,
            permanent_count=0,
            skipped_count=0,
            dry_run=dry_run,
            now=now or datetime(2026, 6, 8, tzinfo=UTC),
        )


def test_memory_retention_defaults_run_and_long_term_windows() -> None:
    created_at = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    run_memory = _memory(scope="RunMemory", status="active", created_at=created_at)
    project_memory = _memory(
        scope="ProjectMemory",
        status="pending_review",
        created_at=created_at,
    )
    session = FakeSession([run_memory, project_memory])

    summary = MemoryRetentionService(session).run(
        now=datetime(2026, 1, 2, tzinfo=UTC),
    )

    assert summary.scanned_count == 2
    assert summary.default_expiry_count == 2
    assert summary.expired_count == 0
    assert run_memory.expires_at == created_at + timedelta(days=30)
    assert project_memory.expires_at == created_at + timedelta(days=90)
    assert session.flush_count == 1


def test_memory_retention_expires_due_active_and_pending_memory() -> None:
    now = datetime(2026, 6, 8, 12, 0, tzinfo=UTC)
    active = _memory(
        scope="RunMemory",
        status="active",
        expires_at=now - timedelta(seconds=1),
    )
    pending = _memory(
        scope="ProjectMemory",
        status="pending_review",
        expires_at=now,
    )
    session = FakeSession([active, pending])

    summary = MemoryRetentionService(session).run(now=now)

    assert summary.expired_count == 2
    assert active.status == "expired"
    assert pending.status == "expired"
    assert active.updated_at == now
    assert pending.updated_at == now


def test_memory_retention_keeps_permanent_memory_active_even_with_past_expiry() -> None:
    now = datetime(2026, 6, 8, tzinfo=UTC)
    memory = _memory(
        scope="ProjectMemory",
        status="active",
        expires_at=now - timedelta(days=1),
        metadata_={"retention_policy": "permanent"},
    )

    summary = MemoryRetentionService(FakeSession([memory])).run(now=now)

    assert summary.permanent_count == 1
    assert summary.expired_count == 0
    assert memory.status == "active"
    assert memory.expires_at is None


def test_memory_retention_dry_run_reports_without_mutating_or_flushing() -> None:
    now = datetime(2026, 3, 1, tzinfo=UTC)
    memory = _memory(
        scope="RunMemory",
        status="active",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    session = FakeSession([memory])

    summary = MemoryRetentionService(session).run(now=now, dry_run=True)

    assert summary.dry_run is True
    assert summary.default_expiry_count == 1
    assert summary.expired_count == 1
    assert memory.status == "active"
    assert memory.expires_at is None
    assert session.flush_count == 0


def test_memory_retention_endpoint_runs_service_with_internal_admin_key() -> None:
    app = create_app(settings=Settings(internal_admin_api_key="test-admin"))
    memory = _memory(scope="RunMemory", status="active")
    session = FakeSession([memory])
    app.dependency_overrides[get_session] = lambda: session
    client = TestClient(app)

    response = client.post(
        "/memory/retention/run",
        json={"dry_run": False, "now": "2026-01-02T00:00:00+00:00"},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["default_expiry_count"] == 1
    assert body["expired_count"] == 0
    assert session.begin_count == 1
    assert session.flush_count == 1
    assert memory.expires_at == datetime(2026, 1, 31, tzinfo=UTC)


def test_memory_retention_endpoint_requires_internal_admin_key() -> None:
    app = create_app(settings=Settings(internal_admin_api_key="test-admin"))
    app.dependency_overrides[get_session] = lambda: FakeSession()
    client = TestClient(app)

    response = client.post("/memory/retention/run", json={"dry_run": True})

    assert response.status_code == 403


def test_memory_retention_runtime_uses_session_transaction(monkeypatch) -> None:
    session = FakeSession([_memory(scope="RunMemory", status="active")])

    class FakeSessionLocal:
        def __call__(self):
            return session

    monkeypatch.setattr(runtime_memory_retention, "SessionLocal", FakeSessionLocal())

    summary = runtime_memory_retention.run_memory_retention_once(
        dry_run=True,
        now=datetime(2026, 6, 8, tzinfo=UTC),
    )

    assert summary.status == "ok"
    assert session.begin_count == 1
    assert session.flush_count == 0


def _memory(
    *,
    scope: str,
    status: str,
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
    metadata_=None,
):
    return SimpleNamespace(
        scope=scope,
        status=status,
        created_at=created_at or datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=None,
        expires_at=expires_at,
        metadata_=metadata_ or {},
    )
