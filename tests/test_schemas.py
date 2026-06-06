from uuid import uuid4

import pytest

from app.schemas.common import CouncilMode, MemoryScope, MemoryStatus
from app.schemas.context import ContextPack
from app.schemas.memory import MemoryItem


def test_memory_item_uses_pending_review_status() -> None:
    item = MemoryItem(
        scope=MemoryScope.case,
        summary="A useful case memory",
        content_ref="memory://case/v1",
    )

    assert item.status == MemoryStatus.pending_review


def test_context_pack_rejects_forbidden_raw_state() -> None:
    with pytest.raises(ValueError, match="forbidden keys"):
        ContextPack(
            thread_id=uuid4(),
            agent_name="StrategyDraftAgent",
            task_brief="brief",
            node_goal="goal",
            council_mode=CouncilMode.lite,
            mode_reason="test",
            recruitment_state={"raw": "state"},
        )


def test_context_pack_rejects_nested_raw_state_in_artifact_refs() -> None:
    with pytest.raises(ValueError):
        ContextPack(
            thread_id=uuid4(),
            agent_name="StrategyDraftAgent",
            task_brief="brief",
            node_goal="goal",
            council_mode=CouncilMode.lite,
            mode_reason="test",
            artifact_refs=[
                {
                    "artifact_id": str(uuid4()),
                    "kind": "strategy_brief",
                    "summary": "summary",
                    "content_ref": "artifact://x/v1",
                    "recruitment_state": {"raw": "state"},
                }
            ],
        )


def test_context_pack_rejects_raw_state_in_budget_remaining() -> None:
    with pytest.raises(ValueError):
        ContextPack(
            thread_id=uuid4(),
            agent_name="StrategyDraftAgent",
            task_brief="brief",
            node_goal="goal",
            council_mode=CouncilMode.lite,
            mode_reason="test",
            budget_remaining={"max_context_tokens": 1000, "node_history": []},
        )
