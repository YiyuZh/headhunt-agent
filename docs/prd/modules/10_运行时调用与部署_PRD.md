# 模块 10：运行时调用与部署 PRD

## 1. 模块目标

讲清楚 Agent 到底在哪里运行、用户怎么调用、飞书 ACK 和 LangGraph 执行如何解耦、Codex / 飞书 / Hermes / MCP 各自负责什么。

最终分四层：

```text
开发层：Codex 负责写代码、改 PRD、跑测试，不是生产运行时。
运行层：FastAPI + LangGraph 承载所有 Agent / graph / state / interrupt。
隔离协作层：policy-engine + AgentHarness + Gateway + ArtifactStore + MemoryGateway + ReviewGate 管理权限、工具、协作、记忆和审查。
入口层：飞书机器人、飞书事件回调、飞书交互卡片、本地 API、后续可选 Hermes/MCP；Discord 是 optional adapter。
```

## 2. 角色边界

### 2.1 Codex

Codex 是开发期工具：

```text
写项目代码
改 PRD
写测试
跑本地调试
做代码审查
根据案例数据迭代 prompt、SOP 和规则
```

Codex 不是：

```text
用户每天使用的 Agent 入口
生产环境里被飞书调用的服务
长期在线的消息处理器
数据库或飞书的运行时宿主
```

### 2.2 飞书

飞书是第一版真实用户入口、War Room 工作台和 Bitable 同步入口：

```text
飞书群 @机器人或入口卡片发起任务
ACK/toast 提示已收到
任务确认卡 double check
交互卡片按钮审批
卡片表单编辑结构化任务或审批 payload
群消息、话题串或卡片展示进度、结果和审计摘要
```

飞书不负责：

```text
业务判断
候选人推荐结论自动落库
外部触达自动发送
长期记忆审批
绕过 LangGraph 直接执行副作用
```

普通用户通过飞书群消息、交互卡片、按钮和表单使用，不需要懂 API 或 JSON。

### 2.3 LangGraph

LangGraph 是运行时编排器：

```text
管理三省六部角色审查矩阵
管理业务节点
管理状态流转
管理条件路由
管理 PostgreSQL checkpoint
管理 interrupt / resume
管理 artifact 依赖的等待和恢复
```

所有“Agent 部门”在工程里都是 LangGraph node 或 subgraph，不是用户手动让 Codex 逐个调用。

LangGraph 不直接授予工具权限。所有 Agent 节点调用工具前必须经过 `AgentHarness` 和 `policy-engine`。

### 2.4 FastAPI

FastAPI 是服务外壳：

```text
接收飞书事件和卡片回调
校验飞书签名
3 秒内完成 ACK / toast
写入 feishu_event_logs、feishu_card_actions 和 feishu_outbox
接收本地 API 请求
创建或读取 thread_id
异步调用 LangGraph 根图
处理 interrupt 返回
处理飞书卡片 approve/edit/reject
查询 AgentRuns 和 thread state
```

### 2.5 Hermes / MCP / 飞书

主链路不依赖 Hermes / MCP。Hermes / MCP 可以作为外部入口或工具生态接入层，但不能替代 LangGraph 根图、policy-engine、AgentHarness 或 action-gateway。

Discord 作为后续 optional adapter，不进入第一版验收主路径。Bitable 是人工确认后的业务展示和同步表。

```text
Hermes 不替代 LangGraph。
Hermes 不直接操作数据库。
Hermes 不直接调用飞书 / Discord SDK。
Discord optional adapter 不影响 Feishu First 验收。
```

## 3. 飞书 ACK 和 Graph 的关系

飞书事件和卡片回调要求短时间内响应。系统做法是把 ACK/toast 和 graph 执行拆开：

```text
ACK/toast：只证明服务收到、签名通过、事件已持久化。
Graph：后台 worker 从 durable outbox claim 后异步执行，可持续数秒到数分钟。
War Room：graph 的阶段进度、追问、确认和结果通过 FeishuGateway 异步发送或更新；Bitable 同步通过 FeishuBitableGateway 执行。
```

所以“跑快 ACK 了，graph 怎么办”的答案是：

```text
graph 不在 ACK 请求里跑。
ACK/toast 前只做校验、幂等、落库和入队。
ACK 后由 worker 根据 outbox 恢复或启动 graph。
thread_id、event_id、open_message_id、idempotency_key 负责把飞书回调和 LangGraph checkpoint 对齐。
```

## 4. 最终运行逻辑

