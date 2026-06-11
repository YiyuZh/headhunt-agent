import operator
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, TypedDict
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.policy.engine import PolicyEngine
from app.runtime.action_executor import ActionExecutionError
from app.runtime.review_gate import ReviewGate, repair_artifact_for_review
from app.schemas.agent import AgentPolicy, AgentTask
from app.schemas.artifacts import ArtifactRef
from app.schemas.common import CouncilMode, MemoryScope, PiiLevel
from app.schemas.council import CouncilDecision, CouncilDeliberateRequest, TaskPlan


class RecruitmentState(TypedDict, total=False):
    thread_id: str
    source: str
    source_ref: str
    task_type: str
    user_input: str
    feishu_context: dict[str, Any]
    model_profile_id: str
    model_owner_user_id: str
    model_guild_id: str
    model_tenant_id: str
    embedding_profile_id: str
    council_mode: str
    mode_reason: str
    council_decision: dict[str, Any]
    department_opinions: Annotated[list[dict[str, Any]], operator.add]
    human_questions: list[str]
    ready_to_execute: bool
    task_plan: dict[str, Any]
    policy_snapshot: dict[str, Any]
    artifacts: Annotated[list[dict[str, Any]], operator.add]
    pending_artifact_types: list[str]
    visibility_mode: str
    memory_refs: Annotated[list[dict[str, Any]], operator.add]
    requisition: dict[str, Any]
    talent_map: list[dict[str, Any]]
    candidate_profile: dict[str, Any]
    candidate_match: dict[str, Any]
    review_result: dict[str, Any]
    review_fix_attempts: int
    human_approval: dict[str, Any]
    feishu_write_result: dict[str, Any]
    action_proposal: dict[str, Any]
    approval_required: bool
    agent_run_id: str
    input_artifact_refs: list[dict[str, Any]]
    node_history: Annotated[list[dict[str, Any]], operator.add]
    errors: Annotated[list[dict[str, Any]], operator.add]


BusinessRoute = Literal[
    "ask_user_for_missing_info",
    "reject_with_reason",
    "save_as_case_data",
    "intake_calibration_graph",
    "talent_mapping_graph",
    "candidate_screening_graph",
    "outreach_and_report_graph",
    "followup_review_graph",
]


