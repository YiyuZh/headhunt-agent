import json
from dataclasses import dataclass, replace
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.feishu.dispatcher import OutboxDispatchError
from app.feishu.gateways import FeishuRateLimitError
from app.feishu.outbox_handlers import FeishuOutboxHandler, FeishuTaskConfirmationPrepareHandler
from app.feishu.task_intake import (
    TaskIntakeSchemaError,
    build_task_confirmation_prepare_payload,
    parse_task_intake,
)
from app.gateways.llm import LLMGatewayError
from app.model_profiles.secrets import ModelSecretService
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
        self.stored = []

    def get_json_payload(self, content_ref: str) -> dict:
        return self.payloads[content_ref]

    def store_json_payload(self, *, content_ref: str, payload: dict, raw_text: str, sha256: str):
        self.payloads[content_ref] = payload
        self.stored.append(
            {
                "content_ref": content_ref,
                "payload": payload,
                "raw_text": raw_text,
                "sha256": sha256,
            }
        )
        return content_ref


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


class FakeOutboxWriter:
    def __init__(self):
        self.enqueued = []

    def enqueue_json(self, **kwargs) -> str:
        self.enqueued.append(kwargs)
        return kwargs.get("content_ref") or "artifact://fake-outbox"


class FakeModelProfile:
    def __init__(self, *, profile_id, encrypted_api_key):
        self.id = profile_id
        self.provider = "deepseek"
        self.model_name = "deepseek-v4-pro"
        self.base_url = "https://api.deepseek.com"
        self.user_id = "ou_1"
        self.encrypted_api_key = encrypted_api_key
        self.tenant_id = "tenant_1"


class FakeModelProfileRepository:
    def __init__(self, profile):
        self.profile = profile

    def get_active_profile(self, **kwargs):
        return self.profile


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


def test_outbox_handler_routes_task_confirmation_prepare_to_preparer() -> None:
    class FakePreparer:
        def __init__(self):
            self.calls = []

        def prepare_task_confirmation(self, payload, *, idempotency_key):
            self.calls.append((payload, idempotency_key))

    preparer = FakePreparer()
    payload = {"chat_id": "oc_1"}
    handler = FeishuOutboxHandler(
        payload_repository=FakePayloadRepository({"artifact://prepare": payload}),
        feishu_gateway=FakeFeishuGateway(),
        bitable_gateway=FakeBitableGateway(),
        task_confirmation_preparer=preparer,
    )

    handler.handle(
        FakeOutbox(
            kind="task_confirmation_prepare",
            payload_ref="artifact://prepare",
            idempotency_key="prepare-1",
        )
    )

    assert preparer.calls == [(payload, "prepare-1")]


def test_task_confirmation_prepare_success_enqueues_structured_confirmation_card(
    monkeypatch,
) -> None:
    event_payload = _message_event_payload()
    intake = parse_task_intake(event_payload, tenant_key="tenant_1")
    model_profile_id = uuid4()
    payload_repository = FakePayloadRepository({"artifact://event": event_payload})
    outbox_writer = FakeOutboxWriter()
    profile = FakeModelProfile(
        profile_id=model_profile_id,
        encrypted_api_key=ModelSecretService("test-model-secret").encrypt_api_key("sk-test"),
    )

    def fake_parse_task_intake_with_llm(received_intake, gateway, *, model_profile_id):
        return replace(
            received_intake,
            structured_fields={
                "task": "新建岗位",
                "project": "北京 AI 产品经理",
                "role": "AI 产品经理",
                "location": "北京",
                "level_years": "5-8 年",
                "compensation": "40-70K",
                "job_description": "负责 AI 产品规划",
                "must_have": ["AI 产品经验"],
                "nice_to_have": ["Agent 产品经验"],
                "target_companies": ["字节"],
                "excluded_companies": [],
                "deliverables": ["岗位校准"],
                "constraints": ["所有动作先确认"],
                "missing_fields": [],
                "assumptions": [],
                "confidence": 0.92,
            },
            parser_status="llm_parsed",
        )

    monkeypatch.setattr(
        "app.feishu.outbox_handlers.parse_task_intake_with_llm",
        fake_parse_task_intake_with_llm,
    )
    handler = FeishuTaskConfirmationPrepareHandler(
        payload_repository=payload_repository,
        outbox_writer=outbox_writer,
        settings=Settings(model_secret_encryption_key="test-model-secret"),
        model_profile_repository=FakeModelProfileRepository(profile),
    )

    handler.prepare_task_confirmation(
        build_task_confirmation_prepare_payload(
            chat_id=intake.chat_id,
            event_payload_ref="artifact://event",
            model_profile_id=model_profile_id,
            source_ref=intake.source_ref,
            tenant_key=intake.tenant_key,
            model_owner_user_id=intake.model_owner_user_id,
            model_owner_id_type=intake.model_owner_id_type,
            model_guild_id=intake.model_guild_id,
            thread_id=intake.thread_id,
        ),
        idempotency_key="prepare-1",
    )

    assert [item["kind"] for item in outbox_writer.enqueued] == ["card_send"]
    card_payload = outbox_writer.enqueued[0]["payload"]
    json.dumps(card_payload, ensure_ascii=False)
    assert card_payload["chat_id"] == "oc_1"
    assert card_payload["card"]["header"]["title"]["content"] == "请确认猎头任务"
    assert _card_markdown(card_payload["card"]).count("岗位: AI 产品经理") == 1
    assert any(
        item["content_ref"].startswith("artifact://feishu-task-intake/")
        and item["payload"]["task_intake"]["parser_status"] == "llm_parsed"
        for item in payload_repository.stored
    )


