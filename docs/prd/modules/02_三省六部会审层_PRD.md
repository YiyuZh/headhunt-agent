# 模块 02：三省六部需求会审层 PRD

## 1. 模块目标

`council_deliberation_graph` 是所有用户输入的前置入口。它先根据任务复杂度、风险、用户意图和预算选择 `council_mode`，再组织对应 Agent 对用户需求做结构化讨论、互相校验、发现缺口、形成统一作战单，最后进入具体业务 graph。

注意：这里的“部门 Agent”不是让用户在 Codex 里手动调用，也不是自由聊天机器人。它们在运行时由 `FastAPI + LangGraph` 根图调度，可以实现为 LangGraph node，也可以进一步服务化为隔离容器。无论哪种实现，都必须经过 `policy-engine`、`AgentHarness`、`ArtifactStore` 和授权 Gateway。Codex 只负责开发这些节点和测试，不负责生产运行。

核心变化：

```text
用户输入
-> policy-engine 创建 TaskPlan / 权限 / 预算
-> 选择 council_mode
-> 会审
-> AgentArtifact 结构化沉淀
-> CouncilDecision
-> 业务 graph 或追问用户
```

`council_mode` 取值：

```text
triage：只做意图、缺字段、风险初筛；适合明显缺信息或明显不安全请求。
lite：运行 IntentRouterAgent、StrategyDraftAgent、规则版 ComplianceRiskAgent、CouncilSynthesizerAgent；适合低风险简单任务。
standard：运行核心会审 Agent，按任务类型选择必要部门和可选部门；适合默认业务请求。
full_council：完整三省六部会审；适合高风险、高价值、高不确定任务，或用户明确要求“三省六部”。
```

规则：

- `PolicyEngine` 和 `AgentHarness` 默认自动选择 `council_mode`。
- 用户输入明确包含“三省六部”“完整会审”“全量会审”等要求时，必须强制 `full_council`。
- 每次响应和 War Room 卡片都必须展示本次使用的 `council_mode` 和 `mode_reason`。

## 2. 部门 Agent

| 部门 | Agent | 职责 |
|---|---|---|
| 太子 | `IntentRouterAgent` | 判断用户是在发 JD、给案例数据、要 Mapping、筛候选人、写话术、做报告，还是问系统规划 |
| 中书省 | `StrategyDraftAgent` | 把用户需求翻译成初版作战目标、执行路径和所需数据 |
| 门下省 | `ChallengeReviewAgent` | 专门挑错：歧义、缺字段、合规风险、错误 Mapping 风险 |
| 尚书省 | `ExecutionDispatchAgent` | 根据会审结论派发到具体 graph，并定义执行顺序 |
| 吏部 | `CandidateJudgementAgent` | 从候选人画像、人岗匹配、证据链角度审查需求 |
| 户部 | `MarketCompAgent` | 从薪资、市场、目标公司、人才供给角度审查需求 |
| 礼部 | `OutreachValueAgent` | 从岗位卖点、触达、客户推荐表达角度审查需求 |
| 兵部 | `SourcingMappingAgent` | 从人才地图、渠道、title 变体、搜索策略角度审查需求 |
| 刑部 | `ComplianceRiskAgent` | 从隐私、敏感属性、证据不足、平台边界角度审查需求 |
| 工部 | `DataAutomationAgent` | 从数据导入、飞书工作台、数据库、自动化可行性角度审查需求 |
| 军机处 | `CouncilSynthesizerAgent` | 汇总意见，输出最终 `CouncilDecision` |

## 3. 接口模型

```python
class CouncilOpinion(BaseModel):
    department: str
    interpretation: str
    artifact_refs: list[str] = []
    tool_refs: list[str] = []
    required_inputs: list[str] = []
    risks: list[str] = []
    recommendations: list[str] = []
    blockers: list[str] = []
    confidence: float

class CouncilDecision(BaseModel):
    intent: str
    task_type: str
    council_mode: Literal["triage", "lite", "standard", "full_council"]
    mode_reason: str
    agreed_goal: str
    execution_graph: str
    required_inputs: list[str] = []
    missing_inputs: list[str] = []
    department_opinions: list[CouncilOpinion] = []
    risks: list[str] = []
    human_questions: list[str] = []
    ready_to_execute: bool = False
    next_action: Literal["execute", "ask_user", "reject", "save_as_case_data"]
```

