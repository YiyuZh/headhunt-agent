# 模块 05：LangGraph 业务工作流 PRD

## 1. 模块目标

用 LangGraph 管理所有可恢复、可审计、可人工确认的工作流。系统只有一个运行时根图：

```text
headhunter_war_room_graph
```

所有用户入口都调用这个根图，但正式 graph 启动前必须先经过 `TaskIntakeParser` 和用户 double check。根图读取已经 approve 并冻结 version 的 `CanonicalTaskBrief`，再执行 `council_deliberation_graph`，并按 `CouncilDecision` 条件路由到业务子图。业务 graph 必须从结构化 brief 和 `CouncilDecision` 开始，不直接重新解析原始用户输入。

LangGraph 是编排器，不是权限系统本身。权限、预算、工具调用、跨 Agent 依赖由 `policy-engine`、`AgentHarness`、`ArtifactStore` 和 Gateway 共同执行。

## 2. 核心状态

LangGraph state 使用 `TypedDict` + reducer。Pydantic 用于请求、响应和 LLM 结构化输出。

`RecruitmentState` 至少包含：

```python
import operator
from typing import Annotated, TypedDict

class RecruitmentState(TypedDict, total=False):
    thread_id: str
    task_type: str
    user_input: str
    channel_context: dict
    canonical_task_brief: dict | None
    double_check_status: str
    council_mode: str
    mode_reason: str
    council_decision: dict | None
    department_opinions: Annotated[list[dict], operator.add]
    human_questions: list[str]
    ready_to_execute: bool
    task_plan: dict | None
    policy_snapshot: dict | None
    artifacts: Annotated[list[dict], operator.add]  # artifact refs + short summaries only
    pending_artifact_types: list[str]
    visibility_mode: str
    memory_refs: Annotated[list[dict], operator.add]
    requisition: dict | None
    talent_map: list[dict]
    candidate_profile: dict | None
    candidate_match: dict | None
    review_result: dict | None
    human_approval: dict | None
    channel_write_result: dict | None
    agent_run_id: str | None
    node_history: Annotated[list[dict], operator.add]
    errors: Annotated[list[dict], operator.add]
```

`department_opinions`、`artifacts`、`memory_refs`、`node_history`、`errors` 使用 reducer 追加，避免并行节点互相覆盖 state。

## 3. `headhunter_war_room_graph`

根图结构：

```text
START
-> load_confirmed_canonical_task
-> create_task_plan_and_policy
-> council_deliberation_graph
-> dispatch_from_council
   -> ask_user_for_missing_info
   -> reject_with_reason
   -> save_as_case_data
   -> intake_calibration_graph
   -> talent_mapping_graph
   -> candidate_screening_graph
   -> outreach_and_report_graph
   -> followup_review_graph
-> record_agent_run
-> END
```

路由规则：

```text
next_action=ask_user            -> ask_user_for_missing_info
next_action=reject              -> reject_with_reason
next_action=save_as_case_data   -> save_as_case_data
next_action=execute             -> execution_graph 指定的业务子图
```

`create_task_plan_and_policy` 必须写入：

```text
task_plan
policy_snapshot
visibility_mode
token_budget
council_mode
mode_reason
user_forced_full_council
required_agents
optional_agents
allowed_gateways
```

`council_mode` 由 `PolicyEngine` 和 `AgentHarness` 自动选择，取值为 `triage | lite | standard | full_council`。用户明确要求“三省六部”时，`user_forced_full_council=true` 且必须使用 `full_council`。所有 API 响应、Discord War Room 卡片和 AgentRuns 都必须记录本次实际使用的 `council_mode`、`mode_reason`、`required_agents` 和 `optional_agents`。

正式 graph 前置任务确认：

```text
POST /discord/interactions
-> TaskIntakeParser
-> CanonicalTaskBrief
-> SchemaValidator
-> Discord 任务确认卡
-> 用户 approve / edit / reject
-> approve 后冻结 CanonicalTaskBrief version
-> approve 后才排队 graph_dispatch
```

`TaskIntakeParser` 的每个关键字段必须有 `field_source` 和 `confidence`；无 source 的推断只能进入 `assumptions`，不能进入事实字段。edit 必须生成新 version 并再次确认。

## 4. `intake_calibration_graph`

