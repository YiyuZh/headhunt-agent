from dataclasses import dataclass
from uuid import uuid4

import pytest

from app.feishu.dispatcher import OutboxDispatchError
from app.feishu.gateways import FeishuRateLimitError
from app.feishu.outbox_handlers import FeishuOutboxHandler
from app.runtime.outbox import RuntimeNotReadyError


@dataclass
class FakeOutbox:
    kind: str
    payload_ref: str
    idempotency_key: str
    id: object | None = None


class FakePayloadRepository:
    def __init__(self, payloads):
        self.payloads = payloads

    def get_json_payload(self, content_ref: str) -> dict:
        return self.payloads[content_ref]


class FakeFeishuGateway:
    def __init__(self, error=None):
        self.sent_cards = []
        self.updated_cards = []
        self.error = error

    def send_card(self, chat_id: str, card: dict, idempotency_key: str) -> str:
        if self.error:
            raise self.error
        self.sent_cards.append((chat_id, card, idempotency_key))
        return "om_1"

    def update_card(self, open_message_id: str, card: dict, idempotency_key: str) -> str:
        self.updated_cards.append((open_message_id, card, idempotency_key))
        return open_message_id


class FakeBitableGateway:
    def __init__(self):
        self.batch_creates = []

    def batch_create(
        self,
        app_token: str,
        table_id: str,
        records: list[dict],
        client_token: str,
    ) -> list[str]:
        self.batch_creates.append((app_token, table_id, records, client_token))
        return ["rec_1"]


class FakeBitableSyncRepository:
    def __init__(self):
        self.successes = []
        self.checked_action_ids = []

    def ensure_action_approved(self, action_id) -> None:
        self.checked_action_ids.append(action_id)

    def record_chunk_success(self, **kwargs) -> None:
        self.successes.append(kwargs)


class FakeGraphHandler:
    def __init__(self):
        self.dispatched = []
        self.resumed = []

    def dispatch_graph(self, payload: dict) -> None:
        self.dispatched.append(payload)

    def resume_graph(self, payload: dict) -> None:
        self.resumed.append(payload)


class NotReadyGraphHandler:
    def dispatch_graph(self, payload: dict) -> None:
        raise RuntimeNotReadyError("runtime not ready", retry_after_seconds=300)

    def resume_graph(self, payload: dict) -> None:
        raise RuntimeNotReadyError("resume not ready", retry_after_seconds=300)


def make_handler(payloads, *, feishu_gateway=None, graph_handler=None):
    return FeishuOutboxHandler(
        payload_repository=FakePayloadRepository(payloads),
        feishu_gateway=feishu_gateway or FakeFeishuGateway(),
        bitable_gateway=FakeBitableGateway(),
        graph_handler=graph_handler,
    )


def test_outbox_handler_sends_card_from_payload() -> None:
    gateway = FakeFeishuGateway()
    handler = make_handler(
        {"artifact://card": {"chat_id": "oc_1", "card": {"elements": []}}},
        feishu_gateway=gateway,
    )

    handler.handle(
        FakeOutbox(
            kind="card_send",
            payload_ref="artifact://card",
            idempotency_key="card-send-1",
        )
    )

    assert gateway.sent_cards == [("oc_1", {"elements": []}, "card-send-1")]


def test_outbox_handler_updates_card_from_payload() -> None:
    gateway = FakeFeishuGateway()
    handler = make_handler(
        {"artifact://card": {"open_message_id": "om_1", "card": {"elements": []}}},
        feishu_gateway=gateway,
    )

    handler.handle(
        FakeOutbox(
            kind="card_update",
            payload_ref="artifact://card",
            idempotency_key="card-update-1",
        )
    )

    assert gateway.updated_cards == [("om_1", {"elements": []}, "card-update-1")]


