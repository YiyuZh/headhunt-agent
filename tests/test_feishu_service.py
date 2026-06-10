import json
from hashlib import sha256
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.core.config import Settings
from app.feishu.callbacks import VerifiedFeishuCallback
from app.feishu.service import FeishuCallbackService
from app.feishu.task_intake import parse_task_intake
from app.storage.models import ArtifactBlob


class FakeResult:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def first(self):
        return self.value


class FakeBegin:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class FakeProfile:
    def __init__(self):
        self.id = uuid4()
        self.encrypted_api_key = "encrypted"


class FakeArtifact:
    def __init__(self, payload: dict):
        self.content_json = payload


class FakeSession:
    def __init__(self, *, select_results=None, payloads=None):
        self.select_results = list(select_results or [])
        self.payloads = payloads or {}
        self.executed = []
        self.added = []
        self.flushed = 0

    def begin(self):
        return FakeBegin()

    def execute(self, statement):
        if getattr(statement, "is_select", False):
            value = self.select_results.pop(0) if self.select_results else None
            return FakeResult(value)
        self.executed.append(statement)
        return FakeResult()

    def get(self, model, key):
        if model is ArtifactBlob and key in self.payloads:
            return FakeArtifact(self.payloads[key])
        if model is ArtifactBlob:
            for statement in reversed(self.executed):
                if getattr(getattr(statement, "table", None), "name", None) != "artifact_blobs":
                    continue
                params = _params(statement)
                if params.get("content_ref") == key:
                    return FakeArtifact(params.get("content_json"))
        return None

    def add(self, item):
        if getattr(item, "id", None) is None:
            item.id = uuid4()
        self.added.append(item)

    def flush(self):
        self.flushed += 1


def test_feishu_event_enqueues_task_confirmation_prepare_not_sync_llm() -> None:
    profile = FakeProfile()
    session = FakeSession(select_results=[profile])
    callback = _message_callback()

    result = FeishuCallbackService(session, settings=Settings()).enqueue_event(callback)

    assert result.status == "queued"
    graph_thread = _insert_params_for_table(session, "graph_threads")
    assert graph_thread["id"] == parse_task_intake(
        callback.payload,
        tenant_key=callback.tenant_key,
    ).thread_id
    assert graph_thread["task_type"] == "task_intake"
    assert graph_thread["state_summary"]["authorization_status"] == "pending_task_parse"
    outbox_params = _insert_params_for_table(session, "feishu_outbox")
    assert outbox_params["kind"] == "task_confirmation_prepare"
    assert outbox_params["payload_ref"].startswith(
        "artifact://feishu-task-confirmation-prepare/"
    )
    prepare_payload = _artifact_payload_for_ref(session, outbox_params["payload_ref"])
    assert prepare_payload["chat_id"] == "oc_1"
    assert prepare_payload["event_payload_ref"].startswith("artifact://feishu-callback/")
    assert prepare_payload["model_profile_id"] == str(profile.id)
    assert not _artifact_payloads(session, title="请确认猎头任务")


def _legacy_feishu_event_without_default_model_sends_setup_card_only() -> None:
    session = FakeSession(select_results=[None])
    callback = _message_callback()

    result = FeishuCallbackService(session, settings=Settings()).enqueue_event(callback)

    assert result.status == "queued"
    graph_thread = _insert_params_for_table(session, "graph_threads")
    assert graph_thread["id"] == parse_task_intake(
        callback.payload,
        tenant_key=callback.tenant_key,
    ).thread_id
    assert graph_thread["task_type"] == "task_intake"
    assert graph_thread["state_summary"]["authorization_status"] == "pending_model_profile"
    outbox_params = _insert_params_for_table(session, "feishu_outbox")
    assert outbox_params["kind"] == "card_send"
    card_payloads = _artifact_payloads(session, title="请先配置模型")
    assert card_payloads[0]["chat_id"] == "oc_1"