会审层还必须使用跨 Agent 产物模型：

```python
class AgentArtifact(BaseModel):
    artifact_id: str
    run_id: str
    thread_id: str
    producer_agent: str
    artifact_type: str
    summary: str
    content_ref: str
    evidence_refs: list[str]
    source_refs: list[str]
    pii_level: Literal["none", "low", "medium", "high"]
    version: int
    size_tokens_estimate: int
    created_at: datetime

class AgentTask(BaseModel):
    task_id: str
    agent_name: str
    operation: str
    input_artifacts: list[str]
    wait_for_artifact_types: list[str]
    output_artifact_type: str
    priority: int
    weight: float
    budget: dict
```

`RecruitmentState` 必须包含：

```python
council_decision: dict | None
department_opinions: list[dict]
human_questions: list[str]
ready_to_execute: bool
task_plan: dict | None
policy_snapshot: dict | None
artifacts: list[dict]
pending_artifact_types: list[str]
visibility_mode: Literal["brief", "standard", "debug"]
council_mode: Literal["triage", "lite", "standard", "full_council"]
mode_reason: str
```

## 4. 工作流

`council_deliberation_graph` 作为 `headhunter_war_room_graph` 的前置子图运行：

```text
START
-> receive_user_input
-> create_task_plan_and_policy
-> select_council_mode
-> intent_router_agent
-> strategy_draft_agent
-> write_strategy_draft_artifact
-> run_mode_selected_review
-> challenge_review_agent
-> council_synthesizer_agent
-> if ready_to_execute:
       dispatch_to_business_graph
   else:
       ask_user_for_missing_info
-> END
```

`run_mode_selected_review` 必须按 `council_mode` 选择 Agent：

```text
triage：IntentRouterAgent + ComplianceRiskAgent + CouncilSynthesizerAgent
lite：IntentRouterAgent + StrategyDraftAgent + ComplianceRiskAgent + CouncilSynthesizerAgent
standard：按 task_type 选择 2-4 个必要部门 Agent，并保留 ChallengeReviewAgent
full_council：运行六部并行 + ChallengeReviewAgent + CouncilSynthesizerAgent
```

并行六部：

```text
吏部：候选人/匹配视角
户部：市场/薪资/供给视角
礼部：话术/卖点/客户表达视角
兵部：Mapping/渠道/搜索视角
刑部：合规/风险/证据视角
工部：数据/飞书/Bitable/自动化视角
```

并行会审不是自由聊天。六部 Agent 的输入只能来自：

```text
1. 原始 user_input 的受控摘要
2. StrategyDraftArtifact
3. policy 允许读取的数据库片段
4. policy 允许的搜索结果 source_refs
5. policy 允许的记忆检索结果 memory_refs
```

并行六部 Agent 不得接收完整聊天历史、完整 `RecruitmentState`、完整 `node_history`、全量 AgentRuns、全量 artifacts 或全量长期记忆。即使用户要求“三省六部”或 debug 展示，也只是增加参与 Agent 和可审计 refs，不改变最小 ContextPack 注入规则。

六部 Agent 的输出必须写入 `ArtifactStore`，再由 `ChallengeReviewAgent` 和 `CouncilSynthesizerAgent` 读取。

LangGraph state 只能保存 artifact 的 `artifact_id`、`summary`、`content_ref`、`pii_level`、`version` 和证据引用。下游 Agent 需要全文时，必须经 `ArtifactStore.read(content_ref, policy)` 读取。

## 5. Artifact 依赖规则

```text
StrategyDraftAgent 产出 StrategyDraftArtifact。
SourcingMappingAgent 必须等待 StrategyDraftArtifact。
MarketCompAgent 可等待 TalentMapDraft 或目标公司假设。
CandidateJudgementAgent 必须等待 Requisition / CandidateProfile。
ComplianceRiskAgent 必须读取所有 draft 的脱敏 artifact。
CouncilSynthesizerAgent 必须等待六部意见和门下省挑战意见。
```

任何下游 Agent 缺少上游 artifact 时，不得猜测执行，必须输出：

