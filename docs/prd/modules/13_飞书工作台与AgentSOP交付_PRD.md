# 模块 13：飞书工作台与 AgentSOP 交付 PRD

## 1. 模块目标

本模块把第一版交付方式固定为 Feishu First，并把外部 Agent SOP、BMAD、Claude Code subagents、Cursor Rules、LangGraph multi-agent / memory 的可复用方法沉淀为本项目自己的 SOPRegistry、ReviewGate 和结构化上下文协议。

底层原则：

```text
统一事实源，而不是多 Agent 自由复述。
结构化交接，而不是 prompt 接力。
用户 double check，而不是系统自信误解。
检索式记忆，而不是全量历史注入。
artifact-level ReviewGate，而不是全局聊天式审查。
飞书卡片审批，而不是绕过人工确认的自动副作用。
```

## 2. 外部方法吸收边界

本项目只吸收公开方法学与工程组织方式，不复制外部项目的大段文本、不把它们作为第一版运行时依赖。

| 来源 | 吸收内容 | 落地方式 |
| --- | --- | --- |
| Agent SOP | Markdown SOP、参数化输入、MUST/SHOULD/MAY 约束、步骤化执行、`.agents/summary|planning|tasks|scratchpad` 分层 | `AgentSOPRegistry` + `ContextPack.sop_refs` |
| BMAD Method | Analysis -> Planning -> Solutioning -> Implementation、agent/skill/trigger/workflow 映射、PRD validation checklist、story template | 项目 SOP 生命周期、ReviewGate rubric、任务拆解模板 |
| Claude Code subagents | task-specific workflows、improved context management、frontmatter/description/tool scope | Agent / Reviewer allowlist、工具权限、上下文预算 |
| Cursor Rules | Always / Auto Attached / Agent Requested / Manual 触发模型 | `SOPTriggerPolicy` |
| LangGraph | 明确职责、受控 handoff、short-term / long-term memory、conditional edge | graph 节点矩阵、非自由 agent-to-agent、ReviewGate 路由 |
| 飞书开放平台 | 事件回调、卡片回传、challenge、快 ACK、交互卡片、Bitable 同步 | `/feishu/events`、`/feishu/card-actions`、Feishu outbox、War Room 群卡片 |

现有代码已经具备 Feishu verifier、FeishuGateway、BitableGateway、feishu_outbox、War Room card builder 和审批 resume 基础；真实飞书后台、真实 Bitable 权限、真实卡片发送和真实 graph 端到端联调仍标注“未验证”。

## 3. SOPRegistry

SOP 是版本化 Markdown/JSON 文件，包含：

```text
sop_id
version
name
purpose
owner
trigger_policy
input_schema
allowed_artifact_types
allowed_memory_scopes
steps
constraints
output_schema
review_rules
token_budget
source_refs
status
content_ref
hit_reason
token_estimate
```

`trigger_policy` 取值：

```text
always
auto_attached
agent_requested
manual
```

注入规则：

- SOP 只能以 `sop_refs` 进入 ContextPack。
- 每个业务节点最多 1 个主 SOP + 2 个审查 SOP。
- SOP 全文默认不注入模型；需要全文时只能由 AgentHarness 按 token budget 读取 `content_ref` 后摘要压缩。
- SOP 变更必须 version bump，并写入 AgentRuns。

规划目录：

```text
docs/agent-sops/
├── registry.json
├── business/*.sop.md
├── reviewers/*.sop.md
├── workflows/*.workflow.md
├── checklists/*.checklist.md
└── templates/*.template.md
```

`AgentSOPRegistry.resolve(agent_name, operation, task_type, policy)` 只返回 `SOPRef`，不得把全量 SOP 仓库塞入 prompt。

## 4. 结构化任务与飞书 Double Check

新增主解析器：

```text
TaskIntakeParser
```

输出固定结构：

```text
CanonicalTaskBrief
RequisitionBrief
ResumeProfile
CandidateEvidencePack
OutreachDraftInput
ReportDraftInput
```

字段来源规则：

- `TaskIntakeParser` 输出的每个关键字段必须有 `field_source` 和 `confidence`。
- `field_source` 必须指向飞书消息、飞书卡片字段、附件引用、source_ref、artifact_ref 或用户编辑记录。
- 无 source 的推断只能进入 `assumptions`，不能进入事实字段。
- 低置信字段进入 `missing_fields`、`questions` 或 `assumptions`，由飞书 double check 卡展示给用户确认。

Double Check 状态机：

```text
parsed
-> feishu_confirmation_card_sent
-> approved | edited | rejected | expired
approved -> freeze CanonicalTaskBrief version -> graph_dispatch_queued
edited -> reparsed -> feishu_confirmation_card_sent
rejected -> stopped
expired -> stopped
```

硬门：

- approve 后冻结 `CanonicalTaskBrief.version`，正式 graph 只能读取冻结版。
- edit 必须生成新版本并重新发送飞书确认卡。
- reject / expired 不得进入正式 graph。
- 下游 Agent 只能读取冻结版 `CanonicalTaskBrief`、ArtifactRef、MemoryRef、SOPRef、SourceRef。

