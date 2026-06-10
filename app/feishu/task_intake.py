import json
from dataclasses import dataclass, replace
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from app.policy.engine import PolicyEngine
from app.schemas.common import CouncilMode
from app.schemas.context import ContextPack
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
    structured_fields: dict[str, Any]
    parser_status: str
    parser_error: str | None

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

    @property
    def canonical_request_text(self) -> str:
        summary = structured_task_summary(self.structured_fields)
        return summary or self.request_text


def parse_task_intake(payload: dict[str, Any], *, tenant_key: str | None) -> FeishuTaskIntake:
    header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
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
    resolved_tenant_key = tenant_key or _first_str(
        header.get("tenant_key"),
        event.get("tenant_key"),
    )
    source_ref = _source_ref(
        tenant_key=resolved_tenant_key,
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
        tenant_key=resolved_tenant_key,
        message_id=message_id,
        sender_open_id=_first_str(sender_id.get("open_id")),
        sender_user_id=_first_str(sender_id.get("user_id")),
        sender_union_id=_first_str(sender_id.get("union_id")),
        field_sources=field_sources,
        missing_fields=missing_fields,
        assumptions=assumptions,
        structured_fields={},
        parser_status="not_run",
        parser_error=None,
    )


def create_task_plan(intake: FeishuTaskIntake, policy_engine: PolicyEngine) -> TaskPlan:
    task_plan = policy_engine.create_task_plan(
        CouncilDeliberateRequest(
            request_text=intake.canonical_request_text,
            source="feishu",
            thread_id=intake.thread_id,
        )
    )
    return task_plan.model_copy(update={"task_id": intake.task_id})


def parse_task_intake_with_llm(
    intake: FeishuTaskIntake,
    llm_gateway,
    *,
    model_profile_id: UUID,
) -> FeishuTaskIntake:
    result = llm_gateway.generate_structured(
        agent_name="FeishuTaskIntakeParser",
        context_pack=_task_intake_context_pack(intake),
        output_schema=TASK_INTAKE_OUTPUT_SCHEMA,
        schema_name="feishu_task_intake",
        max_output_tokens=2400,
        model_profile_id=model_profile_id,
        model_owner_user_id=intake.model_owner_user_id,
        model_guild_id=intake.model_guild_id,
        model_tenant_id=intake.tenant_key,
    )
    structured_fields = normalize_structured_task_fields(result)
    confidence = structured_fields.get("confidence")
    return replace(
        intake,
        structured_fields=structured_fields,
        parser_status="llm_parsed",
        parser_error=None,
        field_sources=[
            *intake.field_sources,
            {
                "field": "structured_fields",
                "source": "llm.FeishuTaskIntakeParser",
                "confidence": confidence if isinstance(confidence, int | float) else None,
            },
        ],
        missing_fields=_merge_text_lists(
            intake.missing_fields,
            structured_fields.get("missing_fields"),
        ),
        assumptions=_merge_text_lists(
            intake.assumptions,
            structured_fields.get("assumptions"),
        ),
    )


def mark_task_intake_parse_failed(intake: FeishuTaskIntake, error: str) -> FeishuTaskIntake:
    return replace(
        intake,
        parser_status="llm_failed",
        parser_error=error[:300],
    )


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
            "structured_fields": intake.structured_fields,
            "parser_status": intake.parser_status,
            "parser_error": intake.parser_error,
            "raw_request_text": intake.request_text,
            "message_id": intake.message_id,
        },
    }


def task_payload_ref(thread_id: UUID, task_id: UUID) -> str:
    return f"artifact://feishu-task-intake/{thread_id}/{task_id}/v1"


def task_confirmation_card_ref(thread_id: UUID, task_id: UUID) -> str:
    return f"artifact://feishu-card/task-confirmation/{thread_id}/{task_id}/v1"


def task_parse_failed_card_ref(source_ref: str, model_profile_id: UUID) -> str:
    ref_id = uuid5(NAMESPACE_URL, f"{source_ref}:{model_profile_id}")
    return f"artifact://feishu-card/task-parse-failed/{ref_id}"


def task_confirmation_prepare_ref(source_ref: str, model_profile_id: UUID) -> str:
    ref_id = uuid5(NAMESPACE_URL, f"{source_ref}:{model_profile_id}")
    return f"artifact://feishu-task-confirmation-prepare/{ref_id}"


def build_task_confirmation_prepare_payload(
    *,
    chat_id: str,
    event_payload_ref: str,
    model_profile_id: UUID,
    source_ref: str,
    tenant_key: str | None,
    model_owner_user_id: str | None,
    model_owner_id_type: str | None,
    model_guild_id: str,
    thread_id: UUID,
) -> dict[str, Any]:
    return {
        "chat_id": chat_id,
        "event_payload_ref": event_payload_ref,
        "model_profile_id": str(model_profile_id),
        "source_ref": source_ref,
        "tenant_key": tenant_key,
        "model_owner_user_id": model_owner_user_id,
        "model_owner_id_type": model_owner_id_type,
        "model_guild_id": model_guild_id,
        "thread_id": str(thread_id),
    }


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


