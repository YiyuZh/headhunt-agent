from datetime import UTC, datetime
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.graphs.war_room import build_headhunter_war_room_graph
from app.runtime.action_executor import ActionExecutionResult
from app.runtime.graph_factory import RuntimeGraphFactory, _psycopg_conninfo
from app.schemas.artifacts import ArtifactRef
from app.schemas.common import CouncilMode
from app.schemas.review import ReviewGateResult


class FakeHarnessResult:
    def __init__(self, task):
        from uuid import uuid4

        self.run_id = uuid4()
        self.artifact = ArtifactRef(
            artifact_id=uuid4(),
            kind=task.output_artifact_type,
            summary=f"{task.agent_name} via harness",
            content_ref=f"artifact://harness/{task.agent_name}/{self.run_id}",
        )


class FakeHarness:
    def __init__(self):
        self.tasks = []

    def run_agent(self, task):
        self.tasks.append(task)
        return FakeHarnessResult(task)


class MalformedArtifact:
    def __init__(self, task):
        self.summary = "Malformed business artifact with an invalid identifier."
        self.content_ref = f"artifact://harness/malformed/{task.agent_name}"
        self.task = task

    def model_dump(self, mode="json"):
        return {
            "artifact_id": "not-a-uuid",
            "kind": self.task.output_artifact_type,
            "summary": self.summary,
            "content_ref": self.content_ref,
            "evidence_refs": ["evidence://malformed"],
            "source_refs": [],
            "pii_level": "none",
            "version": 1,
            "size_tokens_estimate": 12,
        }


class MalformedHarnessResult:
    def __init__(self, task):
        self.run_id = uuid4()
        self.artifact = MalformedArtifact(task)


class MalformedBusinessHarness(FakeHarness):
    def run_agent(self, task):
        self.tasks.append(task)
        if task.output_artifact_type == "TalentMapDraft":
            return MalformedHarnessResult(task)
        return FakeHarnessResult(task)


class FakeProposal:
    def __init__(self, *, idempotency_key: str, payload_ref: str):
        from uuid import uuid4

        self.action_id = uuid4()
        self.interrupt_id = uuid4()
        self.idempotency_key = idempotency_key
        self.payload_ref = payload_ref


class ReusingActionGate:
    def __init__(self):
        self.calls = []
        self.proposals = {}

    def propose_action(self, **kwargs):
        self.calls.append(kwargs)
        idempotency_key = kwargs["idempotency_key"]
        if idempotency_key not in self.proposals:
            self.proposals[idempotency_key] = FakeProposal(
                idempotency_key=idempotency_key,
                payload_ref=f"artifact://proposal/{len(self.proposals) + 1}",
            )
        return self.proposals[idempotency_key]


class FakeActionExecutor:
    def __init__(self):
        self.approvals = []

    def execute(self, approval):
        self.approvals.append(approval)
        return ActionExecutionResult(
            status="queued",
            action_id=approval["action_id"],
            decision=approval["decision"],
            outbox_payload_ref="artifact://outbox/bitable",
        )


class NeedsFixThenPassReviewGate:
    def __init__(self):
        self.calls = []

    def review_artifact(self, artifact, *, review_context=None, repair_attempts=0):
        self.calls.append(
            {
                "artifact": artifact,
                "review_context": review_context,
                "repair_attempts": repair_attempts,
            }
        )
        return ReviewGateResult(
            status="needs_fix" if repair_attempts == 0 else "pass",
            artifact_id=artifact["artifact_id"],
            artifact_kind=artifact["kind"],
            findings=[],
            repair_attempts=repair_attempts,
            reviewed_at=datetime.now(UTC),
        )