def build_headhunter_war_room_graph(
    *,
    policy_engine: PolicyEngine | None = None,
    agent_harness=None,
    action_gate=None,
    action_executor=None,
    review_gate=None,
    checkpointer=None,
):
    resolved_policy_engine = policy_engine or PolicyEngine()
    resolved_review_gate = review_gate or ReviewGate()
    builder = StateGraph(RecruitmentState)

    builder.add_node("receive_user_input", receive_user_input)
    builder.add_node(
        "create_task_plan_and_policy",
        lambda state: create_task_plan_and_policy(state, resolved_policy_engine),
    )
    builder.add_node(
        "council_deliberation_graph",
        lambda state: invoke_council_deliberation_graph(
            build_council_deliberation_graph(agent_harness=agent_harness),
            state,
        ),
    )
    builder.add_node("dispatch_from_council", dispatch_from_council)
    builder.add_node("ask_user_for_missing_info", ask_user_for_missing_info)
    builder.add_node("reject_with_reason", reject_with_reason)
    builder.add_node("save_as_case_data", save_as_case_data)
    builder.add_node(
        "intake_calibration_graph",
        lambda state: invoke_business_subgraph(
            build_business_subgraph(
                node_name="intake_calibration_graph",
                agent_name="StrategyDraftAgent",
                artifact_kind="RequisitionCalibrationDraft",
                summary_prefix="岗位校准草稿",
                agent_harness=agent_harness,
            ),
            state,
        ),
    )
    builder.add_node(
        "talent_mapping_graph",
        lambda state: invoke_business_subgraph(
            build_business_subgraph(
                node_name="talent_mapping_graph",
                agent_name="SourcingMappingAgent",
                artifact_kind="TalentMapDraft",
                summary_prefix="人才地图草稿",
                agent_harness=agent_harness,
            ),
            state,
        ),
    )
    builder.add_node(
        "candidate_screening_graph",
        lambda state: invoke_business_subgraph(
            build_business_subgraph(
                node_name="candidate_screening_graph",
                agent_name="CandidateJudgementAgent",
                artifact_kind="CandidateMatchDraft",
                summary_prefix="候选人筛选草稿",
                agent_harness=agent_harness,
            ),
            state,
        ),
    )
    builder.add_node(
        "outreach_and_report_graph",
        lambda state: invoke_business_subgraph(
            build_business_subgraph(
                node_name="outreach_and_report_graph",
                agent_name="OutreachValueAgent",
                artifact_kind="ReportDraft",
                summary_prefix="报告/触达草稿",
                agent_harness=agent_harness,
            ),
            state,
        ),
    )
    builder.add_node(
        "followup_review_graph",
        lambda state: invoke_business_subgraph(
            build_business_subgraph(
                node_name="followup_review_graph",
                agent_name="DataAutomationAgent",
                artifact_kind="FollowupReviewDraft",
                summary_prefix="复盘跟进草稿",
                agent_harness=agent_harness,
            ),
            state,
        ),
    )
    builder.add_node(
        "interrupt_human_approval",
        lambda state: interrupt_human_approval(state, action_gate=action_gate),
    )
    builder.add_node(
        "review_gate",
        lambda state: review_latest_artifact(state, review_gate=resolved_review_gate),
    )
    builder.add_node("repair_artifact", repair_artifact_after_review)
    builder.add_node("reject_review_gate", reject_review_gate)
    builder.add_node(
        "execute_approved_action",
        lambda state: execute_approved_action(state, action_executor=action_executor),
    )
    builder.add_node("record_agent_run", record_agent_run)

    builder.add_edge(START, "receive_user_input")
    builder.add_edge("receive_user_input", "create_task_plan_and_policy")
    builder.add_edge("create_task_plan_and_policy", "council_deliberation_graph")
    builder.add_edge("council_deliberation_graph", "dispatch_from_council")
    builder.add_conditional_edges(
        "dispatch_from_council",
        route_from_council,
        {
            "ask_user_for_missing_info": "ask_user_for_missing_info",
            "reject_with_reason": "reject_with_reason",
            "save_as_case_data": "save_as_case_data",
            "intake_calibration_graph": "intake_calibration_graph",
            "talent_mapping_graph": "talent_mapping_graph",
            "candidate_screening_graph": "candidate_screening_graph",
            "outreach_and_report_graph": "outreach_and_report_graph",
            "followup_review_graph": "followup_review_graph",
        },
    )
    for node_name in (
        "ask_user_for_missing_info",
        "reject_with_reason",
    ):
        builder.add_edge(node_name, "record_agent_run")
    for node_name in (
        "save_as_case_data",
        "intake_calibration_graph",
        "talent_mapping_graph",
        "candidate_screening_graph",
        "outreach_and_report_graph",
        "followup_review_graph",
    ):
        builder.add_edge(node_name, "review_gate")
    builder.add_conditional_edges(
        "review_gate",
        route_from_review_gate,
        {
            "repair_artifact": "repair_artifact",
            "reject_review_gate": "reject_review_gate",
            "interrupt_human_approval": "interrupt_human_approval",
        },
    )
    builder.add_edge("repair_artifact", "review_gate")
    builder.add_edge("reject_review_gate", "record_agent_run")
    builder.add_edge("interrupt_human_approval", "execute_approved_action")
    builder.add_edge("execute_approved_action", "record_agent_run")
    builder.add_edge("record_agent_run", END)

    return builder.compile(checkpointer=checkpointer, name="headhunter_war_room_graph")


def build_council_deliberation_graph(*, agent_harness=None):
    builder = StateGraph(RecruitmentState)
    builder.add_node(
        "collect_council_opinions",
        lambda state: collect_council_opinions(state, agent_harness=agent_harness),
    )
    builder.add_node("synthesize_council_decision", synthesize_council_decision)
    builder.add_edge(START, "collect_council_opinions")
    builder.add_edge("collect_council_opinions", "synthesize_council_decision")
    builder.add_edge("synthesize_council_decision", END)
    return builder.compile(name="council_deliberation_graph")


def invoke_council_deliberation_graph(graph, state: RecruitmentState) -> dict[str, Any]:
    result = graph.invoke(
        {
            **_subgraph_runtime_scope(state),
            "task_plan": state["task_plan"],
        },
        config={"configurable": {"thread_id": state["thread_id"]}},
    )
    return {
        "department_opinions": result.get("department_opinions", []),
        "council_decision": result.get("council_decision"),
        "human_questions": result.get("human_questions", []),
        "ready_to_execute": result.get("ready_to_execute", False),
        "artifacts": result.get("artifacts", []),
        "node_history": result.get("node_history", []),
    }


