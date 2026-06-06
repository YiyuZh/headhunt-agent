from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.tasks import get_task_authorization_service
from app.core.config import Settings
from app.main import create_app
from app.runtime.task_authorization import (
    TaskAuthorizationConflictError,
    TaskAuthorizationService,
)
from app.schemas.common import CouncilMode
from app.schemas.tasks import TaskAuthorizeResponse

ADMIN_HEADERS = {"X-Internal-Admin-Key": "test-admin"}


class FakeTaskAuthorizationService:
    def authorize(self, request):
        return TaskAuthorizeResponse(
            status="queued",
            task_id=uuid4(),
            thread_id=request.thread_id or uuid4(),
            source_ref=request.source_ref or "request:fake",
            task_type="talent_mapping",
            council_mode=CouncilMode.full_council,
            mode_reason="用户明确要求三省六部或完整会审",
            required_agents=["CandidateJudgementAgent", "CouncilSynthesizerAgent"],
            optional_agents=[],
            user_forced_full_council=True,
            idempotency_key="task_authorize:api:manual-1",
            outbox_payload_ref="artifact://task-authorization/thread/task/v1",
            next_actions=["已写入 durable graph_dispatch outbox。"],
        )


class ConflictingTaskAuthorizationService:
    def authorize(self, request):
        raise TaskAuthorizationConflictError("conflicting payload")


class FakeSession:
    def __init__(self):
        self.executed = []
        self.flushed = 0

    def begin(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, statement):
        self.executed.append(statement)

    def flush(self):
        self.flushed += 1


class FakeOutboxWriter:
    def __init__(self):
        self.enqueued = []

    def enqueue_json(self, **kwargs):
        self.enqueued.append(kwargs)
        return kwargs["content_ref"]


class ConflictingOutboxWriter:
    def enqueue_json(self, **kwargs):
        from app.storage.repositories import OutboxPayloadConflictError

        raise OutboxPayloadConflictError("conflicting payload")


def test_authorize_task_endpoint_returns_queued_plan() -> None:
    app = create_app(settings=Settings(internal_admin_api_key="test-admin"))
    app.dependency_overrides[get_task_authorization_service] = FakeTaskAuthorizationService
    client = TestClient(app)
    thread_id = uuid4()

    response = client.post(
        "/tasks/authorize",
        json={
            "thread_id": str(thread_id),
            "source_ref": "manual-1",
            "request_text": "请用三省六部完整会审 AI 平台负责人岗位并生成人才地图",
            "approved": True,
            "approver": {"user": "tester"},
        },
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["thread_id"] == str(thread_id)
    assert body["council_mode"] == "full_council"
    assert body["user_forced_full_council"] is True


def test_authorize_task_endpoint_requires_internal_admin_key() -> None:
    app = create_app(settings=Settings(internal_admin_api_key="test-admin"))
    app.dependency_overrides[get_task_authorization_service] = FakeTaskAuthorizationService
    client = TestClient(app)

    response = client.post(
        "/tasks/authorize",
        json={
            "source_ref": "manual-1",
            "request_text": "请校准岗位需求",
            "approved": True,
        },
    )

    assert response.status_code == 403


def test_authorize_task_endpoint_maps_conflict_to_409() -> None:
    app = create_app(settings=Settings(internal_admin_api_key="test-admin"))
    app.dependency_overrides[get_task_authorization_service] = (
        ConflictingTaskAuthorizationService
    )
    client = TestClient(app)

    response = client.post(
        "/tasks/authorize",
        json={
            "source_ref": "manual-1",
            "request_text": "请校准岗位需求",
            "approved": True,
        },
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 409


def test_task_authorization_service_enqueues_graph_dispatch_after_approval() -> None:
    session = FakeSession()
    outbox = FakeOutboxWriter()
    thread_id = uuid4()

    result = TaskAuthorizationService(
        session,
        outbox_writer=outbox,
    ).authorize(
        request=_request(
            thread_id=thread_id,
            source_ref="manual-1",
            request_text="请用三省六部完整会审 AI 平台负责人岗位并生成人才地图",
        )
    )

    assert result.status == "queued"
    assert result.thread_id == thread_id
    assert result.council_mode == CouncilMode.full_council
    assert result.source_ref == "manual-1"
    assert result.user_forced_full_council is True
    assert session.executed
    assert outbox.enqueued[0]["kind"] == "graph_dispatch"
    assert outbox.enqueued[0]["thread_id"] == thread_id
    assert outbox.enqueued[0]["idempotency_key"] == "task_authorize:api:manual-1"
    payload = outbox.enqueued[0]["payload"]
    assert payload["thread_id"] == str(thread_id)
    assert payload["source_ref"] == "manual-1"
    assert payload["authorization"]["status"] == "authorized"
    assert payload["council_mode"] == "full_council"


def test_task_authorization_service_uses_stable_generated_source_ref() -> None:
    first = TaskAuthorizationService(
        FakeSession(),
        outbox_writer=FakeOutboxWriter(),
    ).authorize(request=_request())
    second = TaskAuthorizationService(
        FakeSession(),
        outbox_writer=FakeOutboxWriter(),
    ).authorize(request=_request())

    assert first.source_ref.startswith("request:")
    assert first.source_ref == second.source_ref
    assert first.thread_id == second.thread_id
    assert first.task_id == second.task_id
    assert first.idempotency_key == second.idempotency_key


def test_task_authorization_service_rejects_without_side_effects() -> None:
    session = FakeSession()
    outbox = FakeOutboxWriter()

    result = TaskAuthorizationService(session, outbox_writer=outbox).authorize(
        request=_request(approved=False)
    )

    assert result.status == "rejected"
    assert result.idempotency_key is None
    assert outbox.enqueued == []
    assert session.executed == []


def test_task_authorization_request_defaults_to_not_approved() -> None:
    session = FakeSession()
    outbox = FakeOutboxWriter()
    from app.schemas.tasks import TaskAuthorizeRequest

    result = TaskAuthorizationService(session, outbox_writer=outbox).authorize(
        request=TaskAuthorizeRequest(request_text="请校准岗位需求")
    )

    assert result.status == "rejected"
    assert outbox.enqueued == []


def test_task_authorization_service_maps_outbox_conflict() -> None:
    with pytest.raises(TaskAuthorizationConflictError, match="conflicting payload"):
        TaskAuthorizationService(
            FakeSession(),
            outbox_writer=ConflictingOutboxWriter(),
        ).authorize(request=_request(source_ref="manual-1", approved=True))


def _request(
    *,
    thread_id: UUID | None = None,
    source_ref: str | None = None,
    request_text: str = "请校准岗位需求",
    approved: bool = True,
):
    from app.schemas.tasks import TaskAuthorizeRequest

    return TaskAuthorizeRequest(
        request_text=request_text,
        source="api",
        source_ref=source_ref,
        thread_id=thread_id,
        approved=approved,
        approver={"user": "tester"},
    )
