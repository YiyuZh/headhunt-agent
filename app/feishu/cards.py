from typing import Any
from uuid import UUID

from app.schemas.artifacts import ArtifactRef
from app.schemas.context import ContextPack, SOPRef
from app.schemas.memory import MemoryRef


def build_agent_run_card(
    *,
    thread_id: UUID,
    title: str,
    context_pack: ContextPack,
    output_summary: str,
    artifact_refs: list[ArtifactRef],
    memory_refs: list[MemoryRef],
    token_estimate: int,
    requires_human_confirmation: bool,
) -> dict[str, Any]:
    lines = [
        f"**thread_id**\n{thread_id}",
        f"**会审模式**\n{context_pack.council_mode.value}",
        f"**模式原因**\n{context_pack.mode_reason}",
        f"**Agent**\n{context_pack.agent_name}",
        f"**任务目标**\n{context_pack.node_goal}",
        f"**关键结论**\n{output_summary}",
        f"**token 估算**\n{token_estimate}",
        f"**需要人工确认**\n{'是' if requires_human_confirmation else '否'}",
    ]
    if artifact_refs:
        lines.append(
            "**artifact_refs**\n" + "\n".join(_artifact_line(item) for item in artifact_refs)
        )
    if memory_refs:
        lines.append("**memory_refs**\n" + "\n".join(_memory_line(item) for item in memory_refs))
    if context_pack.sop_refs:
        lines.append(
            "**sop_refs**\n" + "\n".join(_sop_line(item) for item in context_pack.sop_refs)
        )

    return _base_card(title=title, template="blue", markdown="\n\n".join(lines))


def build_question_card(
    *,
    thread_id: UUID,
    council_mode: str,
    mode_reason: str,
    questions: list[str],
) -> dict[str, Any]:
    body = [
        f"**thread_id**\n{thread_id}",
        f"**会审模式**\n{council_mode}",
        f"**模式原因**\n{mode_reason}",
        "**需要补充**\n" + "\n".join(f"- {question}" for question in questions),
    ]
    return _base_card(title="需要补充信息", template="orange", markdown="\n\n".join(body))


def build_result_card(
    *,
    thread_id: UUID,
    council_mode: str,
    mode_reason: str,
    artifact_refs: list[ArtifactRef],
) -> dict[str, Any]:
    artifact_lines = "\n".join(_artifact_line(item) for item in artifact_refs) or "无"
    return _base_card(
        title="阶段结果",
        template="green",
        markdown=(
            f"**thread_id**\n{thread_id}\n\n"
            f"**会审模式**\n{council_mode}\n\n"
            f"**模式原因**\n{mode_reason}\n\n"
            f"**artifact_refs**\n{artifact_lines}"
        ),
    )


def build_approval_card(
    *,
    thread_id: UUID,
    interrupt_id: UUID,
    action_id: UUID,
    idempotency_key: str,
    action_type: str,
    payload_summary: str,
    council_mode: str,
    mode_reason: str,
    artifact_refs: list[ArtifactRef],
    payload_ref: str | None = None,
) -> dict[str, Any]:
    value = {
        "thread_id": str(thread_id),
        "interrupt_id": str(interrupt_id),
        "action_id": str(action_id),
        "idempotency_key": idempotency_key,
        "decision": "approve",
    }
    if payload_ref:
        value["payload_ref"] = payload_ref
    reject_value = {**value, "decision": "reject"}
    artifact_lines = "\n".join(_artifact_line(item) for item in artifact_refs) or "无"
    card = _base_card(
        title="需要人工确认",
        template="red",
        markdown=(
            f"**thread_id**\n{thread_id}\n\n"
            f"**会审模式**\n{council_mode}\n\n"
            f"**模式原因**\n{mode_reason}\n\n"
            f"**动作类型**\n{action_type}\n\n"
            f"**摘要**\n{payload_summary}\n\n"
            f"**artifact_refs**\n{artifact_lines}"
        ),
    )
    card["body"]["elements"].extend(
        [
            _button("批准执行", "primary", value),
            _button("拒绝", "danger", reject_value),
        ]
    )
    return card


