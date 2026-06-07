# 模块 08：工程代码设计 PRD

## 1. 架构选择

采用模块化单体先跑通，边界按未来服务化设计。

```text
app/api          FastAPI 路由和请求/响应模型
app/runtime      LangGraph 根图创建、PostgreSQL checkpointer、thread_id、interrupt/resume
app/channels     ChannelGateway 抽象，统一飞书主入口与 Discord optional adapter
app/feishu       飞书事件、卡片、Bitable、outbox、War Room 消息
app/council      三省六部角色审查矩阵、意见模型、汇总器、派发器
app/domain       业务模型、枚举、评分规则
app/graphs       LangGraph 工作流定义
app/nodes        可测试节点函数
app/services     业务服务和 use cases
app/tools        外部工具适配器，主链路真实接入，测试可 mock
app/gateways     SearchGateway、DatabaseGateway、MemoryGateway、ActionGateway、FeishuGateway、FeishuBitableGateway
app/policy       AgentPolicy、TaskPlan、权限校验、预算校验
app/harness      AgentHarness、AgentSOPRegistry、AgentTask 调度
app/artifacts    ArtifactStore、AgentArtifact、版本和证据引用
app/memory       pgvector 记忆模型、检索、写入审批、时间治理
app/review       ReviewGate、SchemaValidator、LLM reviewer、合规规则
app/adapters     DiscordAdapter(optional)、HermesAdapter、MCPAdapter 等入口适配器
app/storage      SQLAlchemy repository、migrations、seed import
app/schemas      Pydantic I/O schema
app/prompts      prompt 模板
tests            单元、图、合规、Gateway fake/mock、真实接入契约测试
```

第一版主工作台是飞书。飞书事件、交互卡片、War Room 群消息和 Bitable 同步是第一版主链路；Discord 只作为后续 optional adapter，现有 Discord 代码可保留但不作为默认入口。

## 2. 依赖方向

```text
api -> services -> runtime -> graphs -> council/nodes -> domain
api -> channels -> services
services -> storage
nodes -> harness interfaces
harness -> policy + artifacts + gateways + memory + sop registry
gateways implementations -> external APIs
adapters -> services
review -> domain + schemas + artifacts
feishu -> channels + services
```

禁止：

```text
nodes 直接 import 飞书 SDK、Discord SDK 或公网 SDK
nodes 直接写数据库
nodes 直接访问公网
nodes 直接调用其他 Agent
agent-* 服务直接持有模型 Key、飞书 Key、Discord Key、数据库写凭证
业务 graph 直接解析原始用户输入并绕过 CanonicalTaskBrief
业务 API 绕过 headhunter_war_room_graph 直接调用子图
LLM 输出绕过 Pydantic
工具函数绕过 interrupt 执行业务副作用
测试依赖真实飞书、Discord 或模型配置
Hermes/MCP 直接写数据库或直接调用飞书 / Discord SDK
下游 Agent 重新理解原始简历、原始任务或完整聊天历史
```

## 3. Feishu First 入口边界

飞书负责普通用户入口、War Room 交互和 Bitable 同步展示，但不承载业务判断。

```text
飞书事件回调 / 交互卡片 / 表单
-> POST /feishu/events + POST /feishu/card-actions
-> FeishuCallbackVerifier 校验事件与卡片签名
-> 3 秒内 ACK / toast
-> feishu_event_logs / feishu_card_actions + feishu_outbox 幂等落库
-> worker 异步处理
-> TaskIntakeParser 生成结构化任务
-> 飞书任务确认卡 double check
-> 用户 approve / edit / reject
-> approve 后才进入 headhunter_war_room_graph
```

v1 不依赖普通聊天历史作为主上下文。任务输入优先来自飞书 @机器人消息、交互卡片字段、附件引用和卡片按钮回调。

## 4. 关键接口

```python
class ChannelGateway: ...
class FeishuGateway: ...
class FeishuCallbackVerifier: ...
class FeishuEventRepository: ...
class FeishuOutboxDispatcher: ...
class TaskIntakeParser: ...
class TaskDoubleCheckService: ...
class RequisitionRepository: ...
class TalentMapRepository: ...
class CandidateRepository: ...
class AuditRepository: ...
class LLMGateway: ...
class SearchGateway: ...
class DatabaseGateway: ...
class MemoryGateway: ...
class VectorMemoryStore: ...
class EmbeddingGateway: ...
class ActionGateway: ...
class ArtifactStore: ...
class PolicyEngine: ...
class AgentHarness: ...
class AgentSOPRegistry: ...
class ReviewGate: ...
class CouncilDeliberationService: ...
class RuntimeGraphFactory: ...
class DiscordGateway: ...  # optional adapter
class HermesAdapter: ...
```