```text
missing_artifacts
human_questions
ready_to_execute=false
```

## 6. 飞书 / ChannelGateway 可视化规则

会审层必须把结构化过程同步到飞书 War Room；Bitable 只作为人工确认后的业务展示和同步表：

```text
会审模式卡片：council_mode、mode_reason、required_agents、optional_agents
IntentRouterAgent：任务识别卡片
StrategyDraftAgent：初版作战目标卡片
模式选中的部门 Agent：部门意见卡片
ChallengeReviewAgent：缺口和反驳卡片
CouncilSynthesizerAgent：CouncilDecision 卡片
```

任务授权后，War Room 进度卡、追问卡、确认卡和结果卡允许自动发送；它们展示的是结构化摘要、证据、工具轨迹、artifact 引用、`council_mode` 和风险，不展示不可控原始内部推理。

用户可以在飞书中：

```text
评论某个 Agent 卡片
修改某个建议
要求指定 AgentTask 重跑
批准 CouncilDecision 进入业务子图
切换 brief / standard / debug 展示模式
```

## 7. 行为规则

- 会审层不执行业务写入、报告生成或候选人推荐。
- 会审层只产出 `CouncilDecision`、追问或拒绝原因。
- `ready_to_execute=false` 时只能追问用户。
- `next_action=reject` 时写审计并提供合规替代方案。
- `next_action=save_as_case_data` 时进入数据导入流程。
- 业务 graph 只能读取 `CouncilDecision`，不能直接解析原始用户输入。
- 飞书、本地内部 API、未来 Hermes/MCP 入口都必须先进入会审层。
- 每次会审必须记录 `council_mode`、`mode_reason`、实际运行 Agent 列表和未运行 Agent 原因。
- 每次会审必须记录各 Agent 实际注入的 artifact_refs、memory_refs、source_refs 和被排除上下文原因。
- 部门 Agent 不能直接互相调用。
- 部门 Agent 不能直接访问数据库、公网、飞书、Discord optional adapter 或模型 Key。
- 部门 Agent 只能通过 `SearchGateway`、`DatabaseGateway`、`MemoryGateway`、`LLMGateway` 访问授权资源。
- 部门 Agent 可以读取上游 artifacts，但必须受 `AgentPolicy.allowed_artifact_types_read` 限制。
- 部门 Agent 可以读取长期记忆检索结果，但必须受 `AgentPolicy.allowed_memory_scopes`、`max_memory_items` 和 `max_context_tokens` 限制。
- 部门 Agent 输出必须包含 `artifact_refs` 或说明没有可引用 artifact 的原因。

## 8. 示例

输入：

```text
帮我做一个 AI Infra Engineer 的人才地图
```

输出：

```text
已识别任务：talent_mapping
会审模式：triage
模式原因：缺少城市、职级、薪资、技术栈和目标公司，先追问比直接 Mapping 更省 token 且更可靠。
建议执行图：talent_mapping_graph
缺失信息：
- 城市/远程要求
- 职级范围
- 薪资范围
- 必须技术栈
- 已知目标公司或禁挖公司
- 候选人来源是否只限授权数据

追问：
1. 岗位偏 RAG 工程化、模型平台，还是 AI Infra 底层？
2. 是否有目标公司或禁挖公司？
3. 是先生成搜索策略，还是已有候选人池要导入？
```

## 9. 验收标准

- 模糊输入必须输出 `human_questions`，不得直接执行。
- 完整 JD 必须生成 `CouncilDecision` 并路由到 `intake_calibration_graph`。
- 每次输出必须包含 `council_mode` 和 `mode_reason`。
- 用户明确要求“三省六部”时，六部意见必须全部进入 `department_opinions` 并写入 `ArtifactStore`。
- `standard` 和 `full_council` 模式下，门下省必须发现至少一个缺口、风险或确认点。
- 刑部发现敏感属性、批量抓取或自动群发时，`next_action` 必须为 `reject` 或 `ask_user`。
- 任一部门 Agent 越权访问工具、artifact 或数据库时必须被拒绝并写入审计。
- 飞书 War Room 能展示会审模式、模式原因和本次实际运行 Agent 的结构化卡片。
