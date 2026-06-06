from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.approvals import get_approval_service
from app.core.config import Settings
from app.main import create_app
from app.runtime.approvals import (
    ApprovalConflictError,
    ApprovalNotFoundError,
    ApprovalService,
)
from app.schemas.approval import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalDetailResponse,
)
from app.storage.models import ArtifactBlob

ADMIN_HEADERS = {"X-Internal-Admin-Key": "test-admin"}


class FakeApprovalService:
    def __init__(self, *, missing: bool = False, conflict: bool = False):
        self.missing = missing
        self.conflict = conflict

    def get_approval(self, interrupt_id):
        if self.missing:
            raise ApprovalNotFoundError("missing")
        return ApprovalDetailResponse(
            action_id=uuid4(),
            interrupt_id=interrupt_id,
            thread_id=uuid4(),
            action_type="talent_map_write",
            payload_summary="准备写入人才地图草稿",
            payload_ref="artifact://proposal/1",
            idempotency_key="action-idem-1",
            status="pending",
            can_decide=True,
        )

    def submit_decision(self, interrupt_id, request):
        if self.missing:
            raise ApprovalNotFoundError("missing")
        if self.conflict:
            raise ApprovalConflictError("conflict")
        return ApprovalDecisionResponse(
            status="queued",
            action_id=uuid4(),
            interrupt_id=interrupt_id,
            thread_id=uuid4(),
            decision=request.decision,
            idempotency_key="action-idem-1",
            outbox_payload_ref="artifact://outbox/resume",
            next_actions=["已写入 durable resume outbox。"],
        )


class FakeScalars:
    def __init__(self, values):
        self.values = list(values)

    def first(self):
        return self.values[0] if self.values else None


class FakeExecuteResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return FakeScalars(self.values)


class FakeArtifactBlob:
    def __init__(self, payload):
        self.content_json = payload


class FakeApprovalSession:
    def __init__(self, execute_results, *, payloads=None):
        self.execute_results = list(execute_results)
        self.payloads = payloads or {}
        self.executed = []
        self.began = 0

    def begin(self):
        return self

    def __enter__(self):
        self.began += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, statement):
        self.executed.append(statement)
        values = self.execute_results.pop(0) if self.execute_results else []
        return FakeExecuteResult(values)

    def get(self, model, key):
        if model is ArtifactBlob and key in self.payloads:
            return FakeArtifactBlob(self.payloads[key])
        return None


class FakeOutboxWriter:
    def __init__(self):
        self.enqueued = []

    def enqueue_json(self, **kwargs):
        self.enqueued.append(kwargs)
        return "artifact://outbox/resume"


class FakeProposal:
    def __init__(self, *, status: str = "pending"):
        self.id = uuid4()
        self.thread_id = uuid4()
        self.interrupt_id = uuid4()
        self.action_type = "talent_map_write"
        self.payload_summary = "准备写入人才地图草稿"
        self.payload_ref = "artifact://proposal/1"
        self.idempotency_key = "action-idem-1"
        self.status = status
        self.created_at = datetime(2026, 6, 4, tzinfo=UTC)
        self.updated_at = datetime(2026, 6, 4, tzinfo=UTC)


class FakeHumanApproval:
    def __init__(self, *, decision: str, edited_payload_ref: str | None = None):
        self.decision = decision
        self.edited_payload_ref = edited_payload_ref