运行时工厂：

```python
class RuntimeGraphFactory:
    def create_headhunter_war_room_graph(self):
        """Build and compile the root LangGraph graph."""

    def checkpointer(self):
        """Return PostgreSQL checkpointer for first-version runtime."""
```

网关规则：

```text
ChannelGateway 只表达发送消息、更新消息、创建 thread、打开 modal、读取回调上下文等跨渠道语义。
FeishuGateway 只封装飞书消息、交互卡片、卡片回传、限频、权限和发送更新。
FeishuCallbackVerifier 只负责飞书事件和卡片回调签名、timestamp、nonce、verification token 和 encrypt key 校验。
FeishuEventRepository 只负责 event、card action、message map、outbox 的幂等落库和状态流转。
FeishuOutboxDispatcher 只负责快速 ACK 后的异步发送、更新、Bitable 写入、重试和 dead letter。
TaskIntakeParser 只把用户输入转为 CanonicalTaskBrief 等结构化 artifact，不启动正式业务 graph。
TaskDoubleCheckService 只处理 approve / edit / reject，不绕过 schema 校验。
LLMGateway 只封装模型调用和结构化输出。
SearchGateway 只封装授权搜索和 source_refs。
DatabaseGateway 只封装授权查询、脱敏和 audit。
MemoryGateway 只封装记忆检索、写入提案、审批、裁剪、时间治理和检索审计。
VectorMemoryStore 只封装 pgvector 写入、混合检索、MMR 去重和向量索引查询；Qdrant / Milvus 只保留适配接口。
EmbeddingGateway 只封装 embedding 生成、批处理、content_hash 去重和模型版本记录。
ActionGateway 只执行 interrupt approve 后的副作用。
DiscordGateway 只作为 optional adapter，不进入第一版主验收路径。
```

## 5. 核心模型

```python
class CanonicalTaskBrief(BaseModel):
    task_id: str
    version: int
    task_type: Literal["new_requisition", "candidate_screen", "talent_map", "outreach", "report", "followup", "case_import"]
    facts: dict
    user_goal: str
    role_summary: str | None = None
    candidate_summary: str | None = None
    output_goal: str
    missing_fields: list[str]
    risk_level: Literal["low", "medium", "high"]
    recommended_council_mode: Literal["triage", "lite", "standard", "full_council"]
    source_refs: list[str]
    assumptions: list[str]
    field_sources: dict[str, list[str]]
    field_confidence: dict[str, float]
    confidence: float
    double_check_status: Literal["pending_user_check", "approved", "edited", "rejected", "expired"]
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    edit_history_ref: str | None = None
    frozen_at: datetime | None = None

class TaskDoubleCheckState(BaseModel):
    task_id: str
    channel: Literal["discord", "api", "feishu"]
    status: Literal["pending_user_check", "approved", "edited", "rejected", "expired"]
    canonical_task_brief_ref: str
    frozen_canonical_task_brief_ref: str | None = None
    latest_preview: dict
    reviewer_user_id: str | None = None
    decision_reason: str | None = None
    decided_at: datetime | None = None
    edit_history_ref: str | None = None

class AgentPolicy(BaseModel):
    agent_name: str
    role: str
    allowed_operations: list[str]
    allowed_tools: list[str]
    allowed_sop_scopes: list[str]
    allowed_artifact_types_read: list[str]
    allowed_artifact_types_write: list[str]
    allowed_db_scopes: list[str]
    allowed_web_scopes: list[str]
    allowed_memory_scopes: list[str]
    max_memory_items: int
    max_context_tokens: int
    can_read_memory_content: bool = False
    can_call_agents: bool = False
    can_execute_side_effects: bool = False
    max_tokens_per_task: int
    max_tool_calls_per_task: int
    requires_human_review: bool = True

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

class SOPResolutionAudit(BaseModel):
    thread_id: str
    run_id: str | None
    agent_name: str
    operation: str
    task_type: str
    selected_sop_refs: list[SOPRef]
    excluded_sop_refs: list[dict]
    excluded_reason: list[str]
    policy_version: str

class MemoryItem(BaseModel):
    memory_id: str
    scope: Literal["RunMemory", "ProjectMemory", "AgentMemory", "CaseMemory", "UserCorrectionMemory", "ProceduralMemory"]
    owner_agent: str | None = None
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

class ContextPack(BaseModel):
    task_brief: CanonicalTaskBrief
    node_goal: str
    council_mode: Literal["triage", "lite", "standard", "full_council"]
    mode_reason: str
    artifact_refs: list[dict]
    memory_refs: list[MemoryRef]
    sop_refs: list[SOPRef]
    source_refs: list[str]
    budget_remaining: dict
    excluded_context_reason: list[str]

class ReviewResult(BaseModel):
    review_id: str
    artifact_id: str
    artifact_type: str
    reviewers: list[str]
    status: Literal["pass", "needs_fix", "needs_human"]
    scores: dict
    thresholds: dict
    failed_fields: list[str]
    fix_suggestions: list[str]
    evidence_refs: list[str]
    source_refs: list[str]
    repair_node: str | None = None
    retry_count: int

class FeishuCallbackEnvelope(BaseModel):
    interaction_id: str
    interaction_token_ref: str
    application_id: str
    guild_id: str | None = None
    channel_id: str | None = None
    user_id: str
    interaction_type: str
    command_name: str | None = None
    custom_id: str | None = None
    idempotency_key: str
    raw_payload_ref: str
    status: Literal["received", "duplicate", "queued", "claimed", "running", "succeeded", "retrying", "failed", "dead_letter"]
    received_at: datetime
    ack_at: datetime | None = None
```