def build_business_subgraph(
    *,
    node_name: str,
    agent_name: str,
    artifact_kind: str,
    summary_prefix: str,
    agent_harness=None,
):
    builder = StateGraph(RecruitmentState)
    builder.add_node(
        "create_business_artifact_ref",
        lambda state: create_business_artifact_ref(
            state,
            node_name=node_name,
            agent_name=agent_name,
            artifact_kind=artifact_kind,
            summary_prefix=summary_prefix,
            agent_harness=agent_harness,
        ),
    )
    builder.add_edge(START, "create_business_artifact_ref")
    builder.add_edge("create_business_artifact_ref", END)
    return builder.compile(name=node_name)


def invoke_business_subgraph(graph, state: RecruitmentState) -> dict[str, Any]:
    result = graph.invoke(
        {
            **_subgraph_runtime_scope(state),
            "council_mode": state.get("council_mode"),
            "mode_reason": state.get("mode_reason"),
            "council_decision": state.get("council_decision"),
            "task_plan": state.get("task_plan"),
            "input_artifact_refs": state.get("artifacts", []),
        },
        config={"configurable": {"thread_id": state["thread_id"]}},
    )
    return {
        "artifacts": result.get("artifacts", []),
        "review_result": result.get("review_result"),
        "review_fix_attempts": result.get("review_fix_attempts", 0),
        "node_history": result.get("node_history", []),
    }


def _subgraph_runtime_scope(state: RecruitmentState) -> dict[str, Any]:
    return {
        "thread_id": state["thread_id"],
        "source": state.get("source", "runtime"),
        "source_ref": state.get("source_ref"),
        "user_input": state.get("user_input", ""),
        "feishu_context": state.get("feishu_context", {}),
        "model_profile_id": state.get("model_profile_id"),
        "model_owner_user_id": state.get("model_owner_user_id"),
        "model_guild_id": state.get("model_guild_id"),
        "model_tenant_id": state.get("model_tenant_id"),
        "embedding_profile_id": state.get("embedding_profile_id"),
    }


def receive_user_input(state: RecruitmentState) -> dict[str, Any]:
    thread_id = state.get("thread_id") or str(uuid4())
    user_input = state.get("user_input") or state.get("task_brief") or ""
    return {
        "thread_id": str(thread_id),
        "user_input": user_input,
        "visibility_mode": state.get("visibility_mode") or "standard",
        "node_history": [_history("receive_user_input", "received summarized user input")],
    }


def create_task_plan_and_policy(
    state: RecruitmentState,
    policy_engine: PolicyEngine,
) -> dict[str, Any]:
    thread_id = UUID(str(state["thread_id"]))
    task_plan = policy_engine.create_task_plan(
        CouncilDeliberateRequest(
            request_text=state.get("user_input") or "空任务",
            source=state.get("source", "runtime"),
            thread_id=thread_id,
        )
    )
    task_plan_data = task_plan.model_dump(mode="json")
    return {
        "thread_id": str(task_plan.thread_id),
        "task_type": task_plan.task_type,
        "task_plan": task_plan_data,
        "policy_snapshot": {
            "council_mode": task_plan.council_mode.value,
            "mode_reason": task_plan.mode_reason,
            "required_agents": task_plan.required_agents,
            "optional_agents": task_plan.optional_agents,
            "allowed_gateways": task_plan.allowed_gateways,
            "token_budget": task_plan.token_budget,
        },
        "council_mode": task_plan.council_mode.value,
        "mode_reason": task_plan.mode_reason,
        "node_history": [
            _history(
                "create_task_plan_and_policy",
                f"selected {task_plan.council_mode.value}",
            )
        ],
    }


