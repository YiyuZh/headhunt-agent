# 模块 12：Agent 协作、可观测、记忆与 Harness PRD

## 1. 模块目标

本模块定义 Agent 协作操作系统。目标不是让 Agent 自由聊天，而是让每个 Agent 在授权范围内读取结构化输入、等待上游 artifact、使用工具和 SOP、输出结构化 artifact，并让用户在飞书 War Room 里看到可审计、可修改、可复盘的过程。

核心链路：

```text
飞书 / API 用户输入
-> TaskIntakeParser
-> CanonicalTaskBrief / RequisitionBrief / CandidateEvidencePack
-> 用户 double check 并冻结 CanonicalTaskBrief version
-> headhunter_war_room_graph
-> policy-engine 创建 TaskPlan / 权限 / 预算
-> 选择 council_mode
-> council_deliberation_graph 角色审查矩阵
-> AgentHarness 调度部门 Agent
-> AgentSOPRegistry 注入 sop_refs
-> MemoryGateway 注入 memory_refs
-> Agent 产出 Artifact
-> ReviewGate 审查 Artifact
-> 飞书 War Room 展示结构化过程
-> CouncilSynthesizer 汇总 CouncilDecision
-> 业务子图执行
-> interrupt / resume
-> action-gateway 执行被批准的副作用
-> AgentRuns + MemoryProposal 更新
```

## 2. 协作底层逻辑

系统稳定性的来源：

```text
统一事实源：CanonicalTaskBrief 是正式任务入口，且 approve 后冻结 version；下游 Agent 不重新理解原始任务。
结构化交接：Agent 只交换 ArtifactRef、结构化 JSON artifact、ReviewFinding。
上下文最小化：AgentHarness 只传当前节点需要的字段、refs、短摘要和预算。
审查内嵌：ReviewGate 是 artifact-level quality gate，在每个关键产物后运行，失败只回对应 repair_node 一次或进入人工确认。
记忆可控：长期记忆检索式注入，带权限、预算、时间衰减和审批。
```

禁止：

```text
Agent 互相自由聊天。
Agent 把完整 prompt / history / ContextPack 传给下游。
下游 Agent 重新读原始简历或原始任务意图来覆盖冻结版结构化输入。
每轮 loop 塞入完整历史、完整 state、全量 AgentRuns 或全量长期记忆。
CouncilSynthesizer 读取各 Agent 原始 ContextPack。
未经 double check 或未冻结的 CanonicalTaskBrief 进入正式 graph。
无 source 的推断字段进入事实字段。
```

允许的交接：

```text
ArtifactRef
结构化 JSON artifact
ReviewFinding
MemoryRef
SOPRef
SourceRef
```

## 3. ArtifactStore

Agent 之间不直接互相调用，只通过 artifact 交换结构化产物。

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
```

LangGraph state 只保存 `artifact_id`、`summary`、`content_ref`、`evidence_refs`、`source_refs`、`pii_level`、`version` 和 `size_tokens_estimate`。完整 artifact 内容必须存放在 ArtifactStore，并通过 `content_ref` 按 policy 读取。

Artifact 类型：

```text
CanonicalTaskBrief
RequisitionBrief
ResumeProfile
CandidateEvidencePack
OutreachDraftInput
IntentClassification
StrategyDraftArtifact
CouncilOpinion
TalentMapDraft
SearchQueryDraft
MarketSupplyOpinion
CandidateMatchDraft
OutreachDraft
ReportDraft
ComplianceReview
ReviewResult
ReviewFinding
DataAutomationPlan
ActionProposal
CouncilDecision
HumanApproval
UserCorrectionMemoryProposal
```

## 4. Canonical Context 与角色审查矩阵

三省六部保留，但不再解释为多个 Agent 自由沟通。它是同一 Canonical Context 下的角色审查矩阵。

```text
triage：只做必要性、安全性、缺失字段判断。
lite：少量角色审查，适合低风险简单任务。
standard：默认模式，覆盖策略、候选人、合规和可执行性。
full_council：高风险、高价值或用户明确要求“三省六部”时启用。
```

`full_council` 触发条件：

```text
用户明确说“三省六部”。
任务风险 high。
候选人推荐结论会进入外部业务记录。
外部触达或敏感话术即将发送。
高价值岗位或信息冲突明显。
```

每个角色读取同一份已冻结 `CanonicalTaskBrief` 的允许字段，不能各自重新解析原始输入。这样保留多角色视角，同时降低 token 和理解漂移。

## 5. 依赖与等待

```python
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

依赖示例：