## 6. AgentHarness 和最小上下文

```python
class AgentHarness:
    def plan(self, canonical_task: CanonicalTaskBrief) -> TaskPlan: ...
    def build_context_pack(self, task: AgentTask, policy: AgentPolicy) -> ContextPack: ...
    def run_agent(self, agent_name: str, operation: str, payload: dict) -> AgentArtifact: ...
    def score_artifact(self, artifact: AgentArtifact, rubric: Rubric) -> Score: ...
    def enforce_budget(self, task_id: str) -> None: ...
```

`build_context_pack` 必须只给 Agent 注入当前节点必要内容：

```text
CanonicalTaskBrief 的允许字段
node_goal
council_mode / mode_reason
allowed_artifact_summaries
content_refs
top_k_memory_refs
source_refs
sop_refs
budget_remaining
excluded_context_reason
```

禁止注入：

```text
完整聊天历史
完整 RecruitmentState
完整 node_history
全量 AgentRuns
全量 artifacts
完整 artifact 内容
全量长期记忆
上游 Agent 原始 prompt
上游 Agent 原始 ContextPack
```

下游 Agent 默认不得重新解析原始简历或原始任务意图。只有 `SchemaConflictReviewer` 判定结构化输入不足时，才允许触发补充解析或向用户追问。

## 7. SOPRegistry

SOP 不是外部框架运行时依赖，而是本项目自己的版本化规则层。

```python
class AgentSOP(BaseModel):
    sop_id: str
    version: str
    title: str
    scope: str
    purpose: str
    required_inputs: list[str]
    steps: list[str]
    constraints: list[str]
    output_schema_ref: str
    review_rules: list[str]
    trigger_policy: Literal["always", "auto_attached", "agent_requested", "manual"]
    content_ref: str

class AgentSOPRegistry:
    def resolve(
        self,
        agent_name: str,
        operation: str,
        task_type: str,
        policy: AgentPolicy,
        task: CanonicalTaskBrief,
    ) -> list[SOPRef]: ...
```

外部方法吸收：

```text
Agent SOP：借鉴 Markdown SOP、参数化输入、MUST/SHOULD/MAY、步骤化执行和 scratchpad 分层。
BMAD：借鉴 Analysis -> Planning -> Solutioning -> Implementation 的阶段化产物链和 validation checklist。
Claude Code subagents：借鉴 task-specific workflow、frontmatter description、tool scope，用于 allowlist。
Cursor Rules：借鉴 Always / Auto Attached / Agent Requested / Manual 触发模型，映射为 SOPTriggerPolicy。
```

注入限制：

```text
每个节点最多 1 个主 SOP + 2 个审查 SOP。
ContextPack 只注入 SOPRef 摘要和 content_ref，不注入全部 SOP 库。
debug 展示也只展示 SOP id、版本和命中原因。
SOP 触发优先级为 always -> auto_attached -> agent_requested -> manual。
manual SOP 只能由用户、管理员或人工审批指定，Agent 不得自行提升。
auto_attached 必须由 task_type / artifact_type / agent_name / risk_level 命中。
每次 resolve 必须写 SOPResolutionAudit。
```

