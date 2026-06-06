# 模块 13：Discord 工作台与 AgentSOP 交付 PRD

## 1. 模块目标

本模块把第一版交付方式固定为 Discord First，并把外部 Agent SOP、BMAD、Claude Code subagents、Cursor Rules、LangGraph multi-agent / memory 的可复用方法沉淀为本项目自己的 SOPRegistry、ReviewGate 和结构化上下文协议。

核心原则：

```text
统一事实源，而不是多 Agent 自由复述。
结构化交接，而不是 prompt 接力。
用户 double check，而不是系统自信误解。
检索式记忆，而不是全量历史注入。
角色审查矩阵，而不是 agent-to-agent 闲聊。
```

## 2. 外部方法吸收边界

本项目只吸收公开方法学与工程组织方式，不复制外部项目的大段文本、不引入它们作为第一版运行时依赖。

| 来源 | 吸收内容 | 落地方式 |
| --- | --- | --- |
| Agent SOP | Markdown SOP、参数化输入、MUST/SHOULD/MAY 约束、步骤化执行、`.agents/summary|planning|tasks|scratchpad` 分层 | `AgentSOPRegistry` + `ContextPack.sop_refs` |
| BMAD Method | Analysis -> Planning -> Solutioning -> Implementation、agent/skill/trigger/workflow 映射、PRD validation checklist、story template | 项目 SOP 生命周期、ReviewGate rubric、任务拆解模板 |
| Claude Code subagents | task-specific workflows、improved context management、frontmatter/description/tool scope | Agent / Reviewer allowlist、工具权限、上下文预算 |
| Cursor Rules | Always / Auto Attached / Agent Requested / Manual 触发模型 | `SOPTriggerPolicy` |
| LangGraph | subagents / handoffs 强调明确职责和上下文控制，memory 区分短期和长期 | graph 节点矩阵、非自由 handoff、short-term / long-term memory |
| Discord 官方文档 | interaction 签名校验、快速 ACK、follow-up、button/select/modal | `/discord/interactions`、durable outbox、War Room thread |

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

触发优先级：

```text
always
-> auto_attached
-> agent_requested
-> manual
```

冲突规则：

- 同一 `sop_id` 只允许一个 active 版本命中；新版本发布后旧版本进入 `revoked` 或 `superseded`。
- `manual` SOP 只能由用户、管理员或人工审批显式指定，Agent 不得自行提升为 manual。
- `agent_requested` 必须经过 `AgentPolicy.allowed_sop_scopes` 和 token budget 校验。
- `auto_attached` 必须由 `task_type`、`artifact_type`、`agent_name` 或 `risk_level` 命中，不能因为全文相似就自动注入。

注入规则：

- SOP 只能以 `sop_refs` 进入 ContextPack。
- 每个业务节点最多 1 个主 SOP + 2 个审查 SOP。
- SOP 全文默认不注入模型；需要时也只能由 AgentHarness 读取 `content_ref` 后按 token budget 摘要压缩，不得整包注入所有 SOP。
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

`AgentSOPRegistry.resolve(agent_name, operation, task_type, policy)` 只返回 `SOPRef`：

```text
sop_id
version
title
scope
trigger_policy
content_ref
summary
hit_reason
token_estimate
source_refs
```

`SOPResolutionAudit` 必须记录：

```text
thread_id
run_id
agent_name
operation
task_type
selected_sop_refs
excluded_sop_refs
excluded_reason
policy_version
created_at
```

## 4. 结构化任务与 Double Check

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
- `field_source` 必须指向 slash command option、modal field、attachment ref、source_ref、artifact_ref 或用户编辑记录。
- 无 source 的推断只能进入 `assumptions`，不能进入事实字段。
- 低置信字段进入 `missing_fields`、`questions` 或 `assumptions`，由 double check 卡展示给用户确认。

`CanonicalTaskBrief` 必须包含：

