import json
from contextlib import nullcontext
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from langgraph.types import Command

from app.runtime.graph_factory import RuntimeGraphFactory


class RuntimeNotReadyError(RuntimeError):
    def __init__(self, message: str, *, retry_after_seconds: int = 300):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class LangGraphOutboxHandler:
    def __init__(
        self,
        *,
        graph_factory: RuntimeGraphFactory | None = None,
        graph=None,
        use_postgres_checkpointer: bool = True,
        allow_minimal_runtime: bool = False,
        allow_resume_without_interrupt: bool = False,
    ):
        self.graph_factory = graph_factory or RuntimeGraphFactory()
        self.graph = graph
        self.use_postgres_checkpointer = use_postgres_checkpointer
        self.allow_minimal_runtime = allow_minimal_runtime
        self.allow_resume_without_interrupt = allow_resume_without_interrupt
        self.last_result = None

    def dispatch_graph(self, payload: dict[str, Any]) -> None:
        if not self._dispatch_runtime_ready():
            raise RuntimeNotReadyError(
                "graph_dispatch requires real AgentHarness, ArtifactStore, AgentRuns, "
                "War Room side effects, and runtime dependency wiring"
            )
        state = feishu_payload_to_initial_state(payload)
        with self._graph_context() as graph:
            self.last_result = graph.invoke(
                state,
                config=_thread_config(state["thread_id"]),
            )

    def resume_graph(self, payload: dict[str, Any]) -> None:
        if not self._resume_runtime_ready():
            raise RuntimeNotReadyError(
                "resume requires an active LangGraph interrupt/checkpoint and ActionProposal gate"
            )
        approval = feishu_card_payload_to_human_approval(payload)
        thread_id = approval["thread_id"]
        with self._graph_context() as graph:
            self.last_result = graph.invoke(
                Command(resume=approval),
                config=_thread_config(thread_id),
            )

    def _graph_context(self):
        if self.graph is not None:
            return nullcontext(self.graph)
        if self.use_postgres_checkpointer:
            return self.graph_factory.graph_with_postgres_checkpointer()
        return nullcontext(self.graph_factory.create_headhunter_war_room_graph())

    def _dispatch_runtime_ready(self) -> bool:
        return (
            self.allow_minimal_runtime
            or getattr(self.graph_factory, "agent_harness", None) is not None
        )

    def _resume_runtime_ready(self) -> bool:
        return self.allow_resume_without_interrupt or (
            getattr(self.graph_factory, "agent_harness", None) is not None
            and getattr(self.graph_factory, "action_gate", None) is not None
            and getattr(self.graph_factory, "action_executor", None) is not None
        )


def feishu_payload_to_initial_state(payload: dict[str, Any]) -> dict[str, Any]:
    header = _dict_value(payload.get("header"))
    event = _dict_value(payload.get("event"))
    message = _dict_value(event.get("message"))
    sender = _dict_value(event.get("sender"))
    sender_id = _dict_value(sender.get("sender_id"))

    event_type = _first_str(
        header.get("event_type"),
        payload.get("event_type"),
        payload.get("type"),
    )
    event_id = _first_str(header.get("event_id"), payload.get("event_id"), payload.get("uuid"))
    message_id = _first_str(message.get("message_id"), message.get("open_message_id"))
    source_ref = message_id or event_id or "unknown"
    thread_id = str(uuid5(NAMESPACE_URL, f"feishu:{source_ref}"))
    text = _extract_message_text(message)

    return {
        "thread_id": thread_id,
        "source": "feishu",
        "source_ref": source_ref,
        "user_input": text or f"飞书事件：{event_type or 'unknown'}",
        "feishu_context": {
            "event_type": event_type,
            "event_id": event_id,
            "message_id": message_id,
            "chat_id": _first_str(message.get("chat_id"), event.get("chat_id")),
            "open_id": _first_str(sender_id.get("open_id"), event.get("open_id")),
        },
    }


def feishu_card_payload_to_human_approval(payload: dict[str, Any]) -> dict[str, Any]:
    direct_approval = _dict_value(payload.get("human_approval"))
    if direct_approval:
        return _direct_human_approval_payload(direct_approval)

    event = _dict_value(payload.get("event"))
    action = _dict_value(event.get("action"))
    value = _coerce_action_value(action.get("value"))
    operator = _dict_value(event.get("operator"))

    thread_id = _required_str(value, "thread_id")
    decision = _required_str(value, "decision")
    approval = {
        "thread_id": thread_id,
        "action_id": _required_str(value, "action_id"),
        "interrupt_id": _required_str(value, "interrupt_id"),
        "idempotency_key": _required_str(value, "idempotency_key"),
        "decision": decision,
        "approver": {
            "source": "feishu",
            "open_id": _first_str(operator.get("open_id")),
            "union_id": _first_str(operator.get("union_id")),
            "user_id": _first_str(operator.get("user_id")),
        },
    }
    edited_payload = value.get("edited_payload") or value.get("form_value")
    if decision == "edit" and (
        not isinstance(edited_payload, dict) or not edited_payload
    ):
        raise ValueError("HumanApproval edit requires edited_payload")
    if isinstance(edited_payload, dict):
        approval["edited_payload"] = edited_payload
    payload_ref = value.get("payload_ref")
    if isinstance(payload_ref, str) and payload_ref:
        approval["payload_ref"] = payload_ref
    return approval


def _direct_human_approval_payload(payload: dict[str, Any]) -> dict[str, Any]:
    decision = _required_str(payload, "decision")
    approval = {
        "thread_id": _required_str(payload, "thread_id"),
        "action_id": _required_str(payload, "action_id"),
        "interrupt_id": _required_str(payload, "interrupt_id"),
        "idempotency_key": _required_str(payload, "idempotency_key"),
        "decision": decision,
        "approver": payload.get("approver")
        if isinstance(payload.get("approver"), dict)
        else {"source": "internal"},
    }
    edited_payload = payload.get("edited_payload")
    if decision == "edit" and (
        not isinstance(edited_payload, dict) or not edited_payload
    ):
        raise ValueError("HumanApproval edit requires edited_payload")
    if isinstance(edited_payload, dict):
        approval["edited_payload"] = edited_payload
    payload_ref = payload.get("payload_ref")
    if isinstance(payload_ref, str) and payload_ref:
        approval["payload_ref"] = payload_ref
    return approval


def _thread_config(thread_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": str(UUID(str(thread_id)))}}


def _extract_message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return content
        if isinstance(parsed, dict):
            return _first_str(parsed.get("text"), parsed.get("content")) or ""
    return _first_str(message.get("text"), message.get("content")) or ""


def _coerce_action_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_str(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = _first_str(payload.get(key))
    if value is None:
        raise ValueError(f"Feishu card payload missing {key}")
    return value