```text
TaskIntakeParser -> CanonicalTaskBrief
TaskDoubleCheckService 等待用户 approve -> frozen CanonicalTaskBrief
StrategyDraftAgent 等待 CanonicalTaskBrief / RequisitionBrief -> StrategyDraftArtifact
SourcingMappingAgent 等待 StrategyDraftArtifact -> TalentMapDraft
MarketCompAgent 等待 TalentMapDraft -> MarketSupplyOpinion
CandidateJudgementAgent 等待 RequisitionBrief + ResumeProfile + CandidateEvidencePack -> CandidateMatchDraft
OutreachValueAgent 等待 CandidateMatchDraft + OutreachDraftInput -> OutreachDraft
ReviewGate 等待每个关键 draft -> ReviewResult
ComplianceRiskAgent 等待所有 draft 摘要 -> ComplianceReview
CouncilSynthesizer 等待全部 opinions / ReviewResult 摘要 -> CouncilDecision
```

LangGraph 节点不得轮询数据库。等待逻辑由 `wait_for_artifacts` 节点和 checkpointer 管理，超时后写入 `errors` 并向用户返回缺失依赖。

## 6. Web Search / Database / Memory

Agent 允许上网，但必须走 `SearchGateway`：

```python
class SearchGateway:
    def search(self, query: str, policy: AgentPolicy, purpose: str) -> list[SearchResult]: ...
```

规则：

```text
必须记录 query、purpose、agent_name、source_url。
必须返回 source_refs。
高时效信息必须搜索。
公司背调、当前在职状态、融资、新闻、裁员、组织变化等当前事实必须来自 SearchGateway source_refs。
长期记忆不得替代实时搜索。
招聘候选人搜索只能生成策略，不自动抓取个人数据。
```

数据库访问必须走 `DatabaseGateway`：

```python
class DatabaseGateway:
    def query(self, agent_name: str, scope: str, params: dict) -> list[dict]: ...
```

规则：

```text
按 policy 限制表、字段、行范围。
候选人联系方式默认不可读。
PII 字段默认脱敏。
所有查询写入 audit。
```

记忆访问必须走 `MemoryGateway`。第一版直接启用 PostgreSQL + pgvector，Qdrant / Milvus 只保留适配接口，不作为首版主依赖。

## 7. 记忆体系与时间治理

记忆分层：

```text
short-term：LangGraph checkpointer thread state，只服务当前 thread 恢复。
semantic：项目事实、客户偏好、行业词库。
episodic：历史案例、成功/失败任务片段。
procedural：SOP、提示词改进、审查规则。
user correction：用户在 飞书卡片 edit/reject 中修正过的内容。
RunMemory：单次 AgentRuns 和 artifacts 的短周期运行记忆。
```

结构：

```python
class MemoryItem(BaseModel):
    memory_id: str
    tenant_id: str
    guild_id: str | None
    user_id: str | None
    project_id: str | None
    requisition_id: str | None
    candidate_id: str | None
    scope: Literal["RunMemory", "ProjectMemory", "AgentMemory", "CaseMemory", "UserCorrectionMemory", "ProceduralMemory"]
    owner_agent: str | None = None
    memory_type: Literal["short_term", "semantic", "episodic", "procedural", "user_correction"]
    summary: str
    content_ref: str
    embedding_ref: str
    source_run_id: str
    pii_level: Literal["none", "low", "medium", "high"]
    status: Literal["draft", "pending_review", "active", "revoked", "expired"]
    retention_policy: Literal["30d", "90d", "permanent"]
    confidence: float
    version: int
    expires_at: datetime | None = None
    last_hit_at: datetime | None = None
    renewal_required_at: datetime | None = None
    metadata: dict
```

Retention policy：

```text
RunMemory：默认 30d，到期自动 expired 或只保留审计摘要。
UserCorrectionMemory：默认 90d，命中频繁且人工确认后可升级 permanent。
CaseMemory：默认 90d，高价值案例人工审批后可升级 permanent。
ProjectMemory：默认 90d，客户长期偏好人工续期后可 permanent。
AgentMemory：默认 90d，过期前复盘，低置信自动降权。
ProceduralMemory / SOP：默认 permanent，但必须版本化和可撤销。
```

写入规则：

```text
RunMemory 自动写入并向量化。
长期记忆只能 propose_update。
ProjectMemory、AgentMemory、CaseMemory、UserCorrectionMemory、ProceduralMemory 必须人工审批，审批通过后 status=active 才进入可检索长期记忆池。
飞书 edit、reject 原因、人工改稿可生成 UserCorrectionMemoryProposal。
错误记忆必须可撤销、可标记失效。
过期、撤销、pending_review、pii_level 超 policy、低置信或超预算的记忆不得注入 ContextPack。
```