def build_task_confirmation_card(
    *,
    thread_id: UUID,
    task_id: UUID,
    task_payload_ref: str,
    source_ref: str,
    request_text: str,
    task_type: str,
    council_mode: str,
    mode_reason: str,
    field_sources: list[dict[str, Any]],
    missing_fields: list[str],
    assumptions: list[str],
    structured_fields: dict[str, Any] | None = None,
    raw_request_text: str | None = None,
    parser_status: str | None = None,
    parser_error: str | None = None,
) -> dict[str, Any]:
    idempotency_key = f"task_confirm:{source_ref}"
    value = {
        "action_kind": "task_double_check",
        "thread_id": str(thread_id),
        "task_id": str(task_id),
        "task_payload_ref": task_payload_ref,
        "source_ref": source_ref,
        "idempotency_key": idempotency_key,
        "decision": "approve",
    }
    reject_value = {**value, "decision": "reject"}
    card = _base_card(
        title="请确认猎头任务",
        template="blue",
        markdown=(
            f"**thread_id**\n{thread_id}\n\n"
            f"**任务类型**\n{task_type}\n\n"
            f"**会审模式**\n{council_mode}\n\n"
            f"**模式原因**\n{mode_reason}\n\n"
            f"**结构化任务**\n{_structured_task_lines(structured_fields, request_text)}\n\n"
            f"**原始任务**\n{raw_request_text or request_text}\n\n"
            f"**解析状态**\n{_parser_status_line(parser_status, parser_error)}\n\n"
            f"**字段来源**\n{_field_source_lines(field_sources)}\n\n"
            f"**缺失字段**\n{_plain_lines(missing_fields)}\n\n"
            f"**系统假设**\n{_plain_lines(assumptions)}"
        ),
    )
    card["body"]["elements"].extend(
        [
            _button("确认并开始", "primary", value),
            _button("拒绝", "danger", reject_value),
        ]
    )
    return card


def build_model_setup_required_card(
    *,
    thread_id: UUID,
    source_ref: str,
    event_payload_ref: str,
    request_text: str,
    chat_id: str,
    user_id: str | None,
    user_id_type: str | None = None,
) -> dict[str, Any]:
    value = {
        "action_kind": "model_profile_setup",
        "thread_id": str(thread_id),
        "source_ref": source_ref,
        "event_payload_ref": event_payload_ref,
        "chat_id": chat_id,
        "model_owner_user_id": user_id or "",
        "model_owner_id_type": user_id_type or "",
        "usage": "chat",
        "idempotency_key": f"model_setup:{source_ref}",
        "make_default": True,
    }
    card = _base_card(
        title="请先配置模型",
        template="orange",
        markdown=(
            f"**thread_id**\n{thread_id}\n\n"
            f"**chat_id**\n{chat_id}\n\n"
            f"**user_id**\n{user_id or 'unknown'}\n\n"
            f"**收到的任务**\n{request_text or '未解析到文本'}\n\n"
            "当前飞书用户没有默认 chat 模型 profile，系统已阻断正式 graph。"
            "请在下方填写 API Key 后保存，系统会加密存储并继续发送任务确认卡。"
        ),
    )
    card["body"]["elements"].append(
        {
            "tag": "form",
            "name": "model_profile_setup",
            "elements": [
                _input(
                    "display_name",
                    "显示名称（可选）",
                    placeholder="例如 我的 OpenAI",
                    required=False,
                ),
                _input(
                    "model_name",
                    "模型名称（不填则用默认）",
                    placeholder="OpenAI: gpt-4.1-mini / DeepSeek: deepseek-v4-pro",
                    required=False,
                ),
                _input(
                    "api_key",
                    "API Key",
                    placeholder="sk-...",
                    required=True,
                    input_type="password",
                ),
                _input(
                    "base_url",
                    "Base URL（可选）",
                    placeholder=(
                        "OpenAI: https://api.openai.com / DeepSeek: https://api.deepseek.com"
                    ),
                    required=False,
                ),
                _form_submit_button(
                    "保存 OpenAI",
                    "primary",
                    {**value, "provider": "openai"},
                    name="model_profile_setup_openai_submit",
                ),
                _form_submit_button(
                    "保存 DeepSeek",
                    "default",
                    {**value, "provider": "deepseek"},
                    name="model_profile_setup_deepseek_submit",
                ),
            ],
        }
    )
    return card