def collect_council_opinions(state: RecruitmentState, *, agent_harness=None) -> dict[str, Any]:
    task_plan = TaskPlan.model_validate(state["task_plan"])
    if agent_harness is not None:
        artifacts: list[dict[str, Any]] = []
        opinions: list[dict[str, Any]] = []
        for agent_name in task_plan.required_agents:
            if agent_name == "CouncilSynthesizerAgent":
                continue
            result = agent_harness.run_agent(
                _agent_task_from_state(
                    state,
                    task_plan=task_plan,
                    agent_name=agent_name,
                    node_name="collect_council_opinions",
                    node_goal=f"{agent_name} 生成会审意见",
                    output_artifact_type="CouncilOpinion",
                    artifact_refs=[],
                )
            )
            artifact_data = result.artifact.model_dump(mode="json")
            artifacts.append(artifact_data)
            opinions.append(
                {
                    "agent_name": agent_name,
                    "artifact_type": "CouncilOpinion",
                    "summary": result.artifact.summary,
                    "content_ref": result.artifact.content_ref,
                    "council_mode": task_plan.council_mode.value,
                    "run_id": str(result.run_id),
                }
            )
        return {
            "department_opinions": opinions,
            "artifacts": artifacts,
            "node_history": [
                _history(
                    "collect_council_opinions",
                    f"harness ran {len(opinions)} council opinions",
                )
            ],
        }
    opinions = [
        {
            "agent_name": agent_name,
            "artifact_type": "CouncilOpinion",
            "summary": f"{agent_name} planned for {task_plan.task_type}",
            "content_ref": _content_ref(
                state["thread_id"],
                "council_opinion",
                agent_name,
            ),
            "council_mode": task_plan.council_mode.value,
        }
        for agent_name in task_plan.required_agents
        if agent_name != "CouncilSynthesizerAgent"
    ]
    return {
        "department_opinions": opinions,
        "node_history": [
            _history(
                "collect_council_opinions",
                f"planned {len(opinions)} council opinions",
            )
        ],
    }