```text
task_brief_id
version
task_type
facts
assumptions
missing_fields
field_sources
field_confidence
double_check_status
confirmed_by
confirmed_at
edit_history_ref
frozen_at
```

下游 Agent 默认不得重新解析原始任务、原始简历或完整聊天历史。只有 `SchemaConflictReviewer` 判断结构化输入不足时，才允许触发补充解析或向用户追问。

Double Check 状态机：

```text
parsed
-> confirmation_card_sent
-> approved | edited | rejected | expired
approved -> freeze CanonicalTaskBrief version -> graph_dispatch_queued
edited -> reparsed -> confirmation_card_sent
rejected -> stopped
expired -> stopped
```

Double Check 硬门：

- approve 后冻结 `CanonicalTaskBrief.version`，正式 graph 只能读取冻结版。
- edit 必须生成新的 version，并重新发送确认卡；不得在已冻结版本上原地修改。
- reject / expired 不得进入正式 graph。
- 下游 Agent 只能读取冻结版 `CanonicalTaskBrief`、ArtifactRef、MemoryRef、SOPRef、SourceRef。

## 5. 三省六部统一上下文审查矩阵

三省六部不再理解为多个 Agent 自由对话，而是同一 `Canonical Context` 下的角色审查矩阵。

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

每个角色只看冻结版 `CanonicalTaskBrief` 的 allowlist 字段、必要 artifact_refs、memory_refs 和 sop_refs。角色不得重新解析原始简历或原始任务；`CouncilSynthesizer` 只看各角色结论摘要和 ReviewFinding，不看各 Agent 原始 ContextPack。

`full_council` 触发条件：

- 用户明确要求“三省六部”。
- 高风险候选人推荐结论。
- 高价值客户报告发布。
- 跨渠道外部触达。
- ReviewGate 判定标准模式不足。

默认模式仍由 `PolicyEngine / AgentHarness` 自动选择 `triage | lite | standard | full_council`。

## 6. ReviewGate

ReviewGate 是 artifact-level quality gate，不是全局聊天式审查者。每个关键 artifact 产出后，只审该 artifact、它声明的 schema、证据 refs、必要 source_refs 和该节点允许的审查 SOP。

每个关键 artifact 后必须通过 LangGraph conditional edge 路由：

```text
artifact_node
-> review_gate_for_artifact
-> pass: next_node
-> needs_fix: repair_node_for_this_artifact
-> needs_human: interrupt_human_approval
repair_node_for_this_artifact
-> review_gate_for_artifact
-> pass: next_node
-> needs_fix: interrupt_human_approval
-> needs_human: interrupt_human_approval
```

输出：

```text
ReviewResult
review_id
thread_id
artifact_id
artifact_type
reviewer_results
status: pass | needs_fix | needs_human
scores
thresholds
failed_fields
fix_suggestions
evidence_refs
source_refs
repair_node
retry_count
created_at
```

Reviewer：

- `SchemaValidator`：确定性 JSON schema、枚举、必填、类型校验。
- `EvidenceConsistencyReviewer`：结论必须有 evidence_refs / source_refs。
- `PracticalityReviewer`：话术/报告按个性化、价值主张、行动请求清晰度、猎头语气、合规风险、简洁度评分。
- `ContextBudgetReviewer`：检查是否传入完整历史、完整 state、过量 memory、过量 SOP。
- `SafetyReviewer`：检查隐私、外部触达、推荐结论和业务副作用是否越过人工确认。

评分阈值：

```text
SchemaValidator: pass / fail；fail -> needs_fix 或 needs_human
EvidenceConsistencyReviewer: 0-5；低于 4 -> needs_fix，高风险且无证据 -> needs_human
PracticalityReviewer: 0-5；低于 3.5 -> needs_fix，外部触达高风险 -> needs_human
ContextBudgetReviewer: pass / fail；发现全历史、全 state、全 AgentRuns -> needs_fix
SafetyReviewer: pass / needs_human；越过人工确认边界 -> needs_human
```