def test_war_room_graph_forces_full_council_when_user_requests_it() -> None:
    graph = build_headhunter_war_room_graph()

    result = graph.invoke(
        {"user_input": "请用三省六部完整会审这个高端猎头岗位，然后给出执行路径"},
        config={"configurable": {"thread_id": "thread-test"}},
    )

    assert result["council_mode"] == CouncilMode.full_council.value
    assert result["council_decision"]["council_mode"] == CouncilMode.full_council.value
    assert result["task_plan"]["user_forced_full_council"] is True
    assert "CandidateJudgementAgent" in result["task_plan"]["required_agents"]
    assert "CouncilSynthesizerAgent" in result["task_plan"]["required_agents"]
    assert result["ready_to_execute"] is True
    assert any(item["kind"] == "RequisitionCalibrationDraft" for item in result["artifacts"])


def test_war_room_graph_subgraph_returns_delta_without_reducer_duplication() -> None:
    graph = build_headhunter_war_room_graph()

    result = graph.invoke(
        {"user_input": "请用三省六部完整会审这个高端猎头岗位，然后给出执行路径"},
        config={"configurable": {"thread_id": "thread-count"}},
    )

    assert len(result["department_opinions"]) == 7
    artifact_kinds = [artifact["kind"] for artifact in result["artifacts"]]
    assert artifact_kinds.count("CouncilDecision") == 1
    assert artifact_kinds.count("RequisitionCalibrationDraft") == 1
    history_nodes = [item["node"] for item in result["node_history"]]
    assert history_nodes.count("collect_council_opinions") == 1
    assert history_nodes.count("synthesize_council_decision") == 1


def test_war_room_graph_triage_routes_to_questions() -> None:
    graph = build_headhunter_war_room_graph()

    result = graph.invoke(
        {"user_input": "招人"},
        config={"configurable": {"thread_id": "thread-triage"}},
    )

    assert result["council_mode"] == CouncilMode.triage.value
    assert result["ready_to_execute"] is False
    assert result["human_questions"] == ["请补充岗位目标、候选人范围或期望输出。"]
    assert result["node_history"][-2]["node"] == "ask_user_for_missing_info"


def test_war_room_graph_state_keeps_refs_not_forbidden_context_fields() -> None:
    graph = build_headhunter_war_room_graph()

    result = graph.invoke(
        {"user_input": "为 AI 平台负责人岗位做人岗筛选，输出候选人风险点"},
        config={"configurable": {"thread_id": "thread-context"}},
    )

    forbidden = {
        "messages",
        "chat_history",
        "recruitment_state",
        "all_artifacts",
        "all_memories",
        "agent_runs",
    }
    assert forbidden.isdisjoint(result)
    assert all("content_ref" in artifact for artifact in result["artifacts"])
    assert all("summary" in artifact for artifact in result["artifacts"])
    assert not any("content" in artifact for artifact in result["artifacts"])


def test_runtime_graph_factory_compiles_without_postgres_connection() -> None:
    graph = RuntimeGraphFactory().create_headhunter_war_room_graph()

    result = graph.invoke(
        {"user_input": "请为企业 AI 平台负责人岗位做人才地图 mapping，包含目标公司和 title 变体"},
        config={"configurable": {"thread_id": "thread-factory"}},
    )

    assert result["council_decision"]["recommended_business_subgraph"] == "talent_mapping"
    assert any(item["kind"] == "TalentMapDraft" for item in result["artifacts"])


def test_war_room_graph_uses_agent_harness_when_wired() -> None:
    harness = FakeHarness()
    graph = build_headhunter_war_room_graph(agent_harness=harness)

    result = graph.invoke(
        {"user_input": "请为 AI 平台负责人岗位做人才地图 mapping，包含目标公司和 title 变体"},
        config={"configurable": {"thread_id": "thread-harness"}},
    )

    task_names = [task.agent_name for task in harness.tasks]
    assert "IntentRouterAgent" in task_names
    assert "CouncilSynthesizerAgent" not in task_names
    assert "SourcingMappingAgent" in task_names
    assert any(item["summary"].endswith("via harness") for item in result["artifacts"])