记忆检索策略固定为：

```text
policy scope filter
-> tenant / guild / user / project / requisition / candidate scope filter
-> active + not expired filter
-> pgvector similarity recall
-> metadata / recency / source_trust / retention weighting
-> rerank
-> MMR dedupe
-> token budget compression
-> ContextPack.memory_refs
```

MemoryGateway 默认只返回 `MemoryRef` 短摘要、相关度、scope、source_run_id 和 content_ref。完整 memory content 必须二次授权读取，且要求 `AgentPolicy.can_read_memory_content=true`。

记忆边界：

```text
tenant_id、guild_id、project_id 缺失时，不得检索跨租户/跨 guild/跨项目长期记忆。
candidate_id 缺失时，不得检索候选人级记忆。
requisition_id 缺失时，不得检索岗位级记忆。
简历关键词抽取以当前简历/JD artifact 为主。
长期记忆只提供 taxonomy、项目规则、用户修正和 SOP，不得覆盖当前简历/JD 事实。
```

## 8. AgentSOPRegistry

SOP 是版本化 Markdown/JSON 文件，包含目的、输入字段、步骤、约束、输出 schema、审查规则和触发策略。

```python
class SOPRef(BaseModel):
    sop_id: str
    version: str
    title: str
    scope: str
    trigger_policy: Literal["always", "auto_attached", "agent_requested", "manual"]
    summary: str
    content_ref: str
    hit_reason: str
    source_refs: list[str]
    token_estimate: int
```

触发策略：

```text
always：该节点固定加载，如 SchemaValidator 的 schema 校验 SOP。
auto_attached：按文件路径、任务类型、artifact 类型自动命中。
agent_requested：AgentHarness 判定需要时加载。
manual：用户或开发者指定时加载。
```

触发优先级：

```text
always -> auto_attached -> agent_requested -> manual
```

外部方法抄入的是结构，不是版权文本：

```text
Agent SOP：Markdown SOP、参数化输入、MUST/SHOULD/MAY、步骤化执行、summary/planning/tasks/scratchpad 分层。
BMAD：Analysis -> Planning -> Solutioning -> Implementation、workflow/agent/skill/trigger 映射、PRD validation checklist、story template。
Claude Code subagents：task-specific workflows、frontmatter description、tool scope，用于 reviewer 与业务 Agent allowlist。
Cursor Rules：规则作用域和 Always / Auto Attached / Agent Requested / Manual 触发模型。
LangGraph：subagents/handoffs 强调明确职责和上下文控制，用 graph 节点矩阵替代自由互聊。
```

注入规则：

```text
每个业务节点只加载 1 个主 SOP 和最多 2 个审查 SOP。
ContextPack 只保存 sop_refs，不保存全部 SOP 全文。
War Room 展示 SOP id、版本、命中原因和 token 估算。
manual SOP 只能由用户、管理员或人工审批指定。
auto_attached 必须由 task_type、artifact_type、agent_name 或 risk_level 命中。
每次 resolve 必须写 SOPResolutionAudit。
```

## 9. ReviewGate

ReviewGate 是每个关键产物后的 artifact-level quality gate，不是全局聊天式审查。

```python
class ReviewResult(BaseModel):
    review_id: str
    artifact_id: str
    artifact_type: str
    status: Literal["pass", "needs_fix", "needs_human"]
    reviewer_results: list[dict]
    scores: dict
    thresholds: dict
    failed_fields: list[str]
    fix_suggestions: list[str]
    evidence_refs: list[str]
    source_refs: list[str]
    repair_node: str | None
    retry_count: int
```

审查分层：

```text
SchemaValidator：确定性校验 JSON 字段、类型、枚举、必填项。
EvidenceConsistencyReviewer：检查结论是否有 evidence_refs / source_refs 支撑。
PracticalityReviewer：检查话术/报告是否可执行。
ContextBudgetReviewer：检查是否传入全历史、全 state、过量 memory。
SafetyReviewer：检查隐私、外部触达、候选人推荐结论是否越过人工确认。
SchemaConflictReviewer：判断结构化输入是否不足，必要时触发补充解析或追问。
```

`PracticalityReviewer` 评分维度：

```text
个性化：是否使用岗位/候选人的具体信息。
价值主张：是否清楚说明机会或建议的实际价值。
行动请求清晰度：是否明确下一步动作。
猎头语气：是否专业、克制、不过度承诺。
合规风险：是否避免敏感、歧视、隐私越界。
简洁度：是否能被真实用户快速阅读和执行。
```

重试规则：