失败处理：

```text
pass -> 进入下一节点
needs_fix && retry_count=0 -> 只回到对应 repair_node 自动修一次
needs_fix && retry_count>=1 -> interrupt 人工确认
needs_human -> interrupt 人工确认
```

禁止行为：

- `needs_fix` 不得重跑完整 graph。
- `needs_fix` 不得重新运行无关上游节点。
- 第二次仍 `needs_fix` 必须进入 `interrupt_human_approval`。
- ReviewGate 不得读取完整聊天历史、其他 Agent 原始 ContextPack 或原始 prompt。

## 7. 长期记忆与时间治理

记忆分层：

```text
short-term: LangGraph checkpointer thread state
semantic: 项目事实、客户偏好、行业词库
episodic: 历史案例、成功/失败任务片段
procedural: SOP、提示词改进、审查规则
user_correction: 用户在 Discord modal/edit 中修正过的内容
```

保留周期：

```text
RunMemory: 默认 30 天，过期自动 expired
UserCorrectionMemory: 默认 90 天，命中频繁或人工确认后可延长
CaseMemory: 默认 90 天，可升级 permanent
ProjectMemory / AgentMemory: 默认 90 天，需定期复审
ProceduralMemory / SOP: permanent，但每次变更必须 version bump
```

检索规则：

- 只通过 `MemoryGateway.retrieve` 返回 `MemoryRef`。
- 检索前必须按 `tenant_id / guild_id / user_id / project_id / requisition_id / candidate_id` 做 scope filter。
- 长期记忆必须审批后 `status=active` 才可检索。
- `revoked`、`expired` 不得进入 active memory pool。
- 超过 retention 的 active memory 必须降权、过期或进入续期审批。
- War Room 展示 memory_refs、命中原因、token 估算，不展示隐私全文。
- 公司背调、当前在职状态、融资、新闻、裁员、组织变化等当前事实必须来自 SearchGateway 返回的 `source_refs`，长期记忆不得替代实时搜索。
- 简历关键词抽取以当前简历/JD artifact 为主；长期记忆只提供 taxonomy、项目规则、用户修正和 SOP，不得覆盖当前简历/JD 的事实。
- Discord edit、reject reason、人工改稿可以生成 `UserCorrectionMemoryProposal`，审批后才可进入 active 长期记忆。

## 8. 普通用户直接使用路径

```text
管理员邀请 Discord bot
-> 配置 allowed guild / channel
-> 用户使用 slash command
-> Bot ephemeral defer
-> 结构化任务确认卡
-> 用户 approve / edit / reject
-> graph_dispatch
-> War Room thread
-> ReviewGate + AgentRuns
-> 人工确认业务副作用
-> 结果卡 / 状态查询
```

用户不需要手写 JSON，不需要调用内部 API，不需要理解 LangGraph。

## 9. 验收标准

- PRD 明确 Discord First，飞书/Bitable deferred。
- 每个任务正式运行前必须有 double check。
- Agent 不自由 agent-to-agent。
- 下游 Agent 不重新理解原始任务/简历。
- SOP 只以 refs 和少量摘要注入。
- ReviewGate 是 artifact-level quality gate，有 pass / needs_fix / needs_human 三态，且通过 conditional edge 路由。
- `needs_fix` 只回对应 repair_node 一次；第二次失败必须人工确认。
- TaskIntakeParser 每个关键字段都有 field_source 和 confidence；无 source 推断只能进入 assumptions。
- Double Check approve 后冻结 CanonicalTaskBrief version。
- 记忆有 30 天、90 天、permanent 的 retention 口径。
- MemoryGateway 有 tenant/guild/user/project/requisition/candidate scope filter。
- 当前公司事实依赖 SearchGateway source_refs；长期记忆不得替代实时搜索。
- War Room 展示模式、refs、token 和审查结果，不展示原始 prompt。
