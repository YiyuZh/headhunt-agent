# 模块 11：Agent 隔离与容器化架构 PRD

## 1. 模块目标

本模块定义目标系统的硬隔离架构。系统允许不同 Agent 协作，但不允许主 Agent 越级替子 Agent 做业务，也不允许子 Agent 越权调用其他 Agent、数据库、Discord、飞书、模型 Key 或公网。

核心原则：

```text
Agent 可以协作，但不能乱连。
Agent 可以使用工具，但必须经授权 Gateway。
Agent 可以读上游结果，但只能读 ArtifactStore 中允许读取的 artifact。
Agent 可以提出副作用动作，但只能由 action-gateway 在人工确认后执行。
```

## 2. 服务拓扑

目标系统采用容器化服务边界：

```text
orchestrator-api
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
feishu-gateway

postgres
redis
```

职责边界：

| 服务 | 职责 | 禁止 |
|---|---|---|
| `orchestrator-api` | FastAPI 入口、thread_id、调用 LangGraph 根图 | 不直接写外部工作台、不跳过 policy |
| `agent-*` | 单一 Agent 职责内的结构化判断 | 不互相调用、不直接访问外部资源 |
| `policy-engine` | 生成和校验 AgentPolicy、TaskPlan、预算 | 不生成业务结论 |
| `agent-harness` | 调度 Agent、注入 skill、记录 token/tool 使用 | 不执行飞书、Bitable 或 Discord optional adapter 副作用 |
| `artifact-store` | 保存 AgentArtifact、版本、证据引用 | 不做模型推理 |
| `model-gateway` | 统一模型调用和结构化输出 | 不接触飞书、Discord optional adapter 和业务数据库写入 |
| `search-gateway` | 受控公网搜索和来源记录 | 不抓取未经授权的个人数据 |
| `database-gateway` | 受控数据库查询、字段脱敏、审计 | 不开放原始连接给 Agent |
| `memory-gateway` | pgvector 记忆检索、写入提案、审批更新、裁剪和检索审计 | 不允许静默写长期记忆、不返回越权全文 |
| `action-gateway` | 人工确认后的业务写入、外部发送、建文档、建任务 | 不接受未 interrupt 的业务副作用动作 |
| `feishu-gateway` | FeishuGateway 主实现，统一事件、卡片、群消息、outbox、限频和权限 | 不承载 Agent 决策逻辑，不绕过 outbox |
| `bitable-gateway` | FeishuBitableGateway，人工确认后的多维表格分片写入和 record_id 映射 | 不作为主库，不接受未审批写入 |
| `channel-gateway` | 跨渠道抽象，后续可挂 Discord optional adapter | 不承载 Agent 决策逻辑 |

## 3. 网络分区

```text
agent-* -> policy-engine, agent-harness, artifact-store, allowed gateway
model-gateway -> 模型 API
search-gateway -> 公网搜索
database-gateway -> postgres
memory-gateway -> postgres / pgvector
feishu-gateway -> 飞书 API
bitable-gateway -> 飞书 Bitable API
channel-gateway -> feishu-gateway / optional Discord adapter
action-gateway -> feishu-gateway / bitable-gateway / database-gateway
orchestrator-api -> LangGraph checkpointer / policy-engine / agent-harness
```

硬禁止：

```text
agent-* 直接访问数据库
agent-* 直接访问 Discord 或飞书
agent-* 直接访问模型 API
agent-* 直接访问公网
agent-* 调用其他 agent-* endpoint
agent-* 持有 OPENAI_API_KEY、FEISHU_APP_SECRET、DATABASE_URL 写权限
```

## 4. 容器安全基线

所有 `agent-*` 容器默认配置：

```yaml
read_only: true
cap_drop: ["ALL"]
security_opt:
  - no-new-privileges:true
user: "10001:10001"
tmpfs:
  - /tmp
```

Secrets 规则：

