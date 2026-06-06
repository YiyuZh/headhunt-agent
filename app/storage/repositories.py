import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.storage.models import (
    ActionProposal,
    ArtifactBlob,
    FeishuBitableRecordMap,
    FeishuBitableWriteChunk,
    FeishuCardAction,
    FeishuEventLog,
    FeishuOutbox,
    HumanApproval,
)


class DuplicateEventError(RuntimeError):
    pass


class DuplicateCardActionError(RuntimeError):
    pass


class InvalidHumanApprovalError(RuntimeError):
    pass


class BitableClientTokenConflictError(RuntimeError):
    pass


class BitableActionNotApprovedError(RuntimeError):
    pass


class OutboxPayloadConflictError(RuntimeError):
    pass


class PayloadRepository:
    def __init__(self, session: Session):
        self.session = session

    def store_json_payload(
        self,
        *,
        content_ref: str,
        payload: dict,
        raw_text: str,
        sha256: str,
    ) -> str:
        statement = (
            pg_insert(ArtifactBlob)
            .values(
                content_ref=content_ref,
                media_type="application/json",
                content_json=payload,
                content_text=raw_text,
                sha256=sha256,
            )
            .on_conflict_do_nothing(index_elements=["content_ref"])
        )
        self.session.execute(statement)
        return content_ref

    def get_json_payload(self, content_ref: str) -> dict:
        payload = self.session.get(ArtifactBlob, content_ref)
        if payload is None or not isinstance(payload.content_json, dict):
            raise KeyError(content_ref)
        return payload.content_json


