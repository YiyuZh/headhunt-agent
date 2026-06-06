from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.storage.repositories import BitableClientTokenConflictError, BitableSyncRepository


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeSession:
    def __init__(self, *, existing_chunk=None):
        self.statements = []
        self.existing_chunk = existing_chunk
        self.flush_count = 0

    def execute(self, statement):
        if statement.__class__.__name__ == "Select":
            return FakeResult(self.existing_chunk)
        self.statements.append(statement)
        return FakeResult(None)

    def flush(self):
        self.flush_count += 1


def test_bitable_sync_repository_records_chunk_and_record_map() -> None:
    session = FakeSession()
    entity_id = uuid4()

    BitableSyncRepository(session).record_chunk_success(
        client_token=str(uuid4()),
        app_token="app_1",
        table_id="tbl_1",
        record_ids=["rec_1"],
        records=[{"fields": {"name": "A"}}],
        outbox_id=uuid4(),
        action_id=uuid4(),
        entity_refs=[{"entity_type": "requisition", "entity_id": str(entity_id)}],
    )

    assert len(session.statements) == 2
    assert session.flush_count == 1


def test_bitable_sync_repository_records_chunk_without_entity_refs() -> None:
    session = FakeSession()

    BitableSyncRepository(session).record_chunk_success(
        client_token=str(uuid4()),
        app_token="app_1",
        table_id="tbl_1",
        record_ids=["rec_1"],
        records=[{"fields": {"name": "A"}}],
    )

    assert len(session.statements) == 1
    assert session.flush_count == 1


def test_bitable_sync_repository_rejects_record_id_length_mismatch() -> None:
    with pytest.raises(ValueError, match="record_ids length"):
        BitableSyncRepository(FakeSession()).record_chunk_success(
            client_token=str(uuid4()),
            app_token="app_1",
            table_id="tbl_1",
            record_ids=[],
            records=[{"fields": {"name": "A"}}],
        )


def test_bitable_sync_repository_rejects_entity_ref_length_mismatch() -> None:
    with pytest.raises(ValueError, match="entity_refs length"):
        BitableSyncRepository(FakeSession()).record_chunk_success(
            client_token=str(uuid4()),
            app_token="app_1",
            table_id="tbl_1",
            record_ids=["rec_1"],
            records=[{"fields": {"name": "A"}}],
            entity_refs=[
                {"entity_type": "requisition", "entity_id": str(uuid4())},
                {"entity_type": "candidate", "entity_id": str(uuid4())},
            ],
        )


def test_bitable_sync_repository_rejects_client_token_payload_conflict() -> None:
    existing = SimpleNamespace(payload_hash="different", record_ids=["rec_1"])

    with pytest.raises(BitableClientTokenConflictError, match="payload_hash"):
        BitableSyncRepository(FakeSession(existing_chunk=existing)).record_chunk_success(
            client_token=str(uuid4()),
            app_token="app_1",
            table_id="tbl_1",
            record_ids=["rec_1"],
            records=[{"fields": {"name": "A"}}],
            payload_hash="new-hash",
        )


def test_bitable_sync_repository_marks_client_token_conflict_before_raise() -> None:
    existing = SimpleNamespace(payload_hash="different", record_ids=["rec_1"])
    session = FakeSession(existing_chunk=existing)

    with pytest.raises(BitableClientTokenConflictError):
        BitableSyncRepository(session).record_chunk_success(
            client_token=str(uuid4()),
            app_token="app_1",
            table_id="tbl_1",
            record_ids=["rec_1"],
            records=[{"fields": {"name": "A"}}],
            payload_hash="new-hash",
        )

    assert len(session.statements) == 1
    assert session.flush_count == 1