## 5. 三省六部统一上下文审查矩阵

三省六部不是多个 Agent 自由对话，而是同一 `Canonical Context` 下的角色审查矩阵：

```text
CanonicalTaskBrief
-> RoleReviewMatrix
   -> CandidateJudgementReviewer
   -> MarketSupplyReviewer
   -> OutreachValueReviewer
   -> SourcingMappingReviewer
   -> ComplianceRiskReviewer
   -> DataAutomationReviewer
   -> ChallengeReviewer
-> CouncilSynthesizer
```

每个角色只看冻结版 `CanonicalTaskBrief` 的 allowlist 字段、必要 artifact_refs、memory_refs 和 sop_refs。角色不得重新解析原始简历或原始任务；`CouncilSynthesizer` 只看各角色结论摘要和 ReviewFinding。

`full_council` 只在高风险、高价值、用户明确要求“三省六部”或 ReviewGate 需要升级时启用。所有飞书 War Room 卡片必须展示本次实际 `council_mode` 和 `mode_reason`。

## 6. ReviewGate

ReviewGate 是 artifact-level quality gate。每个关键 artifact 后必须通过 conditional edge 路由：

```text
pass
needs_fix
needs_human
```

Reviewer：

```text
SchemaValidator：字段、类型、枚举、必填和 JSON schema。
EvidenceConsistencyReviewer：结论是否有 evidence_refs / source_refs 支撑。
PracticalityReviewer：话术和报告是否可执行，按个性化、价值主张、行动请求、猎头语气、合规风险、简洁度评分。
ContextBudgetReviewer：检查是否传入全历史、全 state、过量 memory 或 SOP 全文。
SafetyReviewer：检查隐私、外部触达、推荐结论和 Bitable 写入是否越过人工确认。
```

失败处理：

- `pass`：进入下一个业务节点。
- `needs_fix`：只回到对应 `repair_node` 一次，不允许重跑完整 graph。
- 第二次仍 `needs_fix`：必须 `interrupt_human_approval`，通过飞书卡片请求人工处理。
- `needs_human`：直接进入 HumanApproval，不自动修复。

## 7. 长期记忆治理

PostgreSQL + pgvector 仍是第一版主路径。记忆类型：

```text
short-term：LangGraph checkpointer thread state。
semantic：项目事实、客户偏好、行业词库。
episodic：历史案例、成功/失败任务片段。
procedural：SOP、提示词改进、审查规则。
user correction：用户在飞书卡片 edit/reject 中修正过的内容。
```

retention policy：

```text
RunMemory：默认 30 天。
长期业务记忆：默认 90 天。
Procedural / SOP：可 permanent。
```

长期记忆必须审批后才 `active`；过期、撤销或超 scope 的记忆不可被检索。`MemoryGateway` 必须按 tenant_id / open_id / chat_id / project_id / requisition_id / candidate_id 做 scope filter。

公司背调的当前事实必须来自 SearchGateway `source_refs`；长期记忆只能提供 taxonomy、项目规则、用户修正和 SOP，不能替代实时搜索。

## 8. 飞书用户可用路径

第一版普通用户路径：

```text
管理员创建飞书应用、机器人、事件订阅、卡片回调和 Bitable 表
-> 邀请机器人进测试群
-> 用户 @机器人发起任务或点击任务入口卡
-> 系统 ACK 并发送结构化任务确认卡
-> 用户 approve / edit / reject
-> graph 异步执行
-> 飞书 War Room 卡片展示进度、ReviewGate、证据和待办
-> 高风险动作通过飞书卡片请求人工确认
-> 批准后同步 Bitable 展示表
```

用户不需要懂 API 或 JSON。API 保留给内部调试和自动化，不作为第一使用入口。

## 9. Discord Optional Adapter

Discord 相关代码和手册降级为 optional adapter：

- 可以保留 `/discord/interactions`、`/model` 和 command register 的历史实现。
- 不作为第一版验收、普通用户入口或主手册。
- 后续如重新启用 Discord，必须复用 Feishu First 已确定的结构化任务、ReviewGate、MemoryGateway、HumanApproval 和 Gateway 边界。

## 10. 验收标准

- PRD 明确 Feishu First，Discord optional。
- 飞书 double check 是正式 graph 前硬 gate。
- 飞书卡片 approve/edit/reject 能映射为 double check、HumanApproval 或 graph resume。
- ReviewGate 只审 artifact，并按 `pass / needs_fix / needs_human` conditional edge 路由。
- MemoryGateway 有 scope filter、retention policy 和审批 active 规则。
- BYOK 模型配置入口是飞书卡片；API Key 加密保存且不展示。
- Bitable 只作为人工确认后的业务展示/同步表，PostgreSQL/pgvector 仍是主数据和记忆底座。
- 未真实联调事项必须写“未验证”。