class FeishuOutboxWriteRepository:
    def __init__(self, session: Session):
        self.session = session
        self.payload_repository = PayloadRepository(session)

    def enqueue_json(
        self,
        *,
        kind: str,
        idempotency_key: str,
        payload: dict,
        thread_id: UUID | None = None,
        content_ref: str | None = None,
    ) -> str:
        resolved_content_ref = content_ref or f"artifact://outbox/{_stable_hash(payload)}"
        raw_text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        payload_sha256 = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        existing = self._existing_outbox(idempotency_key)
        if existing is not None:
            self._ensure_existing_payload_matches(
                existing_payload_ref=existing.payload_ref,
                expected_sha256=payload_sha256,
                idempotency_key=idempotency_key,
            )
            return existing.payload_ref

        self.payload_repository.store_json_payload(
            content_ref=resolved_content_ref,
            payload=payload,
            raw_text=raw_text,
            sha256=payload_sha256,
        )
        self.session.execute(
            pg_insert(FeishuOutbox)
            .values(
                kind=kind,
                idempotency_key=idempotency_key,
                thread_id=thread_id,
                payload_ref=resolved_content_ref,
                status="pending",
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
        )
        self.session.flush()
        persisted = self._existing_outbox(idempotency_key)
        if persisted is not None and persisted.payload_ref != resolved_content_ref:
            self._ensure_existing_payload_matches(
                existing_payload_ref=persisted.payload_ref,
                expected_sha256=payload_sha256,
                idempotency_key=idempotency_key,
            )
            return persisted.payload_ref
        return resolved_content_ref

    def _existing_outbox(self, idempotency_key: str) -> FeishuOutbox | None:
        return self.session.execute(
            select(FeishuOutbox).where(FeishuOutbox.idempotency_key == idempotency_key)
        ).scalar_one_or_none()

    def _ensure_existing_payload_matches(
        self,
        *,
        existing_payload_ref: str,
        expected_sha256: str,
        idempotency_key: str,
    ) -> None:
        existing_payload = self.session.get(ArtifactBlob, existing_payload_ref)
        if existing_payload is None or existing_payload.sha256 != expected_sha256:
            raise OutboxPayloadConflictError(
                f"idempotency_key {idempotency_key} already exists with a different payload"
            )


class FeishuEventRepository:
    def __init__(self, session: Session):
        self.session = session

    def record_event_and_enqueue(
        self,
        *,
        event_id: str,
        event_type: str,
        dedupe_key: str,
        idempotency_key: str,
        payload_hash: str,
        payload_ref: str,
        outbox_kind: str,
        tenant_key: str | None = None,
        app_id: str | None = None,
        message_id: str | None = None,
        thread_id: UUID | None = None,
    ) -> None:
        try:
            self.session.execute(
                insert(FeishuEventLog).values(
                    event_id=event_id,
                    event_type=event_type,
                    tenant_key=tenant_key,
                    app_id=app_id,
                    message_id=message_id,
                    dedupe_key=dedupe_key,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    payload_ref=payload_ref,
                    status="queued",
                )
            )
            self.session.execute(
                insert(FeishuOutbox).values(
                    kind=outbox_kind,
                    idempotency_key=idempotency_key,
                    thread_id=thread_id,
                    payload_ref=payload_ref,
                    status="pending",
                )
            )
        except IntegrityError as exc:
            if _is_unique_violation(exc):
                raise DuplicateEventError(str(exc)) from exc
            raise


class FeishuOutboxRepository:
    def __init__(self, session: Session):
        self.session = session

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 60,
        now: datetime | None = None,
    ) -> FeishuOutbox | None:
        current_time = now or datetime.now(UTC)
        lease_until = current_time + timedelta(seconds=lease_seconds)

        candidate = build_claimable_outbox_query(current_time)
        outbox_item = self.session.execute(candidate).scalar_one_or_none()
        if outbox_item is None:
            return None

        self.session.execute(
            update(FeishuOutbox)
            .where(FeishuOutbox.id == outbox_item.id)
            .values(
                status="claimed",
                claimed_by=worker_id,
                claimed_at=current_time,
                claim_expires_at=lease_until,
                attempt_count=FeishuOutbox.attempt_count + 1,
            )
        )
        self.session.flush()
        return outbox_item

    def mark_succeeded(self, outbox_id: UUID) -> None:
        self.session.execute(
            update(FeishuOutbox)
            .where(FeishuOutbox.id == outbox_id)
            .values(
                status="succeeded",
                claimed_by=None,
                claimed_at=None,
                claim_expires_at=None,
                last_error=None,
            )
        )
        self.session.flush()

    def release_for_retry(
        self,
        *,
        outbox_id: UUID,
        next_attempt_at: datetime,
        error: str,
    ) -> None:
        self.session.execute(
            update(FeishuOutbox)
            .where(FeishuOutbox.id == outbox_id)
            .values(
                status="pending",
                next_attempt_at=next_attempt_at,
                claimed_by=None,
                claimed_at=None,
                claim_expires_at=None,
                last_error=error,
            )
        )
        self.session.flush()

    def mark_dead_letter(self, *, outbox_id: UUID, error: str) -> None:
        self.session.execute(
            update(FeishuOutbox)
            .where(FeishuOutbox.id == outbox_id)
            .values(
                status="dead_letter",
                claimed_by=None,
                claimed_at=None,
                claim_expires_at=None,
                last_error=error,
            )
        )
        self.session.flush()

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()


class FeishuCardActionRepository:
    def __init__(self, session: Session):
        self.session = session

    def record_action_and_enqueue_resume(
        self,
        *,
        event_id: str,
        thread_id: UUID,
        action_id: UUID,
        interrupt_id: UUID,
        idempotency_key: str,
        open_message_id: str | None,
        open_chat_id: str | None,
        card_update_token_ref: str,
        operator_open_id: str | None,
        decision: str,
        edited_payload_ref: str | None,
        payload_ref: str,
        approver: dict,
    ) -> None:
        proposal = self.session.get(ActionProposal, action_id)
        if proposal is None:
            raise InvalidHumanApprovalError(f"ActionProposal not found: {action_id}")
        if proposal.thread_id != thread_id:
            raise InvalidHumanApprovalError("Card action thread_id does not match ActionProposal")
        if proposal.interrupt_id != interrupt_id:
            raise InvalidHumanApprovalError(
                "Card action interrupt_id does not match ActionProposal"
            )
        if proposal.idempotency_key != idempotency_key:
            raise InvalidHumanApprovalError(
                "Card action idempotency_key does not match ActionProposal"
            )
        if proposal.status != "pending":
            raise DuplicateCardActionError(
                f"ActionProposal is already {proposal.status}"
            )
        try:
            self.session.execute(
                insert(FeishuCardAction).values(
                    event_id=event_id,
                    thread_id=thread_id,
                    action_id=action_id,
                    interrupt_id=interrupt_id,
                    idempotency_key=idempotency_key,
                    open_message_id=open_message_id,
                    open_chat_id=open_chat_id,
                    card_update_token_ref=card_update_token_ref,
                    operator_open_id=operator_open_id,
                    decision=decision,
                    edited_payload_ref=edited_payload_ref,
                    status="queued",
                )
            )
            self.session.execute(
                insert(HumanApproval).values(
                    interrupt_id=interrupt_id,
                    action_id=action_id,
                    thread_id=thread_id,
                    approver=approver,
                    decision=decision,
                    edited_payload_ref=edited_payload_ref,
                    idempotency_key=idempotency_key,
                )
            )
            self.session.execute(
                insert(FeishuOutbox).values(
                    kind="resume",
                    idempotency_key=f"resume:{idempotency_key}",
                    thread_id=thread_id,
                    payload_ref=payload_ref,
                    status="pending",
                )
            )
        except IntegrityError as exc:
            if _is_unique_violation(exc):
                raise DuplicateCardActionError(str(exc)) from exc
            raise


