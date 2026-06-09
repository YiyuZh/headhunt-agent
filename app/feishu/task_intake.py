import json
from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from app.policy.engine import PolicyEngine
from app.schemas.council import CouncilDeliberateRequest, TaskPlan


@dataclass(frozen=True)
class FeishuTaskIntake:
    request_text: str
    chat_id: str
    source_ref: str
    thread_id: UUID
    task_id: UUID
    tenant_key: str | None
    message_id: str | None
    sender_open_id: str | None
    sender_user_id: str | None
    sender_union_id: str | None
    field_sources: list[dict[str, Any]]
    missing_fields: list[str]
    assumptions: list[str]

    @property
    def model_owner_user_id(self) -> str | None:
        return self.sender_open_id or self.sender_user_id or self.sender_union_id

    @property
    def model_owner_id_type(self) -> str | None:
        if self.sender_open_id:
            return "open_id"
        if self.sender_user_id:
            return "user_id"
        if self.sender_union_id:
            return "union_id"
        return None

    @property
    def model_guild_id(self) -> str:
        return self.chat_id


def parse_task_intake(payload: dict[str, Any], *, tenant_key: str | None) -> FeishuTaskIntake:
    event = payload.get("event")
    if not isinstance(event, dict):
        raise ValueError("Feishu task intake requires event payload")
    message = event.get("message")
    if not isinstance(message, dict):
        raise ValueError("Feishu task intake requires event.message")

    chat_id = _first_str(message.get("chat_id"), event.get("open_chat_id"))
    if not chat_id:
        raise ValueError("Feishu task intake requires message.chat_id")

    message_id = _first_str(message.get("message_id"), event.get("message_id"))
    request_text = _extract_message_text(message).strip()
    source_ref = _source_ref(
        tenant_key=tenant_key,
        chat_id=chat_id,
        message_id=message_id,
        request_text=request_text,
    )
    sender_id = _sender_id(event)
    thread_id = uuid5(NAMESPACE_URL, f"lietou:feishu-task-thread:{source_ref}")
    task_id = uuid5(NAMESPACE_URL, f"lietou:feishu-task:{source_ref}")
    field_sources = [
        {
            "field": "request_text",
            "source": "feishu.event.message.content",
            "confidence": 1.0 if request_text else 0.0,
        }
    ]
    missing_fields = _missing_fields(request_text)
    assumptions = []
    if missing_fields:
        assumptions.append("输入信息不足时先进入 triage，会通过飞书卡片追问缺失字段。")

    return FeishuTaskIntake(
        request_text=request_text,
        chat_id=chat_id,
        source_ref=source_ref,
        thread_id=thread_id,
        task_id=task_id,
        tenant_key=tenant_key,
        message_id=message_id,
        sender_open_id=_first_str(sender_id.get("open_id")),
        sender_user_id=_first_str(sender_id.get("user_id")),
        sender_union_id=_first_str(sender_id.get("union_id")),
        field_sources=field_sources,
        missing_fields=missing_fields,
        assumptions=assumptions,
    )


def create_task_plan(intake: FeishuTaskIntake, policy_engine: PolicyEngine) -> TaskPlan:
    task_plan = policy_engine.create_task_plan(
        CouncilDeliberateRequest(
            request_text=intake.request_text,
            source="feishu",
            thread_id=intake.thread_id,
        )
    )
    return task_plan.model_copy(update={"task_id": intake.task_id})


def build_graph_dispatch_payload(
    *,
    intake: FeishuTaskIntake,
    task_plan: TaskPlan,
    model_profile_id: UUID,
    embedding_profile_id: UUID | None = None,
) -> dict[str, Any]:
    return {
        "thread_id": str(task_plan.thread_id),
        "task_id": str(task_plan.task_id),
        "source": "feishu",
        "source_ref": intake.source_ref,
        "user_input": task_plan.request_text,
        "task_type": task_plan.task_type,
        "council_mode": task_plan.council_mode.value,
        "mode_reason": task_plan.mode_reason,
        "required_agents": task_plan.required_agents,
        "optional_agents": task_plan.optional_agents,
        "user_forced_full_council": task_plan.user_forced_full_council,
        "model_profile_id": str(model_profile_id),
        "model_owner_user_id": intake.model_owner_user_id,
        "model_owner_id_type": intake.model_owner_id_type,
        "model_guild_id": intake.model_guild_id,
        "model_tenant_id": intake.tenant_key,
        "embedding_profile_id": str(embedding_profile_id) if embedding_profile_id else None,
        "authorization": {
            "status": "pending_feishu_double_check",
            "source": "feishu_card",
        },
        "task_intake": {
            "field_sources": intake.field_sources,
            "missing_fields": intake.missing_fields,
            "assumptions": intake.assumptions,
            "message_id": intake.message_id,
        },
    }


def task_payload_ref(thread_id: UUID, task_id: UUID) -> str:
    return f"artifact://feishu-task-intake/{thread_id}/{task_id}/v1"


def task_confirmation_card_ref(thread_id: UUID, task_id: UUID) -> str:
    return f"artifact://feishu-card/task-confirmation/{thread_id}/{task_id}/v1"


def model_setup_card_ref(source_ref: str) -> str:
    return f"artifact://feishu-card/model-setup/{uuid5(NAMESPACE_URL, source_ref)}"


def _extract_message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        try:
            content_json = json.loads(content)
        except json.JSONDecodeError:
            return content
        return _first_str(
            content_json.get("text"),
            content_json.get("title"),
            content_json.get("content"),
        ) or content
    if isinstance(content, dict):
        return _first_str(content.get("text"), content.get("title"), content.get("content")) or ""
    return ""


def _sender_id(event: dict[str, Any]) -> dict[str, Any]:
    sender = event.get("sender")
    if not isinstance(sender, dict):
        return {}
    sender_id = sender.get("sender_id")
    return sender_id if isinstance(sender_id, dict) else {}


def _source_ref(
    *,
    tenant_key: str | None,
    chat_id: str,
    message_id: str | None,
    request_text: str,
) -> str:
    if message_id:
        return f"feishu://message/{tenant_key or 'unknown_tenant'}/{chat_id}/{message_id}"
    fallback = uuid5(NAMESPACE_URL, f"{tenant_key}:{chat_id}:{request_text}")
    return f"feishu://message/{tenant_key or 'unknown_tenant'}/{chat_id}/{fallback}"


def _missing_fields(request_text: str) -> list[str]:
    missing: list[str] = []
    if len(request_text) < 20:
        missing.append("任务目标或岗位/候选人背景不足")
    task_type_keywords = ("岗位", "候选人", "人才地图", "报告", "复盘")
    if not any(keyword in request_text for keyword in task_type_keywords):
        missing.append("任务类型需要用户确认")
    return missing


def _first_str(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None