def build_model_setup_saved_card(
    *,
    thread_id: UUID,
    provider: str,
    model_name: str,
) -> dict[str, Any]:
    return _base_card(
        title="模型已保存",
        template="green",
        markdown=(
            f"**thread_id**\n{thread_id}\n\n"
            f"**模型**\n{provider}:{model_name}\n\n"
            "API Key 已加密保存。任务确认卡已发送，请在新卡片中确认后启动。"
        ),
    )


def _base_card(*, title: str, template: str, markdown: str) -> dict[str, Any]:
    return {
        "schema": "2.0",
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": title},
        },
        "body": {
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": markdown,
                    },
                }
            ]
        },
    }


def _button(label: str, button_type: str, value: dict[str, Any]) -> dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": label},
        "type": button_type,
        "behaviors": [{"type": "callback", "value": value}],
    }


def _structured_task_lines(fields: dict[str, Any] | None, fallback: str) -> str:
    if not fields:
        return fallback
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
        value = _text(fields.get(key))
        if value:
            lines.append(f"- {label}: {value}")
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
            lines.append(f"- {label}: {'、'.join(values)}")
    return "\n".join(lines) or fallback


def _parser_status_line(status: str | None, error: str | None) -> str:
    if status == "llm_parsed":
        return "大模型已完成结构化解析"
    if status == "llm_failed":
        return f"大模型解析失败，已回退规则解析：{error or 'unknown'}"
    if status == "not_run":
        return "未运行大模型解析"
    return status or "unknown"


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        text = _text(item)
        if text:
            result.append(text)
    return result


def _form_submit_button(
    label: str,
    button_type: str,
    value: dict[str, Any],
    *,
    name: str,
) -> dict[str, Any]:
    button = _button(label, button_type, value)
    button["name"] = name
    button["action_type"] = "form_submit"
    button["form_action_type"] = "submit"
    return button


def _input(
    name: str,
    label: str,
    *,
    placeholder: str | None = None,
    required: bool = True,
    input_type: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "tag": "input",
        "name": name,
        "label": {"tag": "plain_text", "content": label},
        "required": required,
    }
    if input_type:
        item["input_type"] = input_type
    if placeholder:
        item["placeholder"] = {"tag": "plain_text", "content": placeholder}
    return item


def _artifact_line(item: ArtifactRef) -> str:
    return f"- {item.kind}: {item.summary} ({item.content_ref})"


def _memory_line(item: MemoryRef) -> str:
    return (
        f"- {item.scope.value}: {item.summary} ({item.content_ref})；"
        f"命中原因：{item.reason}；tokens≈{item.tokens_estimate}"
    )


def _sop_line(item: SOPRef) -> str:
    return (
        f"- {item.sop_id}@{item.version}: {item.title}；"
        f"命中原因：{item.trigger_reason}；tokens≈{item.tokens_estimate}"
    )


def _field_source_lines(items: list[dict[str, Any]]) -> str:
    if not items:
        return "无"
    return "\n".join(
        f"- {item.get('field')}: {item.get('source')} / confidence={item.get('confidence')}"
        for item in items
    )


def _plain_lines(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "无"
