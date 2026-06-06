from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.inspection import get_inspection_service
from app.core.config import Settings
from app.main import create_app
from app.runtime.inspection import InspectionNotFoundError, InspectionService
from app.schemas.inspection import AgentRunInspectionResponse, ThreadInspectionResponse
from app.storage.models import AgentRun, ArtifactBlob, GraphThread

ADMIN_HEADERS = {"X-Internal-Admin-Key": "test-admin"}


class FakeInspectionService:
    def __init__(self, *, missing: bool = False):
        self.missing = missing

    def get_thread(self, thread_id):
        if self.missing:
            raise InspectionNotFoundError("missing")
        return ThreadInspectionResponse(
            thread_id=thread_id,
            source="manual",
            source_ref="manual-1",
            task_type="talent_mapping",
            council_mode="full_council",
            mode_reason="用户明确要求三省六部或完整会审",
            status="queued",
            state_summary={"authorization_status": "authorized"},
            artifact_refs=[],
            memory_refs=[],
            pending_interrupts=[],
            recent_runs=[],
        )

    def get_run(self, run_id):
        if self.missing:
            raise InspectionNotFoundError("missing")
        return AgentRunInspectionResponse(
            run_id=run_id,
            thread_id=uuid4(),
            node_name="strategy",
            agent_name="StrategyDraftAgent",
            council_mode="lite",
            status="succeeded",
            context_pack_ref="artifact://context/1",
            memory_refs=[],
            artifact_refs=[],
            source_refs=[],
        )


class FakeScalarResult:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class FakeExecuteResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return FakeScalarResult(self.values)


class FakeInspectionSession:
    def __init__(self, *, thread=None, run=None, execute_results=None):
        self.thread = thread
        self.run = run
        self.execute_results = list(execute_results or [])
        self.get_calls = []

    def get(self, model, key):
        self.get_calls.append((model, key))
        if model is GraphThread:
            return self.thread
        if model is AgentRun:
            return self.run
        if model is ArtifactBlob:
            raise AssertionError("inspection must not read artifact blob content")
        return None

    def execute(self, statement):
        return FakeExecuteResult(self.execute_results.pop(0))


class FakeThread:
    def __init__(self, thread_id):
        self.id = thread_id
        self.source = "manual"
        self.source_ref = "manual-1"
        self.task_type = "talent_mapping"
        self.council_mode = "standard"
        self.mode_reason = "常规猎头任务"
        self.status = "interrupted"
        self.state_summary = {"pending_action_id": "action-1"}


class FakeRun:
    def __init__(self, run_id, thread_id):
        self.id = run_id
        self.thread_id = thread_id
        self.node_name = "strategy"
        self.agent_name = "StrategyDraftAgent"
        self.council_mode = "standard"
        self.context_pack_ref = "artifact://context/1"
        self.input_summary = "岗位摘要"
        self.output_summary = "策略摘要"
        self.memory_refs = [
            {"memory_id": "mem-1", "summary": "历史经验"},
            {"memory_id": "mem-1", "summary": "历史经验"},
        ]
        self.artifact_refs = [{"content_ref": "artifact://strategy/1"}]
        self.source_refs = [{"source": "manual"}]
        self.token_estimate = 1200
        self.status = "succeeded"
        self.error = None
        self.started_at = datetime(2026, 6, 4, tzinfo=UTC)
        self.ended_at = datetime(2026, 6, 4, 0, 1, tzinfo=UTC)


class FakeArtifact:
    def __init__(self, artifact_id, run_id, thread_id):
        self.id = artifact_id
        self.thread_id = thread_id
        self.run_id = run_id
        self.kind = "strategy_brief"
        self.summary = "策略摘要"
        self.content_ref = "artifact://strategy/1"
        self.evidence_refs = []
        self.source_refs = [{"source": "manual"}]
        self.pii_level = "none"
        self.version = 1
        self.size_tokens_estimate = 300
        self.created_at = datetime(2026, 6, 4, tzinfo=UTC)


class FakeAction:
    def __init__(self, thread_id):
        self.id = uuid4()
        self.thread_id = thread_id
        self.interrupt_id = uuid4()
        self.action_type = "talent_map_write"
        self.payload_summary = "准备写入人才地图"
        self.payload_ref = "artifact://proposal/1"
        self.idempotency_key = "action-idem-1"
        self.status = "pending"
        self.created_at = datetime(2026, 6, 4, tzinfo=UTC)


def test_thread_inspection_endpoint_returns_summary() -> None:
    app = create_app(settings=Settings(internal_admin_api_key="test-admin"))
    app.dependency_overrides[get_inspection_service] = lambda: FakeInspectionService()
    client = TestClient(app)
    thread_id = uuid4()

    response = client.get(f"/threads/{thread_id}", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["thread_id"] == str(thread_id)
    assert body["council_mode"] == "full_council"
    assert body["status"] == "queued"


def test_thread_inspection_endpoint_returns_404() -> None:
    app = create_app(settings=Settings(internal_admin_api_key="test-admin"))
    app.dependency_overrides[get_inspection_service] = lambda: FakeInspectionService(
        missing=True
    )
    client = TestClient(app)

    response = client.get(f"/threads/{uuid4()}", headers=ADMIN_HEADERS)

    assert response.status_code == 404
    assert response.json()["detail"] == "thread not found"


def test_run_inspection_endpoint_returns_context_pack_ref_only() -> None:
    app = create_app(settings=Settings(internal_admin_api_key="test-admin"))
    app.dependency_overrides[get_inspection_service] = lambda: FakeInspectionService()
    client = TestClient(app)
    run_id = uuid4()

    response = client.get(f"/runs/{run_id}", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == str(run_id)
    assert body["context_pack_ref"] == "artifact://context/1"
    assert "context_pack" not in body


def test_inspection_service_summarizes_thread_without_reading_artifact_content() -> None:
    thread_id = uuid4()
    run_id = uuid4()
    run = FakeRun(run_id, thread_id)
    session = FakeInspectionSession(
        thread=FakeThread(thread_id),
        execute_results=[
            [run],
            [FakeArtifact(uuid4(), run_id, thread_id)],
            [FakeAction(thread_id)],
        ],
    )

    result = InspectionService(session).get_thread(thread_id)

    assert result.thread_id == thread_id
    assert result.status == "interrupted"
    assert len(result.artifact_refs) == 1
    assert result.artifact_refs[0].content_ref == "artifact://strategy/1"
    assert result.memory_refs == [{"memory_id": "mem-1", "summary": "历史经验"}]
    assert result.pending_interrupts[0].payload_ref == "artifact://proposal/1"
    assert all(model is not ArtifactBlob for model, _ in session.get_calls)


def test_inspection_service_returns_run_refs_without_context_content() -> None:
    thread_id = uuid4()
    run_id = uuid4()
    run = FakeRun(run_id, thread_id)

    result = InspectionService(FakeInspectionSession(run=run)).get_run(run_id)

    assert result.run_id == run_id
    assert result.context_pack_ref == "artifact://context/1"
    assert result.memory_refs[0]["memory_id"] == "mem-1"
    assert result.artifact_refs[0]["content_ref"] == "artifact://strategy/1"
