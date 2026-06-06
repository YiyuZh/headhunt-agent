import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.runtime.war_room import WarRoomNotifier
from app.schemas.artifacts import ArtifactRef
from app.storage.models import ActionProposal, GraphThread
from app.storage.repositories import PayloadRepository


@dataclass(frozen=True)
class ProposedAction:
    action_id: UUID
    interrupt_id: UUID
    payload_ref: str
    idempotency_key: str


class ActionGate:
    def __init__(
        self,
        session: Session,
        *,
        war_room_notifier: WarRoomNotifier | None = None,
    ):
        self.session = session
        self.war_room_notifier = war_room_notifier
        self.payload_repository = PayloadRepository(session)

    def propose_action(
        self,
        *,
        thread_id: UUID,
        action_type: str,
        payload_summary: str,
        payload: dict[str, Any],
        idempotency_key: str,
        artifact_refs: list[ArtifactRef],
        chat_id: str | None = None,
        council_mode: str = "unknown",
        mode_reason: str = "",
    ) -> ProposedAction:
        existing = self._existing_action(idempotency_key)
        if existing is not None:
            return ProposedAction(
                action_id=existing.id,
                interrupt_id=existing.interrupt_id,
                payload_ref=existing.payload_ref,
                idempotency_key=existing.idempotency_key,
            )

        action_id = uuid4()
        interrupt_id = uuid4()
        payload_ref = f"artifact://action-proposal/{action_id}/payload/v1"
        payload_with_refs = {
            "action_type": action_type,
            "payload_summary": payload_summary,
            "payload": payload,
            "artifact_refs": [item.model_dump(mode="json") for item in artifact_refs],
        }
        raw_text = json.dumps(
            payload_with_refs,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self.payload_repository.store_json_payload(
            content_ref=payload_ref,
            payload=payload_with_refs,
            raw_text=raw_text,
            sha256=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        )
        self.session.execute(
            pg_insert(ActionProposal)
            .values(
                id=action_id,
                thread_id=thread_id,
                interrupt_id=interrupt_id,
                action_type=action_type,
                payload_summary=payload_summary,
                payload_ref=payload_ref,
                idempotency_key=idempotency_key,
                status="pending",
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
        )
        persisted = self._existing_action(idempotency_key)
        if persisted is not None and persisted.id != action_id:
            return ProposedAction(
                action_id=persisted.id,
                interrupt_id=persisted.interrupt_id,
                payload_ref=persisted.payload_ref,
                idempotency_key=persisted.idempotency_key,
            )
        self.session.execute(
            update(GraphThread)
            .where(GraphThread.id == thread_id)
            .values(
                status="interrupted",
                state_summary={
                    "pending_action_id": str(action_id),
                    "pending_interrupt_id": str(interrupt_id),
                    "pending_action_type": action_type,
                    "payload_summary": payload_summary,
                },
            )
        )
        self.session.flush()
        proposed = ProposedAction(
            action_id=action_id,
            interrupt_id=interrupt_id,
            payload_ref=payload_ref,
            idempotency_key=idempotency_key,
        )
        if self.war_room_notifier is not None:
            self.war_room_notifier.enqueue_approval_card(
                thread_id=thread_id,
                chat_id=chat_id,
                interrupt_id=interrupt_id,
                action_id=action_id,
                idempotency_key=idempotency_key,
                action_type=action_type,
                payload_summary=payload_summary,
                payload_ref=payload_ref,
                council_mode=council_mode,
                mode_reason=mode_reason,
                artifact_refs=artifact_refs,
            )
        return proposed

    def _existing_action(self, idempotency_key: str):
        result = self.session.execute(
            select(ActionProposal).where(ActionProposal.idempotency_key == idempotency_key)
        )
        if hasattr(result, "scalar_one_or_none"):
            return result.scalar_one_or_none()
        return None