```text
START
-> load_council_decision
-> parse_jd
-> identify_missing_info
-> define_must_win_criteria
-> extract_selling_points
-> build_initial_search_hypothesis
-> review_gate_for_requisition_artifact
   -> pass: interrupt_human_approval
   -> needs_fix: repair_requisition_artifact
   -> needs_human: interrupt_human_approval
-> interrupt_human_approval
-> write_requisition
-> END
```

输出：

- 岗位使命
- 必杀条件
- 加分项
- 淘汰项
- 岗位卖点
- 目标公司假设
- title 变体
- 缺失信息追问清单

## 5. `talent_mapping_graph`

```text
START
-> load_council_decision
-> load_requisition
-> map_skills_to_taxonomy
-> expand_title_aliases
-> build_target_company_map
-> generate_search_queries
-> create_talent_map_items
-> review_gate_for_talent_map_artifact
   -> pass: interrupt_human_approval
   -> needs_fix: repair_talent_map_artifact
   -> needs_human: interrupt_human_approval
-> interrupt_human_approval
-> write_talent_map
-> END
```

Talent Mapping 节点允许调用 `SearchGateway`，但只能生成搜索策略、目标公司假设、公开资料来源和 TalentMap 草稿，不自动抓取候选人个人数据。公司背调、当前在职状态、融资、新闻、裁员和组织变化等当前事实必须来自 `SearchGateway.source_refs`，长期记忆不得替代实时搜索。

## 6. `candidate_screening_graph`

```text
START
-> load_council_decision
-> load_requisition
-> import_candidate
-> parse_resume
-> extract_candidate_skills
-> match_candidate_to_requisition
-> identify_gaps_and_risks
-> generate_questions_to_ask
-> review_gate_for_candidate_match_artifact
   -> pass: interrupt_human_approval
   -> needs_fix: repair_candidate_match_artifact
   -> needs_human: interrupt_human_approval
-> interrupt_human_approval
-> write_candidate
-> END
```

## 7. `outreach_and_report_graph`

```text
START
-> load_council_decision
-> load_requisition_candidate_interactions
-> draft_outreach_or_report
-> review_gate_for_outreach_or_report_artifact
   -> pass: interrupt_human_approval
   -> needs_fix: repair_outreach_or_report_artifact
   -> needs_human: interrupt_human_approval
-> interrupt_human_approval
-> create_doc_or_save_draft
-> write_report_or_interaction
-> END
```

触达话术只能是草稿，不自动发送。

## 8. `followup_review_graph`

```text
START
-> load_council_decision
-> list_open_requisitions
-> list_due_candidates
-> summarize_blockers
-> recommend_next_actions
-> review_gate_for_followup_artifact
   -> pass: interrupt_human_approval
   -> needs_fix: repair_followup_artifact
   -> needs_human: interrupt_human_approval
-> interrupt_human_approval
-> create_tasks_and_send_summary
-> END
```

## 9. Artifact 与依赖 Gate

业务图之间不直接传大段自由文本，而是通过 `AgentArtifact` 传结构化结果。LangGraph state 只保存 artifact 引用和短摘要，artifact 全文必须放在 ArtifactStore，通过 `content_ref` 读取。

```python
class ArtifactStore:
    def write(self, artifact: AgentArtifact, policy: AgentPolicy) -> str: ...
    def read(self, artifact_id: str, policy: AgentPolicy) -> AgentArtifact: ...
    def list_by_type(self, thread_id: str, artifact_type: str, policy: AgentPolicy) -> list[AgentArtifact]: ...

def wait_for_artifacts(state: RecruitmentState, required_types: list[str]) -> dict:
    """Return missing artifact types or attach artifact refs to state."""
```

依赖规则：

```text
下游 Agent 必须声明 wait_for_artifact_types。
依赖未满足时不能执行 LLM 调用和工具调用。
依赖超时写入 errors，并返回 human_questions 或 missing_artifacts。
Artifact 读取按 AgentPolicy.allowed_artifact_types_read 控制。
Artifact 写入按 AgentPolicy.allowed_artifact_types_write 控制。
```

并行/串行 gate：

```text
parallel gate：六部会审可并行，但都只写自己的 CouncilOpinion artifact。
serial gate：门下省必须等待六部意见后再挑战。
approval gate：Review Node 后必须 interrupt，再进 action-gateway。
memory gate：长期记忆只能提案，不能静默写入。
context gate：Agent 只能接收 `AgentHarness.build_context_pack` 生成的 ContextPack，不接收完整 state、完整历史或全量记忆。
```