class BitableSyncRepository:
    def __init__(self, session: Session):
        self.session = session

    def ensure_action_approved(self, action_id: UUID) -> None:
        proposal = self.session.get(ActionProposal, action_id)
        if proposal is None:
            raise BitableActionNotApprovedError(f"ActionProposal not found: {action_id}")
        if proposal.status not in {"approved", "executed"}:
            raise BitableActionNotApprovedError(
                f"ActionProposal is not approved: {proposal.status}"
            )

    def record_chunk_success(
        self,
        *,
        client_token: str,
        app_token: str,
        table_id: str,
        record_ids: list[str],
        records: list[dict],
        outbox_id: UUID | None = None,
        action_id: UUID | None = None,
        chunk_index: int = 0,
        payload_hash: str | None = None,
        entity_refs: list[dict] | None = None,
    ) -> None:
        resolved_payload_hash = payload_hash or _stable_hash(records)
        resolved_entity_refs = entity_refs or []
        _validate_bitable_result_lengths(
            records=records,
            record_ids=record_ids,
            entity_refs=resolved_entity_refs,
        )
        conflict_reason = self._client_token_conflict_reason(
            client_token=client_token,
            payload_hash=resolved_payload_hash,
            record_ids=record_ids,
        )
        if conflict_reason:
            self.record_chunk_failure(
                client_token=client_token,
                app_token=app_token,
                table_id=table_id,
                records=records,
                error=conflict_reason,
                outbox_id=outbox_id,
                action_id=action_id,
                chunk_index=chunk_index,
                payload_hash=resolved_payload_hash,
                status="conflict",
            )
            raise BitableClientTokenConflictError(conflict_reason)
        self.session.execute(
            pg_insert(FeishuBitableWriteChunk)
            .values(
                action_id=action_id,
                outbox_id=outbox_id,
                app_token=app_token,
                table_id=table_id,
                chunk_index=chunk_index,
                payload_hash=resolved_payload_hash,
                client_token=client_token,
                status="succeeded",
                record_ids=record_ids,
                last_error=None,
            )
            .on_conflict_do_update(
                index_elements=["client_token"],
                set_={
                    "status": "succeeded",
                    "payload_hash": resolved_payload_hash,
                    "record_ids": record_ids,
                    "last_error": None,
                    "updated_at": datetime.now(UTC),
                },
            )
        )
        self._upsert_record_maps(
            app_token=app_token,
            table_id=table_id,
            record_ids=record_ids,
            entity_refs=resolved_entity_refs,
        )
        self.session.flush()

    def record_chunk_failure(
        self,
        *,
        client_token: str,
        app_token: str,
        table_id: str,
        records: list[dict],
        error: str,
        outbox_id: UUID | None = None,
        action_id: UUID | None = None,
        chunk_index: int = 0,
        payload_hash: str | None = None,
        status: str = "failed",
    ) -> None:
        resolved_payload_hash = payload_hash or _stable_hash(records)
        self.session.execute(
            pg_insert(FeishuBitableWriteChunk)
            .values(
                action_id=action_id,
                outbox_id=outbox_id,
                app_token=app_token,
                table_id=table_id,
                chunk_index=chunk_index,
                payload_hash=resolved_payload_hash,
                client_token=client_token,
                status=status,
                record_ids=[],
                last_error=error,
            )
            .on_conflict_do_update(
                index_elements=["client_token"],
                set_={
                    "status": status,
                    "last_error": error,
                    "updated_at": datetime.now(UTC),
                },
            )
        )
        self.session.flush()

    def _upsert_record_maps(
        self,
        *,
        app_token: str,
        table_id: str,
        record_ids: list[str],
        entity_refs: list[dict],
    ) -> None:
        if not entity_refs:
            return
        for entity_ref, record_id in zip(entity_refs, record_ids, strict=True):
            entity_type = entity_ref.get("entity_type")
            entity_id = entity_ref.get("entity_id")
            if not isinstance(entity_id, str) or not entity_id:
                raise ValueError("Bitable entity_refs must include entity_id")
            if not isinstance(entity_type, str) or not entity_type:
                raise ValueError("Bitable entity_refs must include entity_type")
            self.session.execute(
                pg_insert(FeishuBitableRecordMap)
                .values(
                    entity_type=entity_type,
                    entity_id=UUID(entity_id),
                    app_token=app_token,
                    table_id=table_id,
                    record_id=record_id,
                    last_sync_status="succeeded",
                    last_sync_at=datetime.now(UTC),
                )
                .on_conflict_do_update(
                    index_elements=["entity_type", "entity_id", "app_token", "table_id"],
                    set_={
                        "record_id": record_id,
                        "last_sync_status": "succeeded",
                        "last_sync_at": datetime.now(UTC),
                    },
                )
            )

    def _client_token_conflict_reason(
        self,
        *,
        client_token: str,
        payload_hash: str,
        record_ids: list[str],
    ) -> str | None:
        existing = self.session.execute(
            select(FeishuBitableWriteChunk).where(
                FeishuBitableWriteChunk.client_token == client_token
            )
        ).scalar_one_or_none()
        if existing is None:
            return None
        if existing.payload_hash != payload_hash:
            return "Bitable client_token already exists with a different payload_hash"
        existing_record_ids = list(existing.record_ids or [])
        if existing_record_ids and existing_record_ids != record_ids:
            return "Bitable client_token already exists with different record_ids"
        return None