def test_outbox_handler_writes_bitable_with_client_token() -> None:
    bitable_gateway = FakeBitableGateway()
    sync_repository = FakeBitableSyncRepository()
    entity_id = uuid4()
    action_id = uuid4()
    handler = FeishuOutboxHandler(
        payload_repository=FakePayloadRepository(
            {
                "artifact://bitable": {
                    "app_token": "app_1",
                    "table_id": "tbl_1",
                    "records": [{"fields": {"name": "A"}}],
                    "client_token": str(uuid4()),
                    "action_id": str(action_id),
                    "entity_refs": [
                        {"entity_type": "requisition", "entity_id": str(entity_id)}
                    ],
                }
            }
        ),
        feishu_gateway=FakeFeishuGateway(),
        bitable_gateway=bitable_gateway,
        bitable_sync_repository=sync_repository,
    )

    handler.handle(
        FakeOutbox(
            kind="bitable_write",
            payload_ref="artifact://bitable",
            idempotency_key="bitable-1",
            id=uuid4(),
        )
    )

    assert bitable_gateway.batch_creates[0][0] == "app_1"
    assert sync_repository.checked_action_ids == [action_id]
    assert bitable_gateway.batch_creates[0][1] == "tbl_1"
    assert bitable_gateway.batch_creates[0][2] == [{"fields": {"name": "A"}}]
    assert sync_repository.successes[0]["record_ids"] == ["rec_1"]
    assert sync_repository.successes[0]["action_id"] == action_id
    assert sync_repository.successes[0]["entity_refs"] == [
        {"entity_type": "requisition", "entity_id": str(entity_id)}
    ]


def test_outbox_handler_rejects_bitable_write_without_action_id() -> None:
    handler = FeishuOutboxHandler(
        payload_repository=FakePayloadRepository(
            {
                "artifact://bitable": {
                    "app_token": "app_1",
                    "table_id": "tbl_1",
                    "records": [{"fields": {"name": "A"}}],
                    "client_token": str(uuid4()),
                }
            }
        ),
        feishu_gateway=FakeFeishuGateway(),
        bitable_gateway=FakeBitableGateway(),
        bitable_sync_repository=FakeBitableSyncRepository(),
    )

    with pytest.raises(OutboxDispatchError, match="action_id"):
        handler.handle(
            FakeOutbox(
                kind="bitable_write",
                payload_ref="artifact://bitable",
                idempotency_key="bitable-no-action",
                id=uuid4(),
            )
        )


def test_outbox_handler_preserves_gateway_retry_after() -> None:
    handler = make_handler(
        {"artifact://card": {"chat_id": "oc_1", "card": {}}},
        feishu_gateway=FakeFeishuGateway(
            error=FeishuRateLimitError("rate limited", retry_after_seconds=60)
        ),
    )

    with pytest.raises(OutboxDispatchError) as exc:
        handler.handle(
            FakeOutbox(
                kind="card_send",
                payload_ref="artifact://card",
                idempotency_key="card-send-2",
            )
        )

    assert exc.value.retry_after_seconds == 60


def test_outbox_handler_does_not_fake_graph_dispatch_without_graph_handler() -> None:
    handler = make_handler({"artifact://event": {"event_type": "im.message.receive_v1"}})

    with pytest.raises(OutboxDispatchError) as exc:
        handler.handle(
            FakeOutbox(
                kind="graph_dispatch",
                payload_ref="artifact://event",
                idempotency_key="graph-1",
            )
        )

    assert "not wired" in str(exc.value)
    assert exc.value.retry_after_seconds == 300


def test_outbox_handler_dispatches_graph_when_handler_is_wired() -> None:
    graph_handler = FakeGraphHandler()
    handler = make_handler(
        {"artifact://event": {"event_type": "im.message.receive_v1"}},
        graph_handler=graph_handler,
    )

    handler.handle(
        FakeOutbox(
            kind="graph_dispatch",
            payload_ref="artifact://event",
            idempotency_key="graph-1",
        )
    )

    assert graph_handler.dispatched == [{"event_type": "im.message.receive_v1"}]


def test_outbox_handler_preserves_runtime_not_ready_retry_after() -> None:
    handler = make_handler(
        {"artifact://event": {"event_type": "im.message.receive_v1"}},
        graph_handler=NotReadyGraphHandler(),
    )

    with pytest.raises(OutboxDispatchError) as exc:
        handler.handle(
            FakeOutbox(
                kind="graph_dispatch",
                payload_ref="artifact://event",
                idempotency_key="graph-not-ready",
            )
        )

    assert "runtime not ready" in str(exc.value)
    assert exc.value.retry_after_seconds == 300


def test_outbox_handler_resumes_graph_when_handler_is_wired() -> None:
    graph_handler = FakeGraphHandler()
    handler = make_handler(
        {"artifact://approval": {"decision": "approve"}},
        graph_handler=graph_handler,
    )

    handler.handle(
        FakeOutbox(
            kind="resume",
            payload_ref="artifact://approval",
            idempotency_key="resume-1",
        )
    )

    assert graph_handler.resumed == [{"decision": "approve"}]
