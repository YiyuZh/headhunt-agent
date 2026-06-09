from uuid import uuid4

from app.gateways.llm import LLMModelInfo
from app.harness.agent_harness import AgentHarness
from app.runtime.review_gate import ReviewGate
from app.schemas.agent import AgentPolicy, AgentTask
from app.schemas.common import CouncilMode, MemoryScope
from app.schemas.memory import MemoryRef


class FakeSession:
    def __init__(self):
        self.statements = []
        self.added = []
        self.flush_count = 0

    def execute(self, statement):
        self.statements.append(statement)

    def add(self, value):
        self.added.append(value)

    def flush(self):
        self.flush_count += 1


class FakeMemoryGateway:
    def __init__(self):
        self.retrieve_calls = []
        self.proposed = []
        self.memory_ref = MemoryRef(
            memory_id=uuid4(),
            scope=MemoryScope.run,
            summary="相关 run memory",
            content_ref="memory://run/old",
            relevance_score=0.9,
            reason="岗位关键词命中",
            tokens_estimate=20,
        )

    def retrieve(self, **kwargs):
        self.retrieve_calls.append(kwargs)
        return [self.memory_ref]

    def propose_update(self, agent_name, item):
        self.proposed.append((agent_name, item))
        return str(item.memory_id)


class FakeLLMGateway:
    def __init__(self, *, pii_level: str = "none"):
        self.calls = []
        self.pii_level = pii_level

    def model_info(self, **kwargs):
        return LLMModelInfo(
            model_profile_id=kwargs.get("model_profile_id"),
            model_provider="deepseek",
            model_name="deepseek-v4-pro",
            model_owner_user_id=kwargs.get("model_owner_user_id"),
        )

    def generate_structured(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "summary": "策略草稿已生成",
            "artifact_payload": {"plan": ["校准岗位", "生成目标公司"]},
            "evidence_refs": ["source://jd/1"],
            "source_refs": ["source://market/1"],
            "pii_level": self.pii_level,
            "confidence": 0.8,
            "requires_human_confirmation": False,
        }


class FakeArtifactStore:
    def __init__(self):
        self.writes = []

    def write(self, artifact, policy, *, payload=None, media_type="application/json"):
        self.writes.append((artifact, policy, payload, media_type))
        return artifact.content_ref


class FakeWarRoomNotifier:
    def __init__(self):
        self.agent_cards = []

    def enqueue_agent_run_card(self, **kwargs):
        self.agent_cards.append(kwargs)
        return "artifact://outbox/card"


def make_task() -> AgentTask:
    model_profile_id = uuid4()
    return AgentTask(
        thread_id=uuid4(),
        node_name="intake_calibration_graph",
        agent_name="StrategyDraftAgent",
        node_goal="生成岗位校准草稿",
        task_brief="请为 AI 平台负责人岗位做岗位校准",
        council_mode=CouncilMode.lite,
        mode_reason="常规任务",
        output_artifact_type="RequisitionCalibrationDraft",
        task_type="requisition_calibration",
        feishu_chat_id="oc_1",
        model_profile_id=model_profile_id,
        model_owner_user_id="discord-user-1",
        model_guild_id="discord-guild-1",
        model_tenant_id="tenant-1",
        policy=AgentPolicy(
            agent_name="StrategyDraftAgent",
            allowed_artifact_types_write=["RequisitionCalibrationDraft"],
            allowed_memory_scopes=[MemoryScope.run, MemoryScope.project],
            max_memory_items=3,
            max_context_tokens=1200,
            pii_access_level="low",
        ),
    )


def test_agent_harness_builds_minimal_context_and_persists_run_outputs() -> None:
    session = FakeSession()
    memory = FakeMemoryGateway()
    llm = FakeLLMGateway()
    artifact_store = FakeArtifactStore()
    war_room = FakeWarRoomNotifier()
    task = make_task()

    result = AgentHarness(
        session=session,
        llm_gateway=llm,
        memory_gateway=memory,
        artifact_store=artifact_store,
        war_room_notifier=war_room,
    ).run_agent(task)

    assert result.artifact.kind == "RequisitionCalibrationDraft"
    assert result.memory_refs == [memory.memory_ref]
    assert memory.retrieve_calls[0]["memory_scopes"] == ["RunMemory", "ProjectMemory"]
    assert memory.proposed[0][0] == "StrategyDraftAgent"
    assert memory.proposed[0][1].scope == MemoryScope.run
    assert memory.proposed[0][1].thread_id == task.thread_id
    context_pack = llm.calls[0]["context_pack"]
    assert llm.calls[0]["model_profile_id"] == task.model_profile_id
    assert llm.calls[0]["model_owner_user_id"] == "discord-user-1"
    assert context_pack.agent_name == "StrategyDraftAgent"
    assert context_pack.memory_refs == [memory.memory_ref]
    assert [item.sop_id for item in context_pack.sop_refs] == ["artifact-review-gate"]
    assert not hasattr(context_pack.sop_refs[0], "content")
    assert any("full SOP documents" in item for item in context_pack.excluded_context_reason)
    assert not hasattr(context_pack, "recruitment_state")
    assert not hasattr(context_pack, "node_history")
    assert artifact_store.writes[0][0].run_id == result.run_id
    assert artifact_store.writes[0][2] == {"plan": ["校准岗位", "生成目标公司"]}
    assert session.added[0].status == "succeeded"
    assert session.added[0].model_profile_id == task.model_profile_id
    assert session.added[0].model_provider == "deepseek"
    assert session.added[0].model_name == "deepseek-v4-pro"
    assert session.added[0].model_owner_user_id == "discord-user-1"
    assert war_room.agent_cards[0]["chat_id"] == "oc_1"
    assert war_room.agent_cards[0]["context_pack"].sop_refs == context_pack.sop_refs
    assert war_room.agent_cards[0]["output"].summary == "策略草稿已生成"


def test_agent_harness_preserves_high_pii_for_review_gate() -> None:
    session = FakeSession()
    memory = FakeMemoryGateway()
    artifact_store = FakeArtifactStore()
    task = make_task()

    result = AgentHarness(
        session=session,
        llm_gateway=FakeLLMGateway(pii_level="high"),
        memory_gateway=memory,
        artifact_store=artifact_store,
    ).run_agent(task)

    assert artifact_store.writes[0][0].pii_level == "high"
    assert result.artifact.pii_level == "high"
    review = ReviewGate().review_artifact(result.artifact)
    assert review.status == "needs_human"
    assert any(item.path == "pii_level" for item in review.findings)


def test_agent_task_rejects_mismatched_policy_agent() -> None:
    task_data = make_task().model_dump()
    task_data["policy"]["agent_name"] = "OtherAgent"

    try:
        AgentTask.model_validate(task_data)
    except ValueError as exc:
        assert "policy.agent_name" in str(exc)
    else:
        raise AssertionError("AgentTask accepted mismatched policy")