def _is_unique_violation(exc: IntegrityError) -> bool:
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == "23505":
        return True
    return "UNIQUE constraint failed" in str(exc.orig)


def _stable_hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _validate_bitable_result_lengths(
    *,
    records: list[dict],
    record_ids: list[str],
    entity_refs: list[dict],
) -> None:
    if len(record_ids) != len(records):
        raise ValueError("Bitable record_ids length must match records length")
    if entity_refs and len(entity_refs) != len(records):
        raise ValueError("Bitable entity_refs length must match records length")
    for entity_ref in entity_refs:
        if not isinstance(entity_ref.get("entity_type"), str) or not entity_ref.get(
            "entity_type"
        ):
            raise ValueError("Bitable entity_refs must include entity_type")
        if not isinstance(entity_ref.get("entity_id"), str) or not entity_ref.get("entity_id"):
            raise ValueError("Bitable entity_refs must include entity_id")


def build_claimable_outbox_query(current_time: datetime):
    pending_due = and_(
        FeishuOutbox.status == "pending",
        FeishuOutbox.next_attempt_at <= current_time,
    )
    expired_claim = and_(
        FeishuOutbox.status == "claimed",
        FeishuOutbox.claim_expires_at.is_not(None),
        FeishuOutbox.claim_expires_at <= current_time,
    )

    return (
        select(FeishuOutbox)
        .where(or_(pending_due, expired_claim))
        .order_by(
            FeishuOutbox.next_attempt_at.asc(),
            FeishuOutbox.claim_expires_at.asc().nulls_last(),
            FeishuOutbox.created_at.asc(),
        )
        .with_for_update(skip_locked=True)
        .limit(1)
    )