TASK_INTAKE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "task": {"type": "string"},
        "project": {"type": "string"},
        "role": {"type": "string"},
        "location": {"type": "string"},
        "level_years": {"type": "string"},
        "compensation": {"type": "string"},
        "job_description": {"type": "string"},
        "must_have": {"type": "array", "items": {"type": "string"}},
        "nice_to_have": {"type": "array", "items": {"type": "string"}},
        "target_companies": {"type": "array", "items": {"type": "string"}},
        "excluded_companies": {"type": "array", "items": {"type": "string"}},
        "deliverables": {"type": "array", "items": {"type": "string"}},
        "constraints": {"type": "array", "items": {"type": "string"}},
        "missing_fields": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "task",
        "project",
        "role",
        "location",
        "level_years",
        "compensation",
        "job_description",
        "must_have",
        "nice_to_have",
        "target_companies",
        "excluded_companies",
        "deliverables",
        "constraints",
        "missing_fields",
        "assumptions",
        "confidence",
    ],
}


def _task_intake_context_pack(intake: FeishuTaskIntake) -> ContextPack:
    return ContextPack(
        thread_id=intake.thread_id,
        agent_name="FeishuTaskIntakeParser",
        task_brief=(
            "把飞书群里的猎头任务自然语言解析为结构化字段，供人工确认后再启动执行。"
            "不要照抄原文；无法确定的字段留空字符串或空数组，并写入 missing_fields。"
            "必须输出 json 对象，不要输出 markdown。"
            "\n\n示例 json："
            '{"task":"新建岗位","project":"北京 AI 产品经理","role":"AI 产品经理",'
            '"location":"北京","level_years":"5-8 年","compensation":"40-70K",'
            '"job_description":"负责 AI 产品规划","must_have":["AI 产品经验"],'
            '"nice_to_have":["Agent 产品经验"],"target_companies":["字节"],'
            '"excluded_companies":[],"deliverables":["岗位校准"],'
            '"constraints":["所有动作先确认"],"missing_fields":[],"assumptions":[],"confidence":0.86}'
            f"\n\n原始任务：\n{intake.request_text}"
        ),
        node_goal=(
            "Extract job requisition and delivery constraints into JSON fields: "
            "task, project, role, "
            "location, level_years, compensation, job_description, must_have, nice_to_have, "
            "target_companies, excluded_companies, deliverables, constraints."
        ),
        council_mode=CouncilMode.triage,
        mode_reason="飞书任务入站解析",
        source_refs=[intake.source_ref],
    )


def normalize_structured_task_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "task": _clean_text(payload.get("task")),
        "project": _clean_text(payload.get("project")),
        "role": _clean_text(payload.get("role")),
        "location": _clean_text(payload.get("location")),
        "level_years": _clean_text(payload.get("level_years")),
        "compensation": _clean_text(payload.get("compensation")),
        "job_description": _clean_text(payload.get("job_description")),
        "must_have": _text_list(payload.get("must_have")),
        "nice_to_have": _text_list(payload.get("nice_to_have")),
        "target_companies": _text_list(payload.get("target_companies")),
        "excluded_companies": _text_list(payload.get("excluded_companies")),
        "deliverables": _text_list(payload.get("deliverables")),
        "constraints": _text_list(payload.get("constraints")),
        "missing_fields": _text_list(payload.get("missing_fields")),
        "assumptions": _text_list(payload.get("assumptions")),
        "confidence": _confidence(payload.get("confidence")),
    }


def structured_task_summary(fields: dict[str, Any]) -> str:
    if not fields:
        return ""
    lines: list[str] = []
    for label, key in (
        ("任务", "task"),
        ("项目", "project"),
        ("岗位", "role"),
        ("地点", "location"),
        ("职级/年限", "level_years"),
        ("薪资", "compensation"),
        ("JD", "job_description"),
    ):
        value = _clean_text(fields.get(key))
        if value:
            lines.append(f"{label}: {value}")
    for label, key in (
        ("Must-have", "must_have"),
        ("Nice-to-have", "nice_to_have"),
        ("目标公司", "target_companies"),
        ("排除公司", "excluded_companies"),
        ("交付物", "deliverables"),
        ("限制", "constraints"),
    ):
        values = _text_list(fields.get(key))
        if values:
            lines.append(f"{label}: {'、'.join(values)}")
    return "\n".join(lines)


def _clean_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        text = _clean_text(item)
        if text:
            result.append(text)
    return result


def _confidence(value: Any) -> float:
    if isinstance(value, int | float):
        return max(0.0, min(1.0, float(value)))
    return 0.0


def _merge_text_lists(*values: Any) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in values:
        for item in _text_list(value):
            if item not in seen:
                seen.add(item)
                merged.append(item)
    return merged


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