def test_task_confirmation_prepare_parse_failure_sends_failure_card_only(
    monkeypatch,
) -> None:
    event_payload = _message_event_payload()
    intake = parse_task_intake(event_payload, tenant_key="tenant_1")
    model_profile_id = uuid4()
    payload_repository = FakePayloadRepository({"artifact://event": event_payload})
    outbox_writer = FakeOutboxWriter()
    profile = FakeModelProfile(
        profile_id=model_profile_id,
        encrypted_api_key=ModelSecretService("test-model-secret").encrypt_api_key("sk-test"),
    )

    def fake_parse_task_intake_with_llm(received_intake, gateway, *, model_profile_id):
        raise LLMGatewayError("chat completion response did not contain JSON content")

    monkeypatch.setattr(
        "app.feishu.outbox_handlers.parse_task_intake_with_llm",
        fake_parse_task_intake_with_llm,
    )
    handler = FeishuTaskConfirmationPrepareHandler(
        payload_repository=payload_repository,
        outbox_writer=outbox_writer,
        settings=Settings(model_secret_encryption_key="test-model-secret"),
        model_profile_repository=FakeModelProfileRepository(profile),
    )

    handler.prepare_task_confirmation(
        build_task_confirmation_prepare_payload(
            chat_id=intake.chat_id,
            event_payload_ref="artifact://event",
            model_profile_id=model_profile_id,
            source_ref=intake.source_ref,
            tenant_key=intake.tenant_key,
            model_owner_user_id=intake.model_owner_user_id,
            model_owner_id_type=intake.model_owner_id_type,
            model_guild_id=intake.model_guild_id,
            thread_id=intake.thread_id,
        ),
        idempotency_key="prepare-1",
    )

    assert [item["kind"] for item in outbox_writer.enqueued] == ["card_send"]
    card_payload = outbox_writer.enqueued[0]["payload"]
    assert card_payload["card"]["header"]["title"]["content"] == "任务解析失败"
    assert "系统没有启动任务" in _card_markdown(card_payload["card"])
    assert "task_double_check" not in str(card_payload["card"])
    assert not any(
        item["content_ref"].startswith("artifact://feishu-task-intake/")
        for item in payload_repository.stored
    )


def test_task_confirmation_prepare_schema_failure_sends_failure_card_only(
    monkeypatch,
) -> None:
    event_payload = _message_event_payload()
    intake = parse_task_intake(event_payload, tenant_key="tenant_1")
    model_profile_id = uuid4()
    payload_repository = FakePayloadRepository({"artifact://event": event_payload})
    outbox_writer = FakeOutboxWriter()
    profile = FakeModelProfile(
        profile_id=model_profile_id,
        encrypted_api_key=ModelSecretService("test-model-secret").encrypt_api_key("sk-test"),
    )

    def fake_parse_task_intake_with_llm(received_intake, gateway, *, model_profile_id):
        raise TaskIntakeSchemaError("LLM task intake output missing required fields: task")

    monkeypatch.setattr(
        "app.feishu.outbox_handlers.parse_task_intake_with_llm",
        fake_parse_task_intake_with_llm,
    )
    handler = FeishuTaskConfirmationPrepareHandler(
        payload_repository=payload_repository,
        outbox_writer=outbox_writer,
        settings=Settings(model_secret_encryption_key="test-model-secret"),
        model_profile_repository=FakeModelProfileRepository(profile),
    )

    handler.prepare_task_confirmation(
        build_task_confirmation_prepare_payload(
            chat_id=intake.chat_id,
            event_payload_ref="artifact://event",
            model_profile_id=model_profile_id,
            source_ref=intake.source_ref,
            tenant_key=intake.tenant_key,
            model_owner_user_id=intake.model_owner_user_id,
            model_owner_id_type=intake.model_owner_id_type,
            model_guild_id=intake.model_guild_id,
            thread_id=intake.thread_id,
        ),
        idempotency_key="prepare-1",
    )

    assert [item["kind"] for item in outbox_writer.enqueued] == ["card_send"]
    card_payload = outbox_writer.enqueued[0]["payload"]
    assert card_payload["card"]["header"]["title"]["content"] == "任务解析失败"
    assert "task_double_check" not in str(card_payload["card"])
    assert not any(
        item["content_ref"].startswith("artifact://feishu-task-intake/")
        for item in payload_repository.stored
    )


