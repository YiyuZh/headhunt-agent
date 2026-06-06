from typing import Any
from uuid import UUID

from app.schemas.artifacts import ArtifactRef
from app.schemas.context import ContextPack
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


def _artifact_line(item: ArtifactRef) -> str:
    return f"- {item.kind}: {item.summary} ({item.content_ref})"


def _memory_line(item: MemoryRef) -> str:
    return (
        f"- {item.scope.value}: {item.summary} ({item.content_ref})；"
        f"命中原因：{item.reason}；tokens≈{item.tokens_estimate}"
    )