## 8. ReviewGate

ReviewGate 是 artifact-level quality gate，不是全局聊天式审查。每个关键产物后必须进入对应 ReviewGate，并通过 conditional edge 路由。

```python
class ReviewGate:
    def review(self, artifact: AgentArtifact, context: ContextPack, policy: AgentPolicy) -> ReviewResult: ...
```

LangGraph 路由：

```text
artifact_node -> review_gate_for_artifact
pass -> next_node
needs_fix -> repair_node_for_this_artifact
needs_human -> interrupt_human_approval
repair_node_for_this_artifact -> review_gate_for_artifact
第二次 needs_fix -> interrupt_human_approval
```

审查者：

```text
SchemaValidator：确定性校验 JSON 字段、类型、枚举、必填项。
EvidenceConsistencyReviewer：检查结论是否有 evidence_refs / source_refs 支撑。
PracticalityReviewer：检查话术/报告是否可执行。
ContextBudgetReviewer：检查是否传入完整历史、完整 state、过量 memory。
SafetyReviewer：检查隐私、外部触达、候选人推荐结论是否越过人工确认。
```

`PracticalityReviewer` 评分维度：

```text
个性化
价值主张
行动请求清晰度
猎头语气
合规风险
简洁度
```

结果：

```text
pass：进入下一节点。
needs_fix：只回对应 repair_node 自动修一次。
needs_human：interrupt 人工确认。
第二次 needs_fix 仍失败：升级 needs_human。
needs_fix 不允许重跑完整 graph，也不允许重跑无关上游节点。
```

## 9. Use Case

```python
class HandleFeishuCallbackUseCase:
    def run(self, envelope: FeishuCallbackEnvelope) -> dict: ...

class ParseTaskIntakeUseCase:
    def run(self, user_input: dict, channel_context: dict) -> CanonicalTaskBrief: ...

class ApproveTaskDoubleCheckUseCase:
    def run(self, task_id: str, decision: str, edited_payload: dict | None) -> TaskDoubleCheckState: ...

class InvokeWarRoomGraphUseCase:
    def run(self, confirmed_task: CanonicalTaskBrief, thread_id: str) -> dict: ...

class ResumeWarRoomGraphUseCase:
    def run(self, thread_id: str, human_approval: dict) -> dict: ...

class CalibrateRequisitionUseCase:
    def run(self, council_decision: dict) -> dict: ...

class BuildTalentMapUseCase:
    def run(self, council_decision: dict, requisition_id: str) -> dict: ...

class ScreenCandidateUseCase:
    def run(self, council_decision: dict, candidate_input: dict) -> dict: ...

class GenerateOutreachOrReportUseCase:
    def run(self, council_decision: dict, output_type: str) -> dict: ...

class ApproveMemoryProposalUseCase:
    def run(self, proposal_id: str, reviewer: str, decision: str) -> dict: ...
```

## 10. LangGraph 构建路径

正式 graph 只接收用户 double check 后的结构化任务。

```python
def create_headhunter_war_room_graph(deps: AppDependencies):
    builder = StateGraph(RecruitmentState)

    builder.add_node("load_confirmed_canonical_task", deps.nodes.load_confirmed_canonical_task)
    builder.add_node("create_task_plan_and_policy", deps.nodes.create_task_plan_and_policy)
    builder.add_node("council_deliberation_graph", deps.graphs.council_deliberation)
    builder.add_node("dispatch_from_council", deps.nodes.dispatch_from_council)

    builder.add_node("intake_calibration_graph", deps.graphs.intake_calibration)
    builder.add_node("talent_mapping_graph", deps.graphs.talent_mapping)
    builder.add_node("candidate_screening_graph", deps.graphs.candidate_screening)
    builder.add_node("outreach_and_report_graph", deps.graphs.outreach_and_report)
    builder.add_node("followup_review_graph", deps.graphs.followup_review)
    builder.add_node("review_gate_for_artifact", deps.nodes.review_gate_for_artifact)
    builder.add_node("repair_artifact", deps.nodes.repair_artifact)
    builder.add_node("ask_user_for_missing_info", deps.nodes.ask_user_for_missing_info)
    builder.add_node("reject_with_reason", deps.nodes.reject_with_reason)
    builder.add_node("save_as_case_data", deps.nodes.save_as_case_data)

    builder.add_edge(START, "load_confirmed_canonical_task")
    builder.add_edge("load_confirmed_canonical_task", "create_task_plan_and_policy")
    builder.add_edge("create_task_plan_and_policy", "council_deliberation_graph")
    builder.add_conditional_edges("council_deliberation_graph", deps.nodes.dispatch_from_council)

    return builder.compile(checkpointer=deps.checkpointer)
```

