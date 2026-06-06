from uuid import UUID, uuid4

from app.feishu.cards import build_agent_run_card, build_approval_card
from app.schemas.artifacts import ArtifactRef
from app.schemas.common import CouncilMode, MemoryScope
from app.schemas.context import ContextPack
from app.schemas.memory import MemoryRef


def test_agent_run_card_shows_mode_refs_and_not_raw_prompt() -> None:
    thread_id = uuid4()
    artifact = ArtifactRef(
        artifact_id=uuid4(),
        kind="CouncilOpinion",
        summary="结构化意见",
        content_ref="artifact://opinion/1",
    )
    memory = MemoryRef(
        memory_id=uuid4(),
        scope=MemoryScope.run,
        summary="历史校准摘要",
        content_ref="memory://run/1",
        relevance_score=0.8,
        reason="技能关键词命中",
        tokens_estimate=12,
    )
    context_pack = ContextPack(
        thread_id=thread_id,
        agent_name="StrategyDraftAgent",
        task_brief="不要在卡片里展示原始 prompt",
        node_goal="输出策略",
        council_mode=CouncilMode.lite,
        mode_reason="常规任务",
        memory_refs=[memory],
    )

    card = build_agent_run_card(
        thread_id=thread_id,
        title="Agent 结果",
        context_pack=context_pack,
        output_summary="输出完成",
        artifact_refs=[artifact],
        memory_refs=[memory],
        token_estimate=120,
        requires_human_confirmation=False,
    )

    content = card["body"]["elements"][0]["text"]["content"]
    assert card["schema"] == "2.0"
    assert "lite" in content
    assert "artifact://opinion/1" in content
    assert "memory://run/1" in content
    assert "原始 prompt" not in content


def test_approval_card_contains_callback_values_for_feishu_card_action() -> None:
    thread_id = uuid4()
    interrupt_id = uuid4()
    action_id = uuid4()

    card = build_approval_card(
        thread_id=thread_id,
        interrupt_id=interrupt_id,
        action_id=action_id,
        idempotency_key="idem-1",
        payload_ref="artifact://proposal/1",
        action_type="bitable_write",
        payload_summary="写入候选人推荐结论",
        council_mode="standard",
        mode_reason="需要人工确认",
        artifact_refs=[],
    )

    approve = card["body"]["elements"][1]["behaviors"][0]["value"]
    reject = card["body"]["elements"][2]["behaviors"][0]["value"]
    assert UUID(approve["thread_id"]) == thread_id
    assert UUID(approve["interrupt_id"]) == interrupt_id
    assert UUID(approve["action_id"]) == action_id
    assert approve["decision"] == "approve"
    assert reject["decision"] == "reject"
    assert reject["idempotency_key"] == "idem-1"
    assert approve["payload_ref"] == "artifact://proposal/1"