```text
用户在飞书群 @机器人、发送任务或点击入口卡片
-> 飞书调用 POST /feishu/events 或 POST /feishu/card-actions
-> FastAPI 校验签名、租户/群聊 allowlist、event/action idempotency
-> 3 秒内 ACK/toast
-> 写 feishu_event_logs / feishu_card_actions + feishu_outbox
-> worker claim outbox
-> TaskIntakeParser 解析飞书消息、附件、卡片表单字段
-> 生成 CanonicalTaskBrief / RequisitionBrief / ResumeProfile / CandidateEvidencePack
-> SchemaValidator 校验结构化任务
-> 飞书发任务确认卡 double check
-> 用户 approve / edit / reject
-> approve 后创建或读取 thread_id
-> 调用 headhunter_war_room_graph
-> policy-engine 创建 TaskPlan / AgentPolicy / token 预算
-> 自动选择 council_mode，并展示 mode_reason
-> 用户明确要求“三省六部”时强制 full_council
-> council_deliberation_graph 基于同一 Canonical Context 进行角色审查
-> AgentHarness 按 council_mode 调度必要 Agent
-> AgentSOPRegistry 检索 1 个主 SOP + 最多 2 个审查 SOP
-> MemoryGateway 基于当前节点任务检索 pgvector 记忆并生成 memory_refs
-> AgentHarness 构造 ContextPack，不注入完整历史或完整 state
-> Agent 通过 Gateway 搜索、查库、读记忆
-> Agent 通过 ArtifactStore 交换结构化产物
-> ReviewGate 审查关键产物
-> 飞书 War Room 展示结构化过程
-> ready_to_execute=false：返回追问卡
-> ready_to_execute=true：条件路由到业务子图
-> 业务子图产出结构化结果
-> 高风险副作用前 interrupt()
-> 飞书发送确认卡或表单
-> 用户 approve/edit/reject
-> 飞书回调到 /feishu/card-actions
-> FastAPI 校验并幂等生成 HumanApproval
-> FastAPI 用 Command(resume=...) 恢复同一个 thread_id
-> ActionGateway 执行被批准的副作用
-> AgentRuns + MemoryProposal 记录审计和可复盘数据
```

每次运行必须向用户展示本次 `council_mode` 和 `mode_reason`。

## 5. LangGraph 实现路径

### 5.1 状态模型

LangGraph state 使用 `TypedDict` + reducer。Pydantic 用于请求、响应、LLM 结构化输出。

```python
import operator
from typing import Annotated, TypedDict

class RecruitmentState(TypedDict, total=False):
    thread_id: str
    channel_context: dict
    canonical_task_brief: dict
    double_check_status: str
    task_type: str
    task_plan: dict
    policy_snapshot: dict
    council_mode: str
    mode_reason: str
    council_decision: dict
    department_opinions: Annotated[list[dict], operator.add]
    artifacts: Annotated[list[dict], operator.add]
    pending_artifact_types: list[str]
    memory_refs: Annotated[list[dict], operator.add]
    sop_refs: Annotated[list[dict], operator.add]
    source_refs: Annotated[list[str], operator.add]
    visibility_mode: str
    human_questions: list[str]
    ready_to_execute: bool
    requisition: dict
    talent_map: list[dict]
    candidate_profile: dict
    candidate_match: dict
    review_result: dict
    human_approval: dict
    channel_write_result: dict
    node_history: Annotated[list[dict], operator.add]
    errors: Annotated[list[dict], operator.add]
```

`RecruitmentState` 只负责编排和恢复，不得直接作为 LLM 输入。所有 Agent LLM 调用必须通过 `AgentHarness.build_context_pack` 得到最小 `ContextPack`。

### 5.2 正式 graph 入口

正式 graph 只接收用户已经 double check 的结构化任务。

```python
builder.add_edge(START, "load_confirmed_canonical_task")
builder.add_edge("load_confirmed_canonical_task", "create_task_plan_and_policy")
builder.add_edge("create_task_plan_and_policy", "council_deliberation_graph")
builder.add_conditional_edges("council_deliberation_graph", dispatch_from_council)
```

统一口径：

```text
根图只编排入口、前置会审子图、业务子图、ReviewGate、interrupt 和终态记录。
会审 Agent 全部在 council_deliberation_graph 内部运行。
三省六部是同一 Canonical Context 下的角色审查矩阵，不是自由 agent-to-agent 聊天。
下游 Agent 只消费固定 schema、ArtifactRef、MemoryRef、SOPRef 和 SourceRef。
```

### 5.3 持久化

每次调用必须带 `thread_id`：

