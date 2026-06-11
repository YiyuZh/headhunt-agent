import hashlib
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.storage.models import ActionProposal, ArtifactBlob
from app.storage.repositories import (
    DuplicateCardActionError,
    FeishuCardActionRepository,
    FeishuOutboxWriteRepository,
    InvalidHumanApprovalError,
    OutboxPayloadConflictError,
    PayloadRepository,
    _is_unique_violation,
    build_claimable_outbox_query,
)


def test_claimable_outbox_query_recovers_expired_claims() -> None:
    query = build_claimable_outbox_query(datetime(2026, 6, 2, tzinfo=UTC))

    sql = str(
        query.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "feishu_outbox.status = 'pending'" in sql
    assert "feishu_outbox.status = 'claimed'" in sql
    assert "feishu_outbox.claim_expires_at IS NOT NULL" in sql
    assert "feishu_outbox.claim_expires_at <=" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql


class FakeDbError:
    def __init__(self, sqlstate: str):
        self.sqlstate = sqlstate


def test_unique_violation_detection_only_matches_postgres_unique_state() -> None:
    unique_error = IntegrityError("insert", {}, FakeDbError("23505"))
    foreign_key_error = IntegrityError("insert", {}, FakeDbError("23503"))

    assert _is_unique_violation(unique_error) is True
    assert _is_unique_violation(foreign_key_error) is False


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeExistingOutbox:
    def __init__(self, payload_ref: str):
        self.payload_ref = payload_ref


class FakeArtifactBlob:
    def __init__(self, sha256: str):
        self.sha256 = sha256


class FakeOutboxSession:
    def __init__(self, *, existing_outbox=None, existing_payload=None):
        self.existing_outbox = existing_outbox
        self.existing_payload = existing_payload
        self.executed = []
        self.flushed = 0

    def execute(self, statement):
        self.executed.append(statement)
        if getattr(statement, "is_select", False):
            return FakeResult(self.existing_outbox)
        return FakeResult(None)

    def get(self, model, key):
        if (
            model is ArtifactBlob
            and self.existing_outbox is not None
            and self.existing_outbox.payload_ref == key
        ):
            return self.existing_payload
        return None

    def flush(self):
        self.flushed += 1


def test_outbox_writer_reuses_existing_payload_for_same_idempotency_key() -> None:
    payload = {"message": "same"}
    existing_ref = "artifact://outbox/existing"
    session = FakeOutboxSession(
        existing_outbox=FakeExistingOutbox(existing_ref),
        existing_payload=FakeArtifactBlob(_payload_hash(payload)),
    )

    result = FeishuOutboxWriteRepository(session).enqueue_json(
        kind="card_send",
        idempotency_key="idem-1",
        payload=payload,
        content_ref="artifact://outbox/new",
    )

    assert result == existing_ref
    assert session.flushed == 0


def test_outbox_writer_rejects_same_idempotency_key_with_different_payload() -> None:
    session = FakeOutboxSession(
        existing_outbox=FakeExistingOutbox("artifact://outbox/existing"),
        existing_payload=FakeArtifactBlob(_payload_hash({"message": "old"})),
    )

    with pytest.raises(OutboxPayloadConflictError, match="different payload"):
        FeishuOutboxWriteRepository(session).enqueue_json(
            kind="card_send",
            idempotency_key="idem-1",
            payload={"message": "new"},
            content_ref="artifact://outbox/new",
        )


class FakePayloadSession:
    def __init__(self, existing_payload=None):
        self.existing_payload = existing_payload
        self.executed = []

    def get(self, model, key):
        if model is ArtifactBlob:
            return self.existing_payload
        return None

    def execute(self, statement):
        self.executed.append(statement)
        return FakeResult(None)


def test_payload_repository_reuses_same_content_ref_with_same_payload_hash() -> None:
    payload = {"message": "same"}
    session = FakePayloadSession(existing_payload=FakeArtifactBlob(_payload_hash(payload)))
    raw_text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    result = PayloadRepository(session).store_json_payload(
        content_ref="artifact://fixed",
        payload=payload,
        raw_text=raw_text,
        sha256=_payload_hash(payload),
    )

    assert result == "artifact://fixed"
    assert session.executed == []


def test_payload_repository_rejects_same_content_ref_with_different_payload_hash() -> None:
    session = FakePayloadSession(existing_payload=FakeArtifactBlob(_payload_hash({"old": True})))

    with pytest.raises(OutboxPayloadConflictError, match="different payload"):
        PayloadRepository(session).store_json_payload(
            content_ref="artifact://fixed",
            payload={"new": True},
            raw_text='{"new":true}',
            sha256=_payload_hash({"new": True}),
        )


class FakeSession:
    def __init__(self, proposal):
        self.proposal = proposal
        self.executed = []

    def get(self, model, key):
        if model is ActionProposal and self.proposal.id == key:
            return self.proposal
        return None

    def execute(self, statement):
        self.executed.append(statement)


class FakeProposal:
    def __init__(self, *, status: str = "pending"):
        from uuid import uuid4

        self.id = uuid4()
        self.thread_id = uuid4()
        self.interrupt_id = uuid4()
        self.idempotency_key = "action-idem-1"
        self.status = status


def test_card_action_repository_requires_matching_pending_action_proposal() -> None:
    proposal = FakeProposal()
    session = FakeSession(proposal)

    FeishuCardActionRepository(session).record_action_and_enqueue_resume(
        event_id="evt_card_1",
        thread_id=proposal.thread_id,
        action_id=proposal.id,
        interrupt_id=proposal.interrupt_id,
        idempotency_key=proposal.idempotency_key,
        open_message_id="om_1",
        open_chat_id="oc_1",
        card_update_token_ref="artifact://callback",
        operator_open_id="ou_1",
        decision="approve",
        edited_payload_ref=None,
        payload_ref="artifact://callback",
        approver={"source": "feishu", "open_id": "ou_1"},
    )

    assert len(session.executed) == 3


def _payload_hash(payload: dict) -> str:
    raw_text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()


def test_card_action_repository_rejects_mismatched_interrupt_id() -> None:
    from uuid import uuid4

    proposal = FakeProposal()
    session = FakeSession(proposal)

    with pytest.raises(InvalidHumanApprovalError, match="interrupt_id"):
        FeishuCardActionRepository(session).record_action_and_enqueue_resume(
            event_id="evt_card_1",
            thread_id=proposal.thread_id,
            action_id=proposal.id,
            interrupt_id=uuid4(),
            idempotency_key=proposal.idempotency_key,
            open_message_id=None,
            open_chat_id=None,
            card_update_token_ref="artifact://callback",
            operator_open_id=None,
            decision="approve",
            edited_payload_ref=None,
            payload_ref="artifact://callback",
            approver={"source": "feishu"},
        )


def test_card_action_repository_rejects_already_handled_action_proposal() -> None:
    proposal = FakeProposal(status="approved")
    session = FakeSession(proposal)

    with pytest.raises(DuplicateCardActionError, match="already approved"):
        FeishuCardActionRepository(session).record_action_and_enqueue_resume(
            event_id="evt_card_1",
            thread_id=proposal.thread_id,
            action_id=proposal.id,
            interrupt_id=proposal.interrupt_id,
            idempotency_key=proposal.idempotency_key,
            open_message_id=None,
            open_chat_id=None,
            card_update_token_ref="artifact://callback",
            operator_open_id=None,
            decision="approve",
            edited_payload_ref=None,
            payload_ref="artifact://callback",
            approver={"source": "feishu"},
        )