def test_get_approval_endpoint_returns_pending_action_summary() -> None:
    app = create_app(settings=Settings(internal_admin_api_key="test-admin"))
    app.dependency_overrides[get_approval_service] = lambda: FakeApprovalService()
    client = TestClient(app)
    interrupt_id = uuid4()

    response = client.get(f"/approvals/{interrupt_id}", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["interrupt_id"] == str(interrupt_id)
    assert body["status"] == "pending"
    assert body["can_decide"] is True
    assert "payload" not in body


def test_submit_approval_decision_endpoint_queues_resume() -> None:
    app = create_app(settings=Settings(internal_admin_api_key="test-admin"))
    app.dependency_overrides[get_approval_service] = lambda: FakeApprovalService()
    client = TestClient(app)

    response = client.post(
        f"/approvals/{uuid4()}/decision",
        json={"decision": "approve", "approver": {"user": "tester"}},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["decision"] == "approve"
    assert body["outbox_payload_ref"] == "artifact://outbox/resume"


def test_approval_endpoints_map_not_found_and_conflict() -> None:
    app = create_app(settings=Settings(internal_admin_api_key="test-admin"))
    app.dependency_overrides[get_approval_service] = lambda: FakeApprovalService(
        missing=True
    )
    client = TestClient(app)

    assert client.get(f"/approvals/{uuid4()}", headers=ADMIN_HEADERS).status_code == 404

    app.dependency_overrides[get_approval_service] = lambda: FakeApprovalService(
        conflict=True
    )
    response = client.post(
        f"/approvals/{uuid4()}/decision",
        json={"decision": "reject"},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 409


def test_edit_decision_requires_edited_payload() -> None:
    app = create_app(settings=Settings(internal_admin_api_key="test-admin"))
    app.dependency_overrides[get_approval_service] = lambda: FakeApprovalService()
    client = TestClient(app)

    response = client.post(
        f"/approvals/{uuid4()}/decision",
        json={"decision": "edit"},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 422


def test_approval_service_reads_summary_and_does_not_fetch_artifact_blob() -> None:
    proposal = FakeProposal()
    session = FakeApprovalSession(execute_results=[[proposal]])

    result = ApprovalService(session).get_approval(proposal.interrupt_id)

    assert result.payload_ref == "artifact://proposal/1"
    assert result.can_decide is True
    assert session.began == 0


def test_approval_service_queues_resume_without_executing_business_side_effect() -> None:
    proposal = FakeProposal()
    session = FakeApprovalSession(execute_results=[[proposal], []])
    outbox = FakeOutboxWriter()

    result = ApprovalService(session, outbox_writer=outbox).submit_decision(
        proposal.interrupt_id,
        ApprovalDecisionRequest(
            decision="approve",
            approver={"user": "tester"},
        ),
    )

    assert result.status == "queued"
    assert proposal.status == "pending"
    assert outbox.enqueued[0]["kind"] == "resume"
    assert outbox.enqueued[0]["idempotency_key"] == "resume:action-idem-1"
    human_approval = outbox.enqueued[0]["payload"]["human_approval"]
    assert human_approval["thread_id"] == str(proposal.thread_id)
    assert human_approval["action_id"] == str(proposal.id)
    assert human_approval["approver"] == {"user": "tester", "source": "internal"}
    assert any("human_approvals" in str(statement) for statement in session.executed)


def test_approval_service_queues_edit_payload_for_resume() -> None:
    proposal = FakeProposal()
    session = FakeApprovalSession(execute_results=[[proposal], []])
    outbox = FakeOutboxWriter()

    result = ApprovalService(session, outbox_writer=outbox).submit_decision(
        proposal.interrupt_id,
        ApprovalDecisionRequest(
            decision="edit",
            edited_payload={"note": "人工修正"},
        ),
    )

    assert result.status == "queued"
    assert result.decision == "edit"
    human_approval = outbox.enqueued[0]["payload"]["human_approval"]
    assert human_approval["edited_payload"] == {"note": "人工修正"}
    approval_insert_params = session.executed[-1].compile().params
    assert approval_insert_params["edited_payload_ref"] == "artifact://outbox/resume"


def test_approval_service_rejects_duplicate_edit_with_different_payload() -> None:
    proposal = FakeProposal()
    existing_ref = "artifact://outbox/existing-edit"
    outbox = FakeOutboxWriter()

    duplicate = ApprovalService(
        FakeApprovalSession(
            execute_results=[
                [proposal],
                [FakeHumanApproval(decision="edit", edited_payload_ref=existing_ref)],
            ],
            payloads={
                existing_ref: {
                    "human_approval": {"edited_payload": {"note": "人工修正 A"}}
                }
            },
        ),
        outbox_writer=outbox,
    ).submit_decision(
        proposal.interrupt_id,
        ApprovalDecisionRequest(
            decision="edit",
            edited_payload={"note": "人工修正 A"},
        ),
    )

    assert duplicate.status == "duplicate"
    assert outbox.enqueued == []

    with pytest.raises(ApprovalConflictError, match="different edited_payload"):
        ApprovalService(
            FakeApprovalSession(
                execute_results=[
                    [proposal],
                    [FakeHumanApproval(decision="edit", edited_payload_ref=existing_ref)],
                ],
                payloads={
                    existing_ref: {
                        "human_approval": {"edited_payload": {"note": "人工修正 A"}}
                    }
                },
            ),
            outbox_writer=outbox,
        ).submit_decision(
            proposal.interrupt_id,
            ApprovalDecisionRequest(
                decision="edit",
                edited_payload={"note": "人工修正 B"},
            ),
        )


def test_approval_service_does_not_requeue_already_handled_proposal() -> None:
    proposal = FakeProposal(status="approved")
    session = FakeApprovalSession(execute_results=[[proposal]])
    outbox = FakeOutboxWriter()

    result = ApprovalService(session, outbox_writer=outbox).submit_decision(
        proposal.interrupt_id,
        ApprovalDecisionRequest(decision="approve"),
    )

    assert result.status == "already_approved"
    assert outbox.enqueued == []


def test_approval_service_dedupes_same_decision_and_rejects_conflicting_decision() -> None:
    proposal = FakeProposal()
    outbox = FakeOutboxWriter()

    duplicate = ApprovalService(
        FakeApprovalSession(execute_results=[[proposal], [FakeHumanApproval(decision="reject")]]),
        outbox_writer=outbox,
    ).submit_decision(
        proposal.interrupt_id,
        ApprovalDecisionRequest(decision="reject"),
    )

    assert duplicate.status == "duplicate"
    assert outbox.enqueued == []

    with pytest.raises(ApprovalConflictError):
        ApprovalService(
            FakeApprovalSession(
                execute_results=[[proposal], [FakeHumanApproval(decision="approve")]]
            ),
            outbox_writer=outbox,
        ).submit_decision(
            proposal.interrupt_id,
            ApprovalDecisionRequest(decision="reject"),
        )