def test_feishu_event_without_default_model_sends_setup_card_only() -> None:
    session = FakeSession(select_results=[None])
    callback = _message_callback()

    result = FeishuCallbackService(session, settings=Settings()).enqueue_event(callback)

    assert result.status == "queued"
    graph_thread = _insert_params_for_table(session, "graph_threads")
    assert graph_thread["id"] == parse_task_intake(
        callback.payload,
        tenant_key=callback.tenant_key,
    ).thread_id
    assert graph_thread["task_type"] == "task_intake"
    assert graph_thread["state_summary"]["authorization_status"] == "pending_model_profile"
    outbox_params = _insert_params_for_table(session, "feishu_outbox")
    assert outbox_params["kind"] == "card_send"
    card_payloads = _artifact_payloads(session, title="请先配置模型")
    assert card_payloads[0]["chat_id"] == "oc_1"
    buttons = _card_buttons(card_payloads[0]["card"])
    button_names = {button.get("name") for button in buttons}
    assert "model_profile_setup_openai_submit" in button_names
    assert "model_profile_setup_deepseek_submit" in button_names
    api_inputs = [
        item for item in _card_inputs(card_payloads[0]["card"]) if item.get("name") == "api_key"
    ]
    assert api_inputs[0]["input_type"] == "password"
    action_values = _card_action_values(card_payloads[0]["card"])
    assert any(value.get("action_kind") == "model_profile_setup" for value in action_values)
    assert action_values[0]["event_payload_ref"].startswith("artifact://feishu-callback/")
    assert action_values[0]["model_owner_id_type"] == "open_id"


def test_task_confirmation_approve_enqueues_graph_dispatch() -> None:
    thread_id = uuid4()
    task_id = uuid4()
    task_payload_ref = f"artifact://feishu-task-intake/{thread_id}/{task_id}/v1"
    task_payload = {
        "thread_id": str(thread_id),
        "task_id": str(task_id),
        "source": "feishu",
        "source_ref": "feishu://message/tenant_1/oc_1/om_1",
        "user_input": "新建岗位：AI 产品经理",
        "task_type": "requisition_calibration",
        "council_mode": "lite",
        "mode_reason": "常规任务",
        "model_profile_id": str(uuid4()),
        "model_owner_user_id": "ou_1",
        "model_guild_id": "oc_1",
    }
    callback = _task_confirmation_callback(
        thread_id=str(thread_id),
        task_id=str(task_id),
        task_payload_ref=task_payload_ref,
        decision="approve",
    )
    session = FakeSession(select_results=[None, None], payloads={task_payload_ref: task_payload})

    result = FeishuCallbackService(session, settings=Settings()).enqueue_card_action(callback)

    assert result.status == "queued"
    graph_outbox = _insert_params_for_table(session, "feishu_outbox")
    assert graph_outbox["kind"] == "graph_dispatch"
    assert graph_outbox["payload_ref"] == f"{task_payload_ref}:approved"
    assert _insert_params_for_table(session, "graph_threads")["source"] == "feishu"
    assert _insert_params_for_table(session, "feishu_card_actions")["decision"] == "approve"
    approved_payloads = [
        _params(statement)["content_json"]
        for statement in session.executed
        if getattr(getattr(statement, "table", None), "name", None) == "artifact_blobs"
        and _params(statement).get("content_ref") == f"{task_payload_ref}:approved"
    ]
    assert approved_payloads[0]["authorization"]["status"] == "authorized"


def test_model_setup_card_saves_profile_redacts_secret_and_continues_task_flow() -> None:
    event_callback = _message_callback()
    event_payload_ref = f"artifact://feishu-callback/{event_callback.payload_hash}"
    thread_id = parse_task_intake(
        event_callback.payload,
        tenant_key=event_callback.tenant_key,
    ).thread_id
    callback = _model_setup_callback(
        thread_id=str(thread_id),
        event_payload_ref=event_payload_ref,
    )
    session = FakeSession(
        select_results=[None, None, None, None],
        payloads={event_payload_ref: event_callback.payload},
    )

    result = FeishuCallbackService(
        session,
        settings=Settings(model_secret_encryption_key="test-model-secret"),
    ).enqueue_card_action(callback)

    assert result.status == "model_setup_saved"
    assert session.added[0].provider == "openai"
    assert session.added[0].user_id == "ou_1"
    assert session.added[0].encrypted_api_key
    assert "sk-user-secret" not in session.added[0].encrypted_api_key
    outbox_params = _insert_params_for_table(session, "feishu_outbox")
    assert outbox_params["kind"] == "task_confirmation_prepare"
    assert outbox_params["payload_ref"].startswith(
        "artifact://feishu-task-confirmation-prepare/"
    )
    outbox_items = _insert_params_for_table_all(session, "feishu_outbox")
    assert [item["kind"] for item in outbox_items] == [
        "task_confirmation_prepare",
        "card_update",
    ]
    assert result.message == "模型已保存，正在解析任务，确认卡稍后发送。"
    update_payloads = [
        _params(statement)["content_json"]
        for statement in session.executed
        if getattr(getattr(statement, "table", None), "name", None) == "artifact_blobs"
        and _params(statement).get("content_json", {}).get("open_message_id") == "om_card"
    ]
    assert update_payloads[0]["card"]["header"]["title"]["content"] == "模型已保存"
    assert _insert_params_for_table(session, "feishu_card_actions")["decision"] == "approve"
    stored_artifacts = [
        json.dumps(_params(statement).get("content_json"), ensure_ascii=False)
        + str(_params(statement).get("content_text"))
        for statement in session.executed
        if getattr(getattr(statement, "table", None), "name", None) == "artifact_blobs"
    ]
    assert stored_artifacts
    assert all("sk-user-secret" not in item for item in stored_artifacts)