```text
pass：进入下一节点。
needs_fix：只回对应 repair_node 自动修一次。
第二次仍 needs_fix：进入 interrupt_human_approval。
needs_human：interrupt 人工确认。
ReviewResult 本身也进入 ArtifactStore 和 AgentRuns。
```

LangGraph conditional edge：

```text
artifact_node -> review_gate_for_artifact
pass -> next_node
needs_fix -> repair_node_for_this_artifact
needs_human -> interrupt_human_approval
repair_node_for_this_artifact -> review_gate_for_artifact
第二次 needs_fix -> interrupt_human_approval
```

限制：

```text
needs_fix 不得重跑完整 graph。
needs_fix 不得重跑无关上游节点。
repair_node 只能读取原 artifact、ReviewResult、冻结 CanonicalTaskBrief、必要 refs 和审查 SOPRef。
ReviewGate 不读取完整聊天历史、完整 state、其他 Agent 原始 ContextPack 或原始 prompt。
```

## 10. 飞书 War Room 可观测

不展示原始 chain-of-thought，展示可审计工作过程。

一个任务对应一个飞书 War Room 群消息、话题串或消息组。每个 Agent 输出一张结构化卡片：

```text
会审模式
模式原因
Agent 名称
任务目标
读取了哪些 artifacts
注入了哪些 memory_refs
命中了哪些 sop_refs
调用了哪些工具
关键结论
证据来源
风险/缺口
ReviewGate 结果
下一步建议
token 消耗估算
是否需要用户确认
```

用户动作：

```text
approve：形成 HumanApproval。
edit：打开飞书卡片表单，形成 edited_payload 或 UserCorrection artifact。
reject：停止或回退，记录原因。
rerun：只重跑指定 AgentTask，并保留旧 artifact 版本。
view mode：brief / standard / debug。
```

展示模式：

```text
brief：只显示结论、风险、追问。
standard：显示证据、工具、artifact 摘要、memory_refs、sop_refs。
debug：显示完整结构化 artifact 和工具日志，但不展示原始 prompt、隐私全文或 chain-of-thought。
```

默认 `standard`。

任务授权后的进度卡、追问卡、确认卡和结果卡可自动发送，并写入 AgentRuns。

飞书事件和卡片交互也必须可观测：

```text
/feishu/events 和 /feishu/card-actions 快速 ACK，记录 FeishuEventLog 与 FeishuCardAction。
button / modal 记录 custom_id、thread_id、action_id、interrupt_id、idempotency_key、decision 和 user_id。
重复 interaction_id / idempotency_key 标记 duplicate，不重复派发 graph 或 resume。
消息发送、更新、限频退避、权限失败和 token 过期都写入 AgentRuns 摘要。
```

BYOK 模型与记忆路由：

```text
AgentTask.model_profile_id
-> UserModelLLMGateway
-> 只能使用同 guild_id + user_id 的 active chat profile
-> AgentRun 只记录 profile_id/provider/model_name/owner_user_id，不记录 key

AgentTask.embedding_profile_id
-> UserModelMemoryGatewayRouter
-> 只能使用同 guild_id + user_id 的 OpenAI embedding profile
-> 无 profile 时 MemoryGateway skipped/audited，不阻断普通 chat agent
```

## 11. AgentHarness

`AgentHarness` 是 Agent 调度、预算、SOP、评分和复盘的统一执行层。

```python
class AgentHarness:
    def plan(self, task: CanonicalTaskBrief) -> TaskPlan: ...
    def build_context_pack(self, task: AgentTask, policy: AgentPolicy) -> ContextPack: ...
    def run_agent(self, agent_name: str, operation: str, payload: dict) -> AgentArtifact: ...
    def score_artifact(self, artifact: AgentArtifact, rubric: Rubric) -> Score: ...
    def enforce_budget(self, task_id: str) -> None: ...
```

任务权重：

```python
class TaskWeightConfig(BaseModel):
    council_mode: Literal["triage", "lite", "standard", "full_council"]
    mode_reason: str
    user_forced_full_council: bool
    business_value: float
    risk_level: float
    uncertainty: float
    data_completeness: float
    time_sensitivity: float
    token_budget: int
    required_agents: list[str]
    optional_agents: list[str]
```

调度策略：

```text
用户明确要求“三省六部”：强制 full_council。
明显缺信息或明显不安全：triage，优先追问或拒绝。
低风险简单任务：lite，少 Agent，少 token。
普通招聘任务：standard。
高风险任务：自动加入 ComplianceRiskAgent、ChallengeReviewAgent 和 SafetyReviewer。
信息缺失任务：优先追问，不浪费 Mapping token。
高价值岗位：允许更多搜索和更多 Agent 会审。
```