ReviewGate conditional edge 约束：

```text
每个关键 artifact 节点只连接自己的 review_gate_for_artifact。
review_gate_for_artifact 返回 pass / needs_fix / needs_human。
pass 进入该 artifact 对应的 next_node。
needs_fix 只进入该 artifact 对应 repair_artifact，retry_count 上限为 1。
repair_artifact 不得重新运行完整 graph，只读取原 artifact、ReviewResult、冻结 CanonicalTaskBrief 和必要 refs。
第二次仍 needs_fix 或 SafetyReviewer needs_human 时进入 interrupt_human_approval。
```

每次调用：

```python
graph.invoke(
    input_state,
    config={"configurable": {"thread_id": thread_id}},
)
```

人工确认恢复：

```python
graph.invoke(
    Command(resume=human_approval),
    config={"configurable": {"thread_id": thread_id}},
)
```

## 11. 最小 API

```text
POST /feishu/events + POST /feishu/card-actions
POST /council/deliberate
POST /requisitions/calibrate
POST /requisitions/{requisition_id}/talent-map
POST /candidates/import
POST /candidates/{candidate_id}/screen
POST /candidates/{candidate_id}/outreach
POST /candidates/{candidate_id}/report
POST /followups/today
POST /human-approval/{thread_id}
POST /data/import/case
POST /data/import/csv
POST /data/import/json
POST /agent-tasks/{thread_id}/{task_id}/rerun
POST /memory-proposals/{proposal_id}/review
GET /runs/{run_id}
GET /threads/{thread_id}/state
GET /threads/{thread_id}/artifacts
GET /data/import-runs/{run_id}
```

规则：

```text
/feishu/events 和 /feishu/card-actions 是第一版用户入口，只做签名校验、ACK/toast、幂等落库和 outbox。
/council/deliberate 是内部调试和 API 入口，也必须先生成 CanonicalTaskBrief 和 double check 状态。
业务 API 可以保留，方便本地测试和自动化，但不能绕过 CouncilDecision、ReviewGate 或 interrupt。
/human-approval/{thread_id} 仅作为内部调试或后台 resume API，不作为飞书卡片对外入口。
```

## 12. 飞书调用路径

飞书事件：

```text
POST /feishu/events + POST /feishu/card-actions
-> FeishuCallbackVerifier.verify(raw_body, headers)
-> FeishuEventRepository.insert_or_mark_duplicate(event_id/idempotency_key)
-> persist outbox item and commit before ACK
-> return ACK or toast within 3 seconds
-> FeishuOutboxDispatcher.claim_next(envelope)
-> ParseTaskIntakeUseCase.run(...)
-> SchemaValidator 校验 CanonicalTaskBrief
-> FeishuGateway.send_task_double_check_card(...)
```

Double check：

```text
用户 approve
-> 记录 TaskDoubleCheckState(status=approved)
-> 创建或读取飞书 War Room thread_id
-> InvokeWarRoomGraphUseCase.run(confirmed_task, thread_id)

用户 edit
-> 飞书卡片表单收集修改字段
-> 重新生成 CanonicalTaskBrief
-> 再次发送任务确认卡

用户 reject
-> 记录 rejection reason
-> 任务停止
-> 可生成 UserCorrectionMemoryProposal
```

审批按钮：

```text
飞书卡片交互
-> 校验飞书签名
-> 解析 custom_id 或 modal fields 中的 thread_id/action_id/interrupt_id/idempotency_key/decision
-> 3 秒内 ACK/toast
-> 持久 outbox
-> 后台生成 HumanApproval
-> Command(resume=HumanApproval)
-> 异步更新飞书 War Room 卡片和 AgentRuns
```

飞书长期消息更新统一通过 FeishuGateway 和 durable outbox 执行，不依赖回调请求同步完成。

## 13. `.env.example`