def test_model_setup_card_duplicate_submit_does_not_create_profile_or_outbox() -> None:
    event_callback = _message_callback()
    event_payload_ref = f"artifact://feishu-callback/{event_callback.payload_hash}"
    thread_id = parse_task_intake(
        event_callback.payload,
        tenant_key=event_callback.tenant_key,
    ).thread_id
    callback = _model_setup_callback(
        thread_id=str(thread_id),
        event_payload_ref=event_payload_ref,
    )
    session = FakeSession(
        select_results=[object()],
        payloads={event_payload_ref: event_callback.payload},
    )

    result = FeishuCallbackService(
        session,
        settings=Settings(model_secret_encryption_key="test-model-secret"),
    ).enqueue_card_action(callback)

    assert result.status == "duplicate"
    assert result.idempotency_key == "model_setup:1"
    assert session.added == []
    assert not any(
        getattr(getattr(statement, "table", None), "name", None) == "feishu_outbox"
        for statement in session.executed
    )


def test_model_setup_card_rejects_original_task_mismatch_before_profile_save() -> None:
    event_callback = _message_callback()
    event_payload_ref = f"artifact://feishu-callback/{event_callback.payload_hash}"
    callback = _model_setup_callback(
        thread_id=str(uuid4()),
        event_payload_ref=event_payload_ref,
    )
    session = FakeSession(
        select_results=[None],
        payloads={event_payload_ref: event_callback.payload},
    )

    result = FeishuCallbackService(
        session,
        settings=Settings(model_secret_encryption_key="test-model-secret"),
    ).enqueue_card_action(callback)

    assert result.status == "model_setup_failed"
    assert "does not match" in result.message
    assert session.added == []


def test_model_setup_card_rejects_operator_id_type_mismatch() -> None:
    event_callback = _message_callback()
    event_payload_ref = f"artifact://feishu-callback/{event_callback.payload_hash}"
    thread_id = parse_task_intake(
        event_callback.payload,
        tenant_key=event_callback.tenant_key,
    ).thread_id
    callback = _model_setup_callback(
        thread_id=str(thread_id),
        event_payload_ref=event_payload_ref,
        model_owner_id_type="user_id",
        operator={"open_id": "ou_1", "user_id": "different-user"},
    )
    session = FakeSession(
        select_results=[None],
        payloads={event_payload_ref: event_callback.payload},
    )

    result = FeishuCallbackService(
        session,
        settings=Settings(model_secret_encryption_key="test-model-secret"),
    ).enqueue_card_action(callback)

    assert result.status == "model_setup_failed"
    assert result.message == "只能由原任务发起人配置该任务的模型。"
    assert session.added == []


def _message_callback() -> VerifiedFeishuCallback:
    payload = {
        "header": {"event_id": "evt_1", "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_1"}},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_1",
                "content": '{"text":"新建岗位：北京 AI 产品经理，生成岗位校准和人才地图"}',
            },
        },
    }
    return _callback(payload, event_id="evt_1", event_type="im.message.receive_v1")