```python
config = {"configurable": {"thread_id": thread_id}}
result = graph.invoke(input_state, config=config)
```

本地和生产都使用：

```text
PostgreSQL checkpointer
PostgreSQL business repository
PostgreSQL + pgvector memory store
```

SQLite 只允许作为个人临时实验，不进入第一版验收。

### 5.4 人工确认

这些动作仍必须 `interrupt()`：

```text
业务数据写入
报告发布
飞书或后续渠道中的外部触达发送
候选人推荐结论保存
长期记忆进入 active
高风险合规判断通过
```

这些动作可以自动写入或自动发送：

```text
内部审计
checkpoint
AgentRuns
RunMemory
任务授权后的 飞书 War Room 进度卡
任务授权后的追问卡
任务授权后的确认卡
任务授权后的结果卡
```

示例：

```python
approval = interrupt({
    "action_id": "act_...",
    "interrupt_id": "int_...",
    "idempotency_key": "thread_action_version",
    "action": "write_talent_map",
    "thread_id": state["thread_id"],
    "summary": "即将写入 TalentMap",
    "preview": state["talent_map"],
    "options": ["approve", "edit", "reject"],
})
```

飞书卡片回调后恢复：

```python
graph.invoke(
    Command(resume=human_approval),
    config={"configurable": {"thread_id": thread_id}},
)
```

## 6. API / 飞书调用路径

### 6.1 飞书事件入口

```text
POST /feishu/events
POST /feishu/card-actions
```

流程：

```text
读取 raw body
校验飞书事件签名、卡片签名、timestamp、nonce
处理飞书 URL verification / challenge
解析 event_id、tenant_key、open_chat_id、open_id、message_id、action value
校验 tenant/chat allowlist
写入 FeishuEventLog / FeishuCardAction 幂等记录和持久 outbox，并提交 PostgreSQL 事务
重复 event_id / action_id / idempotency_key 只 ACK，不再次派发 graph 或 resume
3 秒内返回 ACK/toast
后台 worker 原子 claim outbox
按 interaction 类型路由到 intake、double check、approval 或 status 查询
异步发送或更新飞书 War Room 卡片
```

### 6.2 Double Check 入口

```text
飞书卡片 action value:
task_check:approve:{task_id}
task_check:edit:{task_id}
task_check:reject:{task_id}
```

流程：

```text
approve：标记 TaskDoubleCheckState approved，启动 graph。
edit：打开飞书卡片表单，用户修改字段后重新解析并再次发确认卡。
reject：停止任务，记录原因，可生成 UserCorrectionMemoryProposal。
```

### 6.3 业务审批入口

```text
飞书卡片 action value:
approval:{decision}:{thread_id}:{action_id}:{interrupt_id}:{idempotency_key}
```

流程：

```text
校验飞书签名
解析 action_id、interrupt_id、idempotency_key、decision、edited_payload
同一 idempotency_key 通过 PostgreSQL 唯一约束和原子 insert / claim 只允许生成一次 HumanApproval
3 秒内 ACK/toast，可提示“已收到，正在处理”
后台调用 Command(resume=HumanApproval)
恢复同一个 thread_id
执行或拒绝副作用
异步更新飞书消息/卡片和 AgentRuns
```

### 6.4 内部人工确认入口

```text
POST /human-approval/{thread_id}
```

规则：

```text
仅供内部调试、后台工具或非飞书入口调用。
不能绕过 idempotency_key。
不能绕过 interrupt。
飞书卡片不得直接调用此内部入口。
```

### 6.5 本地调试入口

```text
POST /council/deliberate
POST /requisitions/calibrate
POST /requisitions/{requisition_id}/talent-map
POST /candidates/{candidate_id}/screen
```

规则：

```text
业务 API 可以保留，方便本地测试和演示。
但内部必须先生成或接收 CanonicalTaskBrief、CouncilDecision 和 ReviewGate 结果。
不能绕过 double check、会审层或人工确认边界。
```

## 7. Hermes / MCP 预留

可新增：

```text
app/adapters/hermes_adapter.py
app/mcp/server.py
```

Hermes / MCP 只能调用公开 API：

```text
POST /council/deliberate
POST /requisitions/calibrate
POST /candidates/{candidate_id}/screen
GET /runs/{run_id}
```

禁止：

```text
Hermes 直接写数据库
Hermes 直接调用飞书 / Discord SDK
Hermes 绕过 CanonicalTaskBrief
Hermes 绕过 CouncilDecision
Hermes 绕过 ReviewGate
Hermes 绕过 interrupt
Hermes 绕过 policy-engine
Hermes 直接调用 agent-* 容器
```