def test_war_room_graph_blocks_schema_invalid_artifact_before_action_proposal() -> None:
    harness = MalformedBusinessHarness()
    action_gate = ReusingActionGate()
    graph = build_headhunter_war_room_graph(agent_harness=harness, action_gate=action_gate)

    result = graph.invoke(
        {"user_input": "Please build a talent mapping for an AI platform leadership role."},
        config={"configurable": {"thread_id": "thread-schema-review"}},
    )

    assert action_gate.calls == []
    assert result["ready_to_execute"] is False
    assert result["approval_required"] is False
    assert result["review_result"]["findings"][0]["reviewer"] == "SchemaValidator"
    assert result["errors"][0]["node"] == "review_gate"
    history_nodes = [item["node"] for item in result["node_history"]]
    assert "reject_review_gate" in history_nodes
    assert "interrupt_human_approval" not in history_nodes


def test_war_room_graph_interrupts_and_resumes_approved_business_action() -> None:
    action_gate = ReusingActionGate()
    action_executor = FakeActionExecutor()
    thread_id = "2c035461-6b47-4b92-a982-7b7eac099c36"
    graph = build_headhunter_war_room_graph(
        action_gate=action_gate,
        action_executor=action_executor,
        checkpointer=InMemorySaver(),
    )

    interrupted = graph.invoke(
        {"thread_id": thread_id, "user_input": "请为 AI 平台负责人岗位做人才地图 mapping"},
        config={"configurable": {"thread_id": thread_id}},
    )

    interrupt_payload = interrupted["__interrupt__"][0].value
    assert interrupt_payload["thread_id"] == thread_id
    assert interrupt_payload["action_id"]
    assert interrupt_payload["interrupt_id"]
    assert interrupt_payload["idempotency_key"].startswith(f"action:{thread_id}:")
    assert interrupt_payload["payload_ref"].startswith("artifact://proposal/")
    proposal_call = action_gate.calls[0]
    assert "ReviewGate=needs_human" in proposal_call["payload_summary"]
    assert "EvidenceConsistencyReviewer/evidence_refs" in proposal_call["payload_summary"]
    assert "Business artifact has no evidence_refs" in proposal_call["payload_summary"]
    assert proposal_call["payload"]["review_findings_summary"].startswith(
        "EvidenceConsistencyReviewer/evidence_refs"
    )

    resumed = graph.invoke(
        Command(
            resume={
                "thread_id": thread_id,
                "action_id": interrupt_payload["action_id"],
                "interrupt_id": interrupt_payload["interrupt_id"],
                "idempotency_key": interrupt_payload["idempotency_key"],
                "decision": "approve",
                "approver": {"open_id": "ou_1"},
            }
        ),
        config={"configurable": {"thread_id": thread_id}},
    )

    assert resumed["human_approval"]["decision"] == "approve"
    assert resumed["feishu_write_result"]["status"] == "queued"
    assert action_executor.approvals == [resumed["human_approval"]]
    history_nodes = [item["node"] for item in resumed["node_history"]]
    assert history_nodes.count("talent_mapping_graph") == 1
    assert history_nodes.count("interrupt_human_approval") == 1
    assert history_nodes.count("execute_approved_action") == 1


def test_war_room_graph_repairs_review_gate_needs_fix_once() -> None:
    review_gate = NeedsFixThenPassReviewGate()
    graph = build_headhunter_war_room_graph(review_gate=review_gate)

    result = graph.invoke(
        {"user_input": "请为 AI 平台负责人岗位做人才地图 mapping，包含目标公司和 title 变体"},
        config={"configurable": {"thread_id": "thread-review-gate"}},
    )

    history_nodes = [item["node"] for item in result["node_history"]]
    assert history_nodes.count("review_gate") == 2
    assert history_nodes.count("repair_artifact") == 1
    assert result["review_result"]["status"] == "pass"
    assert result["review_fix_attempts"] == 1
    assert len(review_gate.calls) == 2
    assert review_gate.calls[0]["repair_attempts"] == 0
    assert review_gate.calls[1]["repair_attempts"] == 1


def test_checkpoint_url_is_converted_to_psycopg_conninfo() -> None:
    assert (
        _psycopg_conninfo("postgresql+psycopg://user:pass@localhost:5432/db")
        == "postgresql://user:pass@localhost:5432/db"
    )