def _task_confirmation_callback(
    *,
    thread_id: str,
    task_id: str,
    task_payload_ref: str,
    decision: str,
) -> VerifiedFeishuCallback:
    payload = {
        "header": {"event_id": "card_evt_1", "event_type": "card.action.trigger"},
        "event": {
            "operator": {"open_id": "ou_1"},
            "action": {
                "value": {
                    "action_kind": "task_double_check",
                    "thread_id": thread_id,
                    "task_id": task_id,
                    "task_payload_ref": task_payload_ref,
                    "source_ref": "feishu://message/tenant_1/oc_1/om_1",
                    "idempotency_key": "task_confirm:1",
                    "decision": decision,
                }
            },
        },
    }
    return _callback(payload, event_id="card_evt_1", event_type="card.action.trigger")


def _model_setup_callback(
    *,
    thread_id: str,
    event_payload_ref: str,
    model_owner_id_type: str = "open_id",
    operator: dict | None = None,
) -> VerifiedFeishuCallback:
    payload = {
        "header": {"event_id": "model_card_evt_1", "event_type": "card.action.trigger"},
        "event": {
            "operator": operator or {"open_id": "ou_1"},
            "action": {
                "value": {
                    "action_kind": "model_profile_setup",
                    "thread_id": thread_id,
                    "source_ref": "feishu://message/tenant_1/oc_1/om_1",
                    "event_payload_ref": event_payload_ref,
                    "chat_id": "oc_1",
                    "model_owner_user_id": "ou_1",
                    "model_owner_id_type": model_owner_id_type,
                    "provider": "openai",
                    "usage": "chat",
                    "idempotency_key": "model_setup:1",
                },
                "form_value": json.dumps(
                    {
                        "display_name": "work-openai",
                        "model_name": "gpt-4.1-mini",
                        "api_key": "sk-user-secret",
                        "base_url": "https://api.openai.com",
                    }
                ),
            },
            "context": {"open_message_id": "om_card", "open_chat_id": "oc_1"},
        },
    }
    return _callback(payload, event_id="model_card_evt_1", event_type="card.action.trigger")


def _callback(payload: dict, *, event_id: str, event_type: str) -> VerifiedFeishuCallback:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return VerifiedFeishuCallback(
        payload=payload,
        raw_body=raw,
        payload_hash=sha256(raw).hexdigest(),
        is_challenge=False,
        challenge=None,
        event_id=event_id,
        event_type=event_type,
        tenant_key="tenant_1",
        app_id="cli_1",
        message_id="om_1",
    )


def _insert_params_for_table(session: FakeSession, table_name: str) -> dict:
    items = _insert_params_for_table_all(session, table_name)
    if items:
        return items[0]
    raise AssertionError(f"No insert captured for {table_name}")


def _insert_params_for_table_all(session: FakeSession, table_name: str) -> list[dict]:
    items = []
    for statement in session.executed:
        table = getattr(statement, "table", None)
        if getattr(table, "name", None) == table_name:
            items.append(_params(statement))
    return items


def _artifact_payloads(session: FakeSession, *, title: str) -> list[dict]:
    payloads = []
    for statement in session.executed:
        table = getattr(statement, "table", None)
        if getattr(table, "name", None) != "artifact_blobs":
            continue
        payload = _params(statement).get("content_json")
        if isinstance(payload, dict) and payload.get("card", {}).get("header", {}).get(
            "title", {}
        ).get("content") == title:
            payloads.append(payload)
    return payloads


def _artifact_payload_for_ref(session: FakeSession, content_ref: str) -> dict:
    for statement in session.executed:
        table = getattr(statement, "table", None)
        if getattr(table, "name", None) != "artifact_blobs":
            continue
        params = _params(statement)
        if params.get("content_ref") == content_ref:
            payload = params.get("content_json")
            if isinstance(payload, dict):
                return payload
    raise AssertionError(f"No artifact payload captured for {content_ref}")


def _card_action_values(card: dict) -> list[dict]:
    values = []

    def visit(item):
        if isinstance(item, dict):
            for behavior in item.get("behaviors", []):
                if isinstance(behavior, dict) and isinstance(behavior.get("value"), dict):
                    values.append(behavior["value"])
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(card)
    return values


def _card_buttons(card: dict) -> list[dict]:
    return _card_items(card, tag="button")


def _card_inputs(card: dict) -> list[dict]:
    return _card_items(card, tag="input")


def _card_items(card: dict, *, tag: str) -> list[dict]:
    items = []

    def visit(item):
        if isinstance(item, dict):
            if item.get("tag") == tag:
                items.append(item)
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(card)
    return items


def _params(statement) -> dict:
    return statement.compile(dialect=postgresql.dialect()).params