## 8. 容器化隔离部署

目标服务：

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

Agent 容器安全基线：

```yaml
read_only: true
cap_drop: ["ALL"]
security_opt:
  - no-new-privileges:true
user: "10001:10001"
tmpfs:
  - /tmp
```

网络规则：

```text
agent-* -> 只能访问 policy-engine、agent-harness、artifact-store、允许的 gateway
model-gateway -> 模型 API
search-gateway -> 公网搜索
database-gateway -> 数据库
memory-gateway -> postgres + pgvector
feishu-gateway -> 飞书 API
action-gateway -> channel-gateway / database-gateway
orchestrator-api -> LangGraph checkpointer / policy-engine / agent-harness
```

## 9. 部署路径

### 9.1 本地开发

```text
uvicorn app.main:app --reload
PostgreSQL
PostgreSQL checkpointer
pgvector memory store
真实飞书 test application
local tunnel 指向 /feishu/events 和 /feishu/card-actions
local SOP registry
单进程模拟 policy-engine / AgentHarness / ArtifactStore
```

### 9.2 飞书联调

```text
FastAPI 暴露公网回调地址
配置飞书事件订阅地址：/feishu/events
配置飞书卡片回调地址：/feishu/card-actions
配置飞书 app_id、app_secret、verification token、encrypt key
配置机器人能力、事件权限和卡片回调
邀请机器人进测试群并配置可用范围
配置 tenant/chat allowlist
使用真实 FeishuGateway 和 FeishuBitableGateway
开启 War Room 群消息和交互卡片
```

当前实现状态：

```text
/feishu/events 与 /feishu/card-actions:
  已具备 verifier、challenge、payload 入库、FeishuEventLog、FeishuCardAction、HumanApproval、feishu_outbox 和 resume 基础。
  真实飞书后台、Bitable 权限、真实卡片发送和 graph end-to-end 仍未验证。
```

真实飞书联调未完成前，状态必须写“未验证”。

### 9.3 小团队试用

```text
PostgreSQL
PostgreSQL checkpointer
pgvector memory store
真实飞书租户 / 测试群
AgentRuns 审计开启
日志脱敏开启
policy-engine / AgentHarness 独立服务化
ArtifactStore / MemoryGateway 开启审批流
核心 agent-* 容器化
```

### 9.4 Discord optional adapter

```text
Discord 可作为后续 optional adapter。
不得作为第一版验收 blocker。
不得在 Feishu First 主链路中直接写死 DiscordGateway。
```

### 9.5 目标生产部署

```text
所有 agent-* 容器化隔离
Gateway 分服务部署
Rootless Docker 或等价非特权运行环境
Secrets 按服务最小授权
网络分区启用
审计和告警启用
memory retention job 启用
```

## 10. 验收标准

```text
用户能从飞书群消息或交互卡片触发任务解析。
/feishu/events 与 /feishu/card-actions 能在 3 秒内 ACK/toast，完整 graph 执行在后台异步完成。
用户必须 double check 结构化 CanonicalTaskBrief 后才启动正式 graph。
本地 API 和飞书入口调用同一套 headhunter_war_room_graph。
每次运行都展示 council_mode 和 mode_reason。
用户要求“三省六部”时必须使用 full_council。
第一版主链路使用 PostgreSQL、PostgreSQL checkpointer、pgvector 和真实飞书事件回调和交互卡片。
相同 thread_id 能恢复状态。
interrupt() 能暂停并通过飞书卡片 resume。
Hermes/MCP 只能通过公开 API 调用，不能绕过根图、policy-engine、ReviewGate 或 action-gateway。
Codex 的角色在文档中明确为开发期工具。
Agent 上网、查库、读记忆和用 SOP 都必须经过授权 Gateway / Registry。
Agent 之间只能通过 ArtifactStore 交换结构化产物。
飞书 War Room 能展示结构化过程、证据、工具轨迹、memory_refs、sop_refs、token 消耗和用户修改。
飞书事件和卡片具备 event_id、action_id、idempotency_key 幂等保护。
AgentHarness 只向 Agent 注入 ContextPack，不注入完整历史、完整 state、全量 AgentRuns、全量 artifacts 或全量长期记忆。
ReviewGate 具备 pass / needs_fix / needs_human 三态，自动修复最多一次。
MemoryGateway 具备 30d / 90d / permanent retention policy。
真实飞书、Postgres checkpointer、OpenAI、外部触达未联调前必须写“未验证”。
```