副作用分级：

```text
自动写入：Postgres checkpoint、AgentRuns、RunMemory、ArtifactStore、任务授权后的 War Room 进度卡 / 追问卡 / 确认卡 / 结果卡。
必须人工确认：业务表写入、候选人推荐结论、报告发布、Discord 对外触达发送、飞书/Bitable deferred adapter 写入、外部触达发送。
必须审批：长期记忆写入 ProjectMemory / AgentMemory / CaseMemory / UserCorrectionMemory。
```

ReviewGate 规则：

```text
ReviewGate 是 artifact-level quality gate，不是全局聊天式审查。
artifact_node -> review_gate_for_artifact
pass -> 进入该 artifact 对应 next_node
needs_fix && retry_count=0 -> 只回该 artifact 对应 repair_node
needs_fix && retry_count>=1 -> interrupt 人工确认
needs_human -> interrupt 人工确认
needs_fix 不得重跑完整 graph，也不得重跑无关上游节点。
```

## 10. Graph 约束

- 输入进入 Pydantic request schema。
- 所有入口调用同一个 `headhunter_war_room_graph`。
- 业务子图必须由 `dispatch_from_council` 路由进入。
- 每个节点只更新自己的 state 字段。
- 每个节点追加 `node_history`。
- 每个 Agent 节点必须通过 `AgentHarness.run_agent` 执行。
- 每个 Agent 节点只能把 `ContextPack` 传给 LLM，不能把完整 `RecruitmentState`、完整聊天历史、完整 `node_history`、全量 AgentRuns、全量 artifacts 或全量长期记忆传给 LLM。
- 记忆进入 Agent 前必须经过 `MemoryGateway.retrieve`：policy scope filter -> tenant/guild/user/project/requisition/candidate scope filter -> pgvector 召回 -> 加权/rerank -> MMR 去重 -> token budget 压缩。
- 简历关键词抽取以当前简历/JD artifact 为主；长期记忆只提供 taxonomy、项目规则、用户修正和 SOP，不得覆盖当前 artifact 事实。
- Agent 节点不能直接调用另一个 Agent 节点。
- 下游 Agent 不能重新解析原始任务、原始简历或完整聊天历史，除非 `SchemaConflictReviewer` 明确判定结构化输入不足。
- Agent 节点不能直接访问数据库、公网、Discord SDK、飞书 SDK、模型 API。
- 工具调用必须经 `SearchGateway`、`DatabaseGateway`、`MemoryGateway`、`ChannelGateway`、`DiscordGateway` 或 `ActionGateway`。
- LLM 节点失败时写 `errors`，不吞异常。
- Review Node 后才能进入 `interrupt()`。
- `interrupt()` resume 后才能执行写入、发消息、创建文档、创建任务。
- graph 完成后更新 `AgentRuns`。
- 每次调用必须携带 `config={"configurable": {"thread_id": thread_id}}`。

## 11. 验收标准

- Discord、本地 API、未来 Hermes/MCP 都调用同一根图。
- 正式 graph 运行前必须有用户 double check approve。
- double check approve 后必须冻结 CanonicalTaskBrief version，下游 Agent 只能读冻结版。
- 所有业务 graph 都读取 `CouncilDecision`。
- 每次运行都返回并展示 `council_mode` 和 `mode_reason`。
- `full_council` 模式下并行六部意见不会覆盖，全部进入 `department_opinions`。
- 并行 Agent 输出不会覆盖，全部进入 `artifacts` 和 `ArtifactStore`。
- 下游 Agent 缺少上游 artifact 时必须等待或追问，不得猜测。
- 每个 Agent 的工具、skills、token 都由 `AgentHarness` 记录。
- 未人工确认不得执行副作用。
- 每条 graph 可单测。
- 每次运行可用 `thread_id` 恢复和查询状态。
- 每个 Agent 的 ContextPack 可审计，且只包含当前节点所需 artifact refs、memory refs、source refs 和预算信息。
- 每个关键产物都有 artifact-level ReviewGate 结果，且 `needs_fix` 只回对应 repair_node 一次，第二次失败必须 interrupt 人工确认。