```text
模型 Key 只给 model-gateway。
飞书 app_secret、verification token、encrypt key 只给 feishu-gateway；Discord bot token/public key 只给 optional adapter。
数据库写凭证只给 database-gateway / action-gateway。
Agent 容器只拿短时任务 token，不拿长期 secret。
Agent 容器不能拿 pgvector 直连凭证，记忆检索必须经 memory-gateway。
```

## 5. AgentGateway 与 PolicyEngine

所有 Agent 运行前必须拿到 `AgentPolicy`。`AgentGateway` 负责把调用转成受控请求：

```python
class AgentPolicy(BaseModel):
    agent_name: str
    role: str
    allowed_operations: list[str]
    allowed_tools: list[str]
    allowed_skills: list[str]
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
```

默认规则：

```text
can_call_agents = false
can_execute_side_effects = false
```

任何例外都必须写入 `policy_snapshot` 和 `AgentRuns`。

## 6. 典型 Agent 权限

`SourcingMappingAgent`：

```text
可 web search：GitHub、公开公司官网、搜索引擎。
可读：Requisition、SkillTaxonomy、StrategyDraftArtifact。
可写：TalentMapDraft、SearchQueryDraft。
不可写 Discord 或飞书。
不可读候选人联系方式。
不可调用 CandidateJudgementAgent。
```

`CandidateJudgementAgent`：

```text
可读：Requisition、Candidate、TalentMapDraft。
可写：CandidateMatchDraft。
默认不可 web search，除非任务明确要求并经 policy 允许。
不可写 Discord 或飞书。
不可生成触达话术。
```

`ComplianceRiskAgent`：

```text
可读所有 draft artifact 的脱敏版本。
可写：ComplianceReview。
不可调用外部搜索。
不可写业务数据。
```

`DataAutomationAgent`：

```text
可读：表结构、导入批次、同步状态、脱敏错误日志。
可写：DataAutomationPlan、ImportValidationReport。
不可读取候选人联系方式明文。
不可直接执行迁移或生产写入。
```

## 7. Side Effect 防线

必须人工确认的业务副作用统一走：

```text
Agent 输出 ActionProposal artifact
-> Review Node
-> interrupt()
-> 飞书确认卡片或表单 approve/edit/reject
-> action-gateway
-> feishu-gateway / database-gateway
-> AgentRuns 审计
```

允许自动执行的运行时写入：

```text
Postgres checkpoint
AgentRuns
RunMemory
ArtifactStore
Feishu event/card action logs、Discord optional adapter 幂等记录
RunMemory embedding / memory_retrieval_audit
任务授权后的 War Room 进度卡 / 追问卡 / 确认卡 / 结果卡
```

`feishu-gateway` 自动发送 War Room 卡片前必须完成机器人入群/可用范围/权限预检查、卡片 JSON 校验和 per channel / per user 限频。遇到 429 或权限错误时只记录审计并返回可读错误，不得让 Agent 绕过 gateway 重试。Discord optional adapter 后续接入时遵循同样规则。

禁止：

```text
Agent 直接执行外部触达发送
Agent 直接 create_task
Agent 直接 write_talent_map
Agent 直接 update_candidate
Agent 直接 publish_report
```

## 8. 容器化验收标准

- 每个 Agent 容器只能访问 allowlist 网络目标。
- 每个 Agent 容器无模型 Key、无 飞书/Discord optional adapter Key、无数据库写凭证。
- 任何越权调用都会被 `policy-engine` 拒绝并写入审计。
- 任何业务副作用请求必须有 `HumanApproval` 和 `interrupt_id`。
- 任务授权后的 War Room 进度卡、追问卡、确认卡和结果卡可自动发送，并必须写入 AgentRuns。
- Agent 容器不能直接访问 pgvector、EmbeddingGateway 或完整 memory content。
- MemoryGateway 只能返回 policy 允许的 MemoryRef，并记录检索审计。
- `agent-*` 容器以非 root 用户运行，默认只读文件系统。
- 本地开发可用单进程模拟，但接口边界必须按容器化目标设计。