def synthesize_council_decision(state: RecruitmentState) -> dict[str, Any]:
    task_plan = TaskPlan.model_validate(state["task_plan"])
    decision = CouncilDecision.from_task_plan(task_plan)
    if task_plan.council_mode == CouncilMode.triage:
        decision.next_questions.append("请补充岗位目标、候选人范围或期望输出。")
    decision_data = decision.model_dump(mode="json")
    return {
        "council_decision": decision_data,
        "human_questions": decision.next_questions,
        "ready_to_execute": not decision.next_questions,
        "artifacts": [
            {
                "artifact_id": decision_data["decision_id"],
                "kind": "CouncilDecision",
                "summary": decision.intent_summary,
                "content_ref": _content_ref(state["thread_id"], "council_decision", "v1"),
                "evidence_refs": [],
                "source_refs": [],
                "pii_level": "none",
                "version": 1,
                "size_tokens_estimate": max(1, len(decision.intent_summary) // 4),
            }
        ],
        "node_history": [
            _history(
                "synthesize_council_decision",
                f"ready_to_execute={not decision.next_questions}",
            )
        ],
    }


def dispatch_from_council(state: RecruitmentState) -> dict[str, Any]:
    decision = state.get("council_decision") or {}
    return {
        "node_history": [
            _history(
                "dispatch_from_council",
                f"route={decision.get('recommended_business_subgraph', 'ask_user')}",
            )
        ]
    }


def route_from_council(state: RecruitmentState) -> BusinessRoute:
    if state.get("human_questions"):
        return "ask_user_for_missing_info"
    decision = state.get("council_decision") or {}
    subgraph = str(decision.get("recommended_business_subgraph") or "")
    route_map: dict[str, BusinessRoute] = {
        "requisition_calibration": "intake_calibration_graph",
        "talent_mapping": "talent_mapping_graph",
        "candidate_screening": "candidate_screening_graph",
        "report_draft": "outreach_and_report_graph",
        "review": "followup_review_graph",
        "save_as_case_data": "save_as_case_data",
    }
    return route_map.get(subgraph, "reject_with_reason")


def ask_user_for_missing_info(state: RecruitmentState) -> dict[str, Any]:
    return {
        "ready_to_execute": False,
        "node_history": [
            _history(
                "ask_user_for_missing_info",
                "waiting for required information",
            )
        ],
    }


def reject_with_reason(state: RecruitmentState) -> dict[str, Any]:
    return {
        "ready_to_execute": False,
        "errors": [
            {
                "node": "reject_with_reason",
                "message": "No supported business subgraph matched the council decision.",
            }
        ],
        "node_history": [_history("reject_with_reason", "unsupported route")],
    }


def save_as_case_data(state: RecruitmentState) -> dict[str, Any]:
    return {
        "ready_to_execute": True,
        "artifacts": [
            {
                "artifact_id": str(uuid5(NAMESPACE_URL, f"{state['thread_id']}:case_data:v1")),
                "kind": "CaseDataDraft",
                "summary": "保存为案例数据草稿，等待人工确认后写入业务数据底座。",
                "content_ref": _content_ref(state["thread_id"], "case_data", "v1"),
                "evidence_refs": [],
                "source_refs": [],
                "pii_level": "none",
                "version": 1,
                "size_tokens_estimate": 20,
            }
        ],
        "node_history": [_history("save_as_case_data", "case data route selected")],
    }


def create_business_artifact_ref(
    state: RecruitmentState,
    *,
    node_name: str,
    agent_name: str,
    artifact_kind: str,
    summary_prefix: str,
    agent_harness=None,
) -> dict[str, Any]:
    decision = state.get("council_decision") or {}
    if agent_harness is not None:
        task_plan = TaskPlan.model_validate(state["task_plan"])
        result = agent_harness.run_agent(
            _agent_task_from_state(
                state,
                task_plan=task_plan,
                agent_name=agent_name,
                node_name=node_name,
                node_goal=f"{agent_name} 生成 {artifact_kind}",
                output_artifact_type=artifact_kind,
                artifact_refs=_artifact_refs_from_state(state.get("input_artifact_refs", [])),
            )
        )
        return {
            "artifacts": [result.artifact.model_dump(mode="json")],
            "node_history": [_history(node_name, f"harness created {artifact_kind}")],
        }
    artifact_id = uuid5(
        NAMESPACE_URL,
        f"{state['thread_id']}:{node_name}:{decision.get('decision_id', 'decision')}",
    )
    summary = (
        f"{summary_prefix}；会审模式 {state.get('council_mode')}，"
        f"原因：{state.get('mode_reason')}"
    )
    return {
        "artifacts": [
            {
                "artifact_id": str(artifact_id),
                "kind": artifact_kind,
                "summary": summary,
                "content_ref": _content_ref(state["thread_id"], node_name, str(artifact_id)),
                "evidence_refs": [],
                "source_refs": [],
                "pii_level": "none",
                "version": 1,
                "size_tokens_estimate": max(1, len(summary) // 4),
            }
        ],
        "node_history": [_history(node_name, f"created {artifact_kind} ref")],
    }


def record_agent_run(state: RecruitmentState) -> dict[str, Any]:
    return {
        "node_history": [
            _history(
                "record_agent_run",
                "recorded run summary in graph state; database AgentRuns are handled by Harness",
            )
        ]
    }


def review_latest_artifact(
    state: RecruitmentState,
    *,
    review_gate: ReviewGate,
) -> dict[str, Any]:
    artifact = _latest_business_artifact(state)
    repair_attempts = int(state.get("review_fix_attempts") or 0)
    result = review_gate.review_artifact(
        artifact,
        review_context=_review_context_from_state(state),
        repair_attempts=repair_attempts,
    )
    return {
        "review_result": result.model_dump(mode="json"),
        "node_history": [
            _history(
                "review_gate",
                f"ReviewGate status={result.status} findings={len(result.findings)}",
            )
        ],
    }


def route_from_review_gate(state: RecruitmentState) -> str:
    result = state.get("review_result") or {}
    if _review_gate_has_schema_error(result):
        return "reject_review_gate"
    if result.get("status") == "needs_fix" and int(state.get("review_fix_attempts") or 0) < 1:
        return "repair_artifact"
    return "interrupt_human_approval"


def reject_review_gate(state: RecruitmentState) -> dict[str, Any]:
    artifact = _latest_business_artifact(state)
    finding_summary = _review_findings_summary(state.get("review_result") or {})
    return {
        "ready_to_execute": False,
        "approval_required": False,
        "errors": [
            {
                "node": "review_gate",
                "message": (
                    "ReviewGate rejected malformed artifact before action proposal"
                    + (f": {finding_summary}" if finding_summary else ".")
                ),
                "artifact_id": str(artifact.get("artifact_id") or ""),
                "artifact_kind": str(artifact.get("kind") or ""),
            }
        ],
        "node_history": [
            _history(
                "reject_review_gate",
                "blocked malformed artifact before action proposal",
            )
        ],
    }


def repair_artifact_after_review(state: RecruitmentState) -> dict[str, Any]:
    artifact = _latest_business_artifact(state)
    repaired = repair_artifact_for_review(artifact, state.get("review_result") or {})
    return {
        "artifacts": [repaired],
        "review_fix_attempts": int(state.get("review_fix_attempts") or 0) + 1,
        "node_history": [
            _history(
                "repair_artifact",
                f"repaired {artifact.get('kind', 'artifact')} after ReviewGate needs_fix",
            )
        ],
    }


def interrupt_human_approval(
    state: RecruitmentState,
    *,
    action_gate=None,
) -> dict[str, Any]:
    if action_gate is None:
        return {
            "approval_required": False,
            "node_history": [
                _history(
                    "interrupt_human_approval",
                    "approval gate skipped because ActionGate is not wired",
                )
            ],
        }

    proposal = _propose_business_action(state, action_gate=action_gate)
    approval = interrupt(_interrupt_payload(state, proposal))
    normalized = _normalize_human_approval(
        approval,
        state=state,
        action_id=str(proposal.action_id),
        interrupt_id=str(proposal.interrupt_id),
        idempotency_key=proposal.idempotency_key,
    )
    return {
        "approval_required": True,
        "action_proposal": {
            "action_id": str(proposal.action_id),
            "interrupt_id": str(proposal.interrupt_id),
            "idempotency_key": proposal.idempotency_key,
            "payload_ref": proposal.payload_ref,
        },
        "human_approval": normalized,
        "node_history": [
            _history(
                "interrupt_human_approval",
                f"resumed with human decision={normalized['decision']}",
            )
        ],
    }


def execute_approved_action(
    state: RecruitmentState,
    *,
    action_executor=None,
) -> dict[str, Any]:
    approval = state.get("human_approval")
    if not approval:
        return {
            "node_history": [
                _history("execute_approved_action", "no human approval to execute")
            ]
        }
    if action_executor is None:
        return {
            "node_history": [
                _history(
                    "execute_approved_action",
                    "action executor is not wired; no business side effect executed",
                )
            ]
        }
    try:
        result = action_executor.execute(approval)
    except ActionExecutionError as exc:
        return {
            "errors": [
                {
                    "node": "execute_approved_action",
                    "message": str(exc),
                }
            ],
            "node_history": [_history("execute_approved_action", "action execution failed")],
        }
    return {
        "feishu_write_result": result.to_dict(),
        "node_history": [
            _history(
                "execute_approved_action",
                f"action execution status={result.status}",
            )
        ],
    }


def _propose_business_action(state: RecruitmentState, *, action_gate):
    business_artifact = _latest_business_artifact(state)
    artifact_refs = _artifact_refs_from_state(state.get("artifacts", []))
    action_type = _action_type_for_business_kind(business_artifact["kind"])
    payload_summary = f"{action_type}: {business_artifact['summary']}"
    payload = {
        "business_kind": business_artifact["kind"],
        "record_fields": _record_fields_for_artifact(state, business_artifact),
        "entity_refs": [
            {
                "entity_type": business_artifact["kind"],
                "entity_id": business_artifact["artifact_id"],
            }
        ],
        "client_token": str(uuid4()),
    }
    review_result = state.get("review_result")
    if isinstance(review_result, dict):
        payload["review_result"] = review_result
        status = review_result.get("status")
        finding_summary = _review_findings_summary(review_result)
        if finding_summary:
            payload["review_findings_summary"] = finding_summary
        if status and status != "pass":
            payload_summary = f"{payload_summary} | ReviewGate={status}"
            if finding_summary:
                payload_summary = f"{payload_summary}: {finding_summary}"
    feishu_context = state.get("feishu_context") or {}
    return action_gate.propose_action(
        thread_id=UUID(str(state["thread_id"])),
        action_type=action_type,
        payload_summary=payload_summary,
        payload=payload,
        idempotency_key=_action_idempotency_key(state, action_type, business_artifact),
        artifact_refs=artifact_refs,
        chat_id=feishu_context.get("chat_id") if isinstance(feishu_context, dict) else None,
        council_mode=str(state.get("council_mode") or "unknown"),
        mode_reason=str(state.get("mode_reason") or ""),
    )


def _latest_business_artifact(state: RecruitmentState) -> dict[str, Any]:
    artifacts = state.get("artifacts", [])
    for artifact in reversed(artifacts):
        if artifact.get("kind") not in {"CouncilDecision", "CouncilOpinion"}:
            return artifact
    return {
        "artifact_id": str(uuid5(NAMESPACE_URL, f"{state['thread_id']}:action:v1")),
        "kind": "CaseDataDraft",
        "summary": "业务动作草稿",
        "content_ref": _content_ref(state["thread_id"], "action", "v1"),
        "version": 1,
    }


def _action_type_for_business_kind(kind: str) -> str:
    mapping = {
        "RequisitionCalibrationDraft": "requisition_write",
        "TalentMapDraft": "talent_map_write",
        "CandidateMatchDraft": "candidate_write",
        "ReportDraft": "report_write",
        "FollowupReviewDraft": "followup_write",
        "CaseDataDraft": "case_data_write",
    }
    return mapping.get(kind, "business_write")


def _record_fields_for_artifact(
    state: RecruitmentState,
    artifact: dict[str, Any],
) -> dict[str, Any]:
    return {
        "thread_id": str(state["thread_id"]),
        "artifact_id": str(artifact["artifact_id"]),
        "artifact_kind": str(artifact["kind"]),
        "summary": str(artifact.get("summary") or ""),
        "content_ref": str(artifact.get("content_ref") or ""),
        "council_mode": str(state.get("council_mode") or ""),
        "mode_reason": str(state.get("mode_reason") or ""),
        "source": str(state.get("source") or "runtime"),
        "source_ref": str(state.get("source_ref") or ""),
        "review_gate_status": str((state.get("review_result") or {}).get("status") or ""),
        "review_gate_findings": len((state.get("review_result") or {}).get("findings") or []),
    }


def _review_gate_has_schema_error(review_result: dict[str, Any]) -> bool:
    findings = review_result.get("findings")
    if not isinstance(findings, list):
        return False
    return any(
        isinstance(item, dict) and item.get("reviewer") == "SchemaValidator"
        for item in findings
    )


def _review_findings_summary(review_result: dict[str, Any], *, limit: int = 3) -> str:
    findings = review_result.get("findings")
    if not isinstance(findings, list):
        return ""
    parts: list[str] = []
    for item in findings:
        if not isinstance(item, dict):
            continue
        reviewer = str(item.get("reviewer") or "ReviewGate")
        path = str(item.get("path") or "").strip()
        message = " ".join(str(item.get("message") or "").split())
        if not message:
            continue
        prefix = f"{reviewer}/{path}" if path else reviewer
        parts.append(f"{prefix}: {message}")
        if len(parts) >= limit:
            break
    summary = " | ".join(parts)
    return summary[:500]


def _review_context_from_state(state: RecruitmentState) -> dict[str, Any]:
    artifacts = state.get("artifacts") or []
    memory_refs = state.get("memory_refs") or []
    latest = _latest_business_artifact(state)
    return {
        "thread_id": str(state.get("thread_id") or ""),
        "source": str(state.get("source") or "runtime"),
        "source_ref": str(state.get("source_ref") or ""),
        "council_mode": str(state.get("council_mode") or ""),
        "artifact_count": len(artifacts),
        "memory_ref_count": len(memory_refs),
        "total_tokens_estimate": sum(
            int(item.get("size_tokens_estimate") or 0)
            for item in artifacts
            if isinstance(item, dict)
        ),
        "pii_level": str(latest.get("pii_level") or "none"),
    }


def _action_idempotency_key(
    state: RecruitmentState,
    action_type: str,
    artifact: dict[str, Any],
) -> str:
    return (
        f"action:{state['thread_id']}:{action_type}:"
        f"{artifact['artifact_id']}:v{artifact.get('version', 1)}"
    )


def _interrupt_payload(state: RecruitmentState, proposal) -> dict[str, Any]:
    return {
        "thread_id": str(state["thread_id"]),
        "action_id": str(proposal.action_id),
        "interrupt_id": str(proposal.interrupt_id),
        "idempotency_key": proposal.idempotency_key,
        "council_mode": str(state.get("council_mode") or "unknown"),
        "mode_reason": str(state.get("mode_reason") or ""),
        "payload_ref": proposal.payload_ref,
        "message": "请在飞书确认卡片中 approve / edit / reject 该业务副作用。",
    }


def _normalize_human_approval(
    approval: Any,
    *,
    state: RecruitmentState,
    action_id: str,
    interrupt_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    if not isinstance(approval, dict):
        raise ValueError("HumanApproval resume payload must be an object")
    normalized = {
        "thread_id": _matching_value(
            approval,
            "thread_id",
            str(state["thread_id"]),
        ),
        "action_id": _matching_value(approval, "action_id", action_id),
        "interrupt_id": _matching_value(approval, "interrupt_id", interrupt_id),
        "idempotency_key": _matching_value(approval, "idempotency_key", idempotency_key),
        "decision": str(approval.get("decision") or ""),
        "approver": approval.get("approver") if isinstance(approval.get("approver"), dict) else {},
    }
    if normalized["decision"] not in {"approve", "edit", "reject"}:
        raise ValueError("HumanApproval decision must be approve, edit, or reject")
    edited_payload = approval.get("edited_payload")
    if normalized["decision"] == "edit" and not edited_payload:
        raise ValueError("HumanApproval edit requires edited_payload")
    if isinstance(edited_payload, dict):
        normalized["edited_payload"] = edited_payload
    return normalized


def _matching_value(payload: dict[str, Any], key: str, expected: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"HumanApproval missing {key}")
    if value != expected:
        raise ValueError(f"HumanApproval {key} does not match pending action")
    return value


def _history(node: str, summary: str) -> dict[str, str]:
    return {
        "node": node,
        "summary": summary,
        "at": datetime.now(UTC).isoformat(),
    }


def _content_ref(thread_id: str, kind: str, identity: str) -> str:
    return f"artifact://graph/{thread_id}/{kind}/{identity}/v1"


def _agent_task_from_state(
    state: RecruitmentState,
    *,
    task_plan: TaskPlan,
    agent_name: str,
    node_name: str,
    node_goal: str,
    output_artifact_type: str,
    artifact_refs: list[ArtifactRef],
) -> AgentTask:
    feishu_context = state.get("feishu_context") or {}
    return AgentTask(
        thread_id=task_plan.thread_id,
        node_name=node_name,
        agent_name=agent_name,
        node_goal=node_goal,
        task_brief=str(state.get("user_input") or task_plan.request_text),
        council_mode=task_plan.council_mode,
        mode_reason=task_plan.mode_reason,
        output_artifact_type=output_artifact_type,
        source=str(state.get("source") or "runtime"),
        source_ref=state.get("source_ref"),
        task_type=task_plan.task_type,
        feishu_chat_id=feishu_context.get("chat_id") if isinstance(feishu_context, dict) else None,
        artifact_refs=artifact_refs,
        source_refs=[],
        model_profile_id=_optional_uuid(state.get("model_profile_id")),
        model_owner_user_id=_optional_str(state.get("model_owner_user_id")),
        model_guild_id=_optional_str(state.get("model_guild_id")),
        model_tenant_id=_optional_str(state.get("model_tenant_id")),
        embedding_profile_id=_optional_uuid(state.get("embedding_profile_id")),
        policy=_default_agent_policy(agent_name, output_artifact_type),
    )


def _default_agent_policy(agent_name: str, output_artifact_type: str) -> AgentPolicy:
    pii_level = PiiLevel.medium if agent_name == "CandidateJudgementAgent" else PiiLevel.low
    return AgentPolicy(
        agent_name=agent_name,
        allowed_artifact_types_read=[
            "CouncilOpinion",
            "CouncilDecision",
            "RequisitionCalibrationDraft",
            "TalentMapDraft",
            "CandidateMatchDraft",
            "ReportDraft",
            "FollowupReviewDraft",
        ],
        allowed_artifact_types_write=[output_artifact_type],
        allowed_memory_scopes=[MemoryScope.run, MemoryScope.project, MemoryScope.case],
        max_memory_items=5,
        max_context_tokens=3500,
        max_output_tokens=1200,
        can_read_memory_content=False,
        can_read_artifact_content=False,
        pii_access_level=pii_level,
        allowed_side_effects=["audit_write", "run_memory_write", "war_room_card"],
    )


def _artifact_refs_from_state(artifacts: list[dict[str, Any]]) -> list[ArtifactRef]:
    refs: list[ArtifactRef] = []
    for artifact in artifacts:
        try:
            refs.append(ArtifactRef.model_validate(artifact))
        except ValueError:
            continue
    return refs


def _optional_uuid(value: Any) -> UUID | None:
    if value is None or value == "":
        return None
    return UUID(str(value))


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
