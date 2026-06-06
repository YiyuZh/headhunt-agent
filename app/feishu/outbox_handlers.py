from typing import Any, Protocol
from uuid import UUID

from app.feishu.dispatcher import OutboxDispatchError
from app.feishu.gateways import FeishuGatewayError, FeishuHttpBitableGateway, FeishuHttpGateway
from app.runtime.outbox import RuntimeNotReadyError
from app.storage.models import FeishuOutbox
from app.storage.repositories import (
    BitableActionNotApprovedError,
    BitableSyncRepository,
    PayloadRepository,
)


class GraphDispatchHandler(Protocol):
    def dispatch_graph(self, payload: dict[str, Any]) -> None: ...
    def resume_graph(self, payload: dict[str, Any]) -> None: ...


class FeishuOutboxHandler:
    def __init__(
        self,
        *,
        payload_repository: PayloadRepository,
        feishu_gateway: FeishuHttpGateway,
        bitable_gateway: FeishuHttpBitableGateway,
        graph_handler: GraphDispatchHandler | None = None,
        bitable_sync_repository: BitableSyncRepository | None = None,
    ):
        self.payload_repository = payload_repository
        self.feishu_gateway = feishu_gateway
        self.bitable_gateway = bitable_gateway
        self.graph_handler = graph_handler
        self.bitable_sync_repository = bitable_sync_repository

    def handle(self, item: FeishuOutbox) -> None:
        payload = self.payload_repository.get_json_payload(item.payload_ref)

        try:
            match item.kind:
                case "card_send":
                    self._send_card(payload, item.idempotency_key)
                case "card_update":
                    self._update_card(payload, item.idempotency_key)
                case "bitable_write":
                    self._write_bitable(item, payload)
                case "graph_dispatch":
                    self._dispatch_graph(payload)
                case "resume":
                    self._resume_graph(payload)
                case _:
                    raise OutboxDispatchError(f"Unsupported outbox kind: {item.kind}")
        except FeishuGatewayError as exc:
            raise OutboxDispatchError(
                str(exc),
                retry_after_seconds=exc.retry_after_seconds,
            ) from exc

    def _send_card(self, payload: dict[str, Any], idempotency_key: str) -> None:
        chat_id = _required_str(payload, "chat_id")
        card = _required_dict(payload, "card")
        self.feishu_gateway.send_card(chat_id, card, idempotency_key)

    def _update_card(self, payload: dict[str, Any], idempotency_key: str) -> None:
        open_message_id = _required_str(payload, "open_message_id")
        card = _required_dict(payload, "card")
        self.feishu_gateway.update_card(open_message_id, card, idempotency_key)

    def _write_bitable(self, item: FeishuOutbox, payload: dict[str, Any]) -> None:
        action_id = _optional_uuid(payload.get("action_id"))
        if action_id is None:
            raise OutboxDispatchError("bitable_write payload.action_id is required")
        if self.bitable_sync_repository is None:
            raise OutboxDispatchError(
                "bitable_write requires BitableSyncRepository approval checker"
            )
        try:
            self.bitable_sync_repository.ensure_action_approved(action_id)
        except BitableActionNotApprovedError as exc:
            raise OutboxDispatchError(str(exc), retry_after_seconds=300) from exc
        app_token = _required_str(payload, "app_token")
        table_id = _required_str(payload, "table_id")
        client_token = _required_str(payload, "client_token")
        records = payload.get("records")
        if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
            raise OutboxDispatchError("bitable_write payload.records must be a list of objects")
        record_ids = self.bitable_gateway.batch_create(app_token, table_id, records, client_token)
        payload_hash = payload.get("payload_hash")
        entity_refs = payload.get("entity_refs")
        self.bitable_sync_repository.record_chunk_success(
            client_token=client_token,
            app_token=app_token,
            table_id=table_id,
            record_ids=record_ids,
            records=records,
            outbox_id=getattr(item, "id", None),
            action_id=action_id,
            chunk_index=_optional_int(payload.get("chunk_index")),
            payload_hash=payload_hash if isinstance(payload_hash, str) else None,
            entity_refs=entity_refs if isinstance(entity_refs, list) else [],
        )

    def _dispatch_graph(self, payload: dict[str, Any]) -> None:
        if self.graph_handler is None:
            raise OutboxDispatchError(
                "graph_dispatch handler is not wired yet",
                retry_after_seconds=300,
            )
        try:
            self.graph_handler.dispatch_graph(payload)
        except RuntimeNotReadyError as exc:
            raise OutboxDispatchError(
                str(exc),
                retry_after_seconds=exc.retry_after_seconds,
            ) from exc

    def _resume_graph(self, payload: dict[str, Any]) -> None:
        if self.graph_handler is None:
            raise OutboxDispatchError("resume handler is not wired yet", retry_after_seconds=300)
        try:
            self.graph_handler.resume_graph(payload)
        except RuntimeNotReadyError as exc:
            raise OutboxDispatchError(
                str(exc),
                retry_after_seconds=exc.retry_after_seconds,
            ) from exc


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise OutboxDispatchError(f"outbox payload missing {key}")
    return value


def _required_dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise OutboxDispatchError(f"outbox payload missing {key}")
    return value


def _optional_uuid(value: Any) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str) and value:
        return UUID(value)
    return None


def _optional_int(value: Any) -> int:
    return value if isinstance(value, int) else 0