```env
APP_ENV=docker
APP_NAME=AI Headhunter Feishu War Room
APP_VERSION=0.1.0
DOMAIN=localhost
HTTP_PORT=80
HTTPS_PORT=443
INTERNAL_ADMIN_API_KEY=change-this-admin-key
DATABASE_WAIT_SECONDS=60

POSTGRES_DB=lietou
POSTGRES_USER=lietou
POSTGRES_PASSWORD=change-this-password
POSTGRES_HOST=postgres
POSTGRES_INTERNAL_PORT=5432
POSTGRES_BIND=127.0.0.1
POSTGRES_PORT=5432

# Docker Compose derives DATABASE_URL and CHECKPOINT_DB_URL from POSTGRES_*.
# Only set DATABASE_URL/CHECKPOINT_DB_URL manually for host-local Python runs.

FEISHU_BASE_URL=https://open.feishu.cn
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_VERIFICATION_TOKEN=
FEISHU_ENCRYPT_KEY=
FEISHU_EVENT_CALLBACK_PATH=/feishu/events
FEISHU_CARD_ACTION_CALLBACK_PATH=/feishu/card-actions
FEISHU_DEFAULT_CHAT_ID=
FEISHU_BITABLE_APP_TOKEN=
FEISHU_BITABLE_TABLE_ID=
FEISHU_OUTBOX_MAX_ATTEMPTS=5

VECTOR_STORE_PROVIDER=pgvector
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSION=1536
EMBEDDING_API_KEY=
MEMORY_RETRIEVAL_TOP_K=5
MEMORY_MAX_TOKENS_PER_AGENT=800
MEMORY_RUN_RETENTION_DAYS=30
MEMORY_LONG_RETENTION_DAYS=90
CONTEXT_PACK_MAX_TOKENS=3000
SOP_REGISTRY_PATH=docs/agent-sops

CHANNEL_GATEWAY_PROVIDER=feishu
DEFAULT_VISIBILITY_MODE=standard
AGENT_DEFAULT_TOKEN_BUDGET=4000

# Feishu BYOK model profiles: users choose provider/model/API key in Feishu cards.
MODEL_SECRET_ENCRYPTION_KEY=change-this-model-secret
MODEL_PROVIDER_ALLOWLIST=openai,deepseek

# Legacy local debug fallback only, not Feishu multi-user main path.
LLM_PROVIDER=
LLM_MODEL=
LLM_API_KEY=
DEEPSEEK_API_KEY=
DEEPSEEK_MODEL=deepseek-v4-pro

```

## 14. 容器化服务边界

工程实现先用模块化单体跑通，但主链路必须直接接入真实 PostgreSQL、PostgreSQL checkpointer、pgvector 和真实飞书事件回调、飞书交互卡片和 Bitable 同步。mock / fake gateway 只用于单元测试、CI 和失败隔离，不作为第一版主运行路径。

未来可拆服务：

```text
orchestrator-api
feishu-gateway
channel-gateway
task-intake-parser
review-gate
sop-registry
agent-intent-router
agent-strategy-draft
agent-challenge-review
agent-candidate-judgement
agent-market-comp
agent-outreach-value
agent-sourcing-mapping
agent-compliance-risk
agent-data-automation
council-synthesizer
model-gateway
search-gateway
database-gateway
memory-gateway
artifact-store
policy-engine
agent-harness
action-gateway
postgres
redis
```

拆分约束：

```text
Agent 服务只暴露 /run_task。
/run_task 输入必须包含 AgentPolicy、AgentTask、ContextPack refs。
/run_task 输出必须是 AgentArtifact。
Agent 服务不能暴露任意工具调用 API。
Gateway 服务必须记录审计。
FeishuGateway 不能直接执行招聘业务副作用；Bitable 写入必须先经过 HumanApproval。
```

## 15. 工程验收

```text
飞书事件与卡片签名校验单测。
/feishu/events 与 /feishu/card-actions 3 秒 ACK/toast 单测。
TaskIntakeParser 生成 CanonicalTaskBrief 单测。
double check approve/edit/reject 单测。
ReviewGate pass / needs_fix / needs_human 单测。
ContextPack 不含完整历史、完整 state、完整 node_history、全量 AgentRuns、全量 artifacts。
MemoryGateway 30d / 90d / permanent retention 单测。
长期记忆未经审批不可 active。
feishu_outbox 幂等、重试和 Bitable 写入单测。
真实飞书后台、Bitable 权限和卡片发送联调未执行前必须写“未验证”。
```