Context allowlist：

| Agent | 允许注入的上下文 |
|---|---|
| `TaskIntakeParser` | 当前 slash command / modal 字段、附件摘要、入口来源 |
| `IntentRouterAgent` | 冻结版 CanonicalTaskBrief 摘要、入口来源、少量 ProjectMemory refs |
| `StrategyDraftAgent` | 冻结版 CanonicalTaskBrief、RequisitionBrief 摘要、必要 ProjectMemory / CaseMemory refs |
| `SourcingMappingAgent` | StrategyDraftArtifact 摘要、技能词库、目标公司/岗位相关 memory_refs、公开 source_refs |
| `MarketCompAgent` | 岗位摘要、薪资/市场相关 CaseMemory refs、公开市场 source_refs |
| `CandidateJudgementAgent` | RequisitionBrief、ResumeProfile、CandidateEvidencePack、TalentMap 摘要、CaseMemory refs；禁止联系方式明文 |
| `OutreachValueAgent` | CandidateMatchDraft 摘要、岗位卖点、UserCorrectionMemory refs；禁止全候选池 |
| `ComplianceRiskAgent` | 所有 draft 的脱敏摘要、风险规则记忆、必要审计 refs |
| `DataAutomationAgent` | 表结构、导入批次、同步状态、DataImportArtifact 摘要 |
| `CouncilSynthesizerAgent` | 各 Agent 的 CouncilOpinion / ComplianceReview / ReviewResult 摘要和 refs；禁止各 Agent 原始 ContextPack |
| `ReviewGate` | 被审 artifact、schema、审查 SOP、必要 evidence/source/memory refs |

## 12. Token 与成本控制

```text
每个 TaskPlan 有总 token_budget。
每个 AgentTask 有 max_tokens_per_task 和 max_tool_calls_per_task。
每个 AgentPolicy 有 max_context_tokens、max_memory_items 和 can_read_memory_content。
每次运行必须记录并展示 council_mode 和 mode_reason。
SearchGateway 默认只返回摘要和 source_refs。
飞书默认 standard 模式，不发送完整 debug artifact。
SOPRegistry 默认只返回 sop_refs，不注入全部 SOP。
超预算时先裁剪低相关 memory_refs，再裁剪 optional source_refs，再裁剪 optional sop_refs，再降级展示模式，最后返回人工追问。
ArtifactStore 负责保存全文；state 和 War Room 默认只传短摘要和 content_ref。
```

## 13. 验收标准

```text
Agent 只能读取 policy 允许的 artifact 类型、memory scope 和 SOP scope。
下游 Agent 必须等待上游 artifact，不得猜测缺失结果。
下游 Agent 不得默认重新解析原始任务或原始简历。
TaskIntakeParser 关键字段必须有 field_source 和 confidence；无 source 推断只能进入 assumptions。
Double Check approve 后冻结 CanonicalTaskBrief version，下游 Agent 只能读取冻结版。
SearchGateway 输出必须有 source_refs。
公司背调当前事实必须由 SearchGateway source_refs 支撑，长期记忆不得替代实时搜索。
DatabaseGateway 默认脱敏候选人联系方式。
MemoryGateway 写长期记忆必须审批。
MemoryGateway 使用 pgvector 主链路，能按 tenant/guild/user/project/requisition/candidate scope filter、policy scope、top_k、retention policy 和 token budget 返回 MemoryRef。
简历关键词抽取以当前简历/JD artifact 为主，长期记忆只提供 taxonomy、项目规则、用户修正和 SOP。
30d / 90d / permanent retention policy 有清理、降权、续期或升级规则。
AgentHarness 构造的 ContextPack 不包含完整历史、完整 state、完整 node_history、全量 AgentRuns、全量 artifacts 或全量长期记忆。
ReviewGate 对关键产物生成 pass / needs_fix / needs_human，并通过 conditional edge 路由。
needs_fix 只回对应 repair_node 一次，不重跑完整 graph；第二次仍失败必须 interrupt_human_approval。
PracticalityReviewer 有可解释评分维度。
飞书 War Room 能看到每个 Agent 的结构化过程、ReviewGate 结果和 token 消耗。
飞书 War Room 和 AgentRuns 能展示本次注入了哪些 memory_refs / sop_refs、命中原因和 token 估算，但不展示完整隐私内容或原始 prompt。
用户能 double check 结构化任务，能修改某个 Agent 的建议并触发指定 AgentTask 重跑。
AgentSOPRegistry 能证明 Agent 只能加载授权 SOP。
AgentHarness 能根据任务权重、风险和用户显式要求自动选择 council_mode、加入或裁剪 Agent。
```