def test_task_confirmation_prepare_rejects_unstructured_failed_intake(
    monkeypatch,
) -> None:
    event_payload = _message_event_payload()
    intake = parse_task_intake(event_payload, tenant_key="tenant_1")
    model_profile_id = uuid4()
    payload_repository = FakePayloadRepository({"artifact://event": event_payload})
    outbox_writer = FakeOutboxWriter()
    profile = FakeModelProfile(
        profile_id=model_profile_id,
        encrypted_api_key=ModelSecretService("test-model-secret").encrypt_api_key("sk-test"),
    )

    def fake_parse_task_intake_with_llm(received_intake, gateway, *, model_profile_id):
        return replace(
            received_intake,
            structured_fields={},
            parser_status="llm_failed",
            parser_error="LLM structured output is not valid JSON",
        )

    monkeypatch.setattr(
        "app.feishu.outbox_handlers.parse_task_intake_with_llm",
        fake_parse_task_intake_with_llm,
    )
    handler = FeishuTaskConfirmationPrepareHandler(
        payload_repository=payload_repository,
        outbox_writer=outbox_writer,
        settings=Settings(model_secret_encryption_key="test-model-secret"),
        model_profile_repository=FakeModelProfileRepository(profile),
    )

    handler.prepare_task_confirmation(
        build_task_confirmation_prepare_payload(
            chat_id=intake.chat_id,
            event_payload_ref="artifact://event",
            model_profile_id=model_profile_id,
            source_ref=intake.source_ref,
            tenant_key=intake.tenant_key,
            model_owner_user_id=intake.model_owner_user_id,
            model_owner_id_type=intake.model_owner_id_type,
            model_guild_id=intake.model_guild_id,
            thread_id=intake.thread_id,
        ),
        idempotency_key="prepare-1",
    )

    assert [item["kind"] for item in outbox_writer.enqueued] == ["card_send"]
    card_payload = outbox_writer.enqueued[0]["payload"]
    assert card_payload["card"]["header"]["title"]["content"] == "任务解析失败"
    assert "task_double_check" not in str(card_payload["card"])
    assert not any(
        item["content_ref"].startswith("artifact://feishu-task-intake/")
        for item in payload_repository.stored
    )


def test_task_confirmation_prepare_retryable_llm_error_raises_without_failure_card(
    monkeypatch,
) -> None:
    event_payload = _message_event_payload()
    intake = parse_task_intake(event_payload, tenant_key="tenant_1")
    model_profile_id = uuid4()
    outbox_writer = FakeOutboxWriter()
    profile = FakeModelProfile(
        profile_id=model_profile_id,
        encrypted_api_key=ModelSecretService("test-model-secret").encrypt_api_key("sk-test"),
    )

    def fake_parse_task_intake_with_llm(received_intake, gateway, *, model_profile_id):
        raise LLMGatewayError("DeepSeek Chat Completions API error: HTTP 429")

    monkeypatch.setattr(
        "app.feishu.outbox_handlers.parse_task_intake_with_llm",
        fake_parse_task_intake_with_llm,
    )
    handler = FeishuTaskConfirmationPrepareHandler(
        payload_repository=FakePayloadRepository({"artifact://event": event_payload}),
        outbox_writer=outbox_writer,
        settings=Settings(model_secret_encryption_key="test-model-secret"),
        model_profile_repository=FakeModelProfileRepository(profile),
    )

    with pytest.raises(OutboxDispatchError, match="HTTP 429") as exc_info:
        handler.prepare_task_confirmation(
            build_task_confirmation_prepare_payload(
                chat_id=intake.chat_id,
                event_payload_ref="artifact://event",
                model_profile_id=model_profile_id,
                source_ref=intake.source_ref,
                tenant_key=intake.tenant_key,
                model_owner_user_id=intake.model_owner_user_id,
                model_owner_id_type=intake.model_owner_id_type,
                model_guild_id=intake.model_guild_id,
                thread_id=intake.thread_id,
            ),
            idempotency_key="prepare-1",
        )

    assert exc_info.value.retry_after_seconds == 120
    assert outbox_writer.enqueued == []


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


def _message_event_payload() -> dict:
    return {
        "header": {
            "event_id": "evt_1",
            "event_type": "im.message.receive_v1",
            "tenant_key": "tenant_1",
        },
        "event": {
            "sender": {"sender_id": {"open_id": "ou_1"}},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_1",
                "content": '{"text":"新建岗位：北京 AI 产品经理，生成岗位校准和人才地图"}',
            },
        },
    }


def _card_markdown(card: dict) -> str:
    return str(card.get("body", {}).get("elements", [{}])[0].get("text", {}).get("content", ""))
