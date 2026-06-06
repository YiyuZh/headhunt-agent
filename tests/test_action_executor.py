from uuid import uuid4

import pytest

from app.core.config import Settings
from app.runtime.action_executor import ActionExecutionError, ActionExecutor
from app.storage.models import ActionProposal, ArtifactBlob


class FakeProposal:
    def __init__(self):
        self.id = uuid4()
        self.thread_id = uuid4()
        self.interrupt_id = uuid4()
        self.idempotency_key = "action-idem-1"
        self.action_type = "talent_map_write"
        self.payload_ref = "artifact://proposal/payload"
        self.status = "pending"


class FakeBlob:
    def __init__(self, payload):
        self.content_json = payload


class FakeSession:
    def __init__(self, proposal, payload):
        self.proposal = proposal
        self.payload = payload
        self.executed = []
        self.flush_count = 0

    def get(self, model, key):
        if model is ActionProposal and key == self.proposal.id:
            return self.proposal
        if model is ArtifactBlob and key == self.proposal.payload_ref:
            return FakeBlob(self.payload)
        return None

    def execute(self, statement):
        self.executed.append(statement)

    def flush(self):
        self.flush_count += 1


class FakeOutboxWriter:
    def __init__(self):
        self.enqueued = []

    def enqueue_json(self, **kwargs):
        self.enqueued.append(kwargs)
        return "artifact://outbox/bitable"


def settings() -> Settings:
    return Settings(
        feishu_bitable_app_token="app_token_1",
        feishu_bitable_talent_map_table_id="tbl_talent",
    )


def proposal_payload() -> dict:
    return {
        "payload": {
            "business_kind": "TalentMapDraft",
            "record_fields": {"summary": "人才地图草稿"},
            "entity_refs": [{"entity_type": "TalentMapDraft", "entity_id": str(uuid4())}],
            "client_token": str(uuid4()),
        }
    }


def approval(proposal: FakeProposal, *, decision: str = "approve") -> dict:
    return {
        "thread_id": str(proposal.thread_id),
        "action_id": str(proposal.id),
        "interrupt_id": str(proposal.interrupt_id),
        "idempotency_key": proposal.idempotency_key,
        "decision": decision,
        "approver": {"open_id": "ou_1"},
    }


def test_action_executor_queues_bitable_write_only_after_approval() -> None:
    proposal = FakeProposal()
    outbox = FakeOutboxWriter()
    executor = ActionExecutor(
        FakeSession(proposal, proposal_payload()),
        settings=settings(),
        outbox_writer=outbox,
    )

    result = executor.execute(approval(proposal))

    assert result.status == "queued"
    assert result.outbox_payload_ref == "artifact://outbox/bitable"
    assert outbox.enqueued[0]["kind"] == "bitable_write"
    assert outbox.enqueued[0]["thread_id"] == proposal.thread_id
    payload = outbox.enqueued[0]["payload"]
    assert payload["app_token"] == "app_token_1"
    assert payload["table_id"] == "tbl_talent"
    assert payload["records"] == [{"fields": {"summary": "人才地图草稿"}}]
    assert payload["action_id"] == str(proposal.id)


def test_action_executor_reject_does_not_enqueue_business_side_effect() -> None:
    proposal = FakeProposal()
    outbox = FakeOutboxWriter()
    executor = ActionExecutor(
        FakeSession(proposal, proposal_payload()),
        settings=settings(),
        outbox_writer=outbox,
    )

    result = executor.execute(approval(proposal, decision="reject"))

    assert result.status == "rejected"
    assert outbox.enqueued == []


def test_action_executor_rejects_edit_without_edited_payload() -> None:
    proposal = FakeProposal()
    outbox = FakeOutboxWriter()
    executor = ActionExecutor(
        FakeSession(proposal, proposal_payload()),
        settings=settings(),
        outbox_writer=outbox,
    )

    with pytest.raises(ActionExecutionError, match="edit requires edited_payload"):
        executor.execute(approval(proposal, decision="edit"))

    assert outbox.enqueued == []


def test_action_executor_rejects_mismatched_interrupt() -> None:
    proposal = FakeProposal()
    executor = ActionExecutor(
        FakeSession(proposal, proposal_payload()),
        settings=settings(),
        outbox_writer=FakeOutboxWriter(),
    )
    bad_approval = approval(proposal)
    bad_approval["interrupt_id"] = str(uuid4())

    with pytest.raises(ActionExecutionError, match="interrupt_id"):
        executor.execute(bad_approval)
