# 模块 09：测试、验收与模拟执行 PRD

## 1. 模块目标

在真正执行前，通过审查 Agent、模拟执行、红队用例和测试计划，提前发现漏洞。

## 2. PRD 复查 Agent

```text
TopRecruiterReviewer
ProductPRDReviewer
ArchitectureReviewer
DataModelReviewer
MappingQualityReviewer
SecurityComplianceReviewer
CodeReviewReviewer
ChannelOpsReviewer
DevOpsIsolationReviewer
TestHarnessAgent
MemorySafetyReviewer
```

输出格式：

```json
{
  "agent": "ArchitectureReviewer",
  "verdict": "pass | pass_with_risks | fail",
  "blockers": [],
  "risks": [],
  "required_changes": [],
  "recommended_changes": [],
  "acceptance_checks": []
}
```

## 3. 模拟案例

### A：从 JD 到 TalentMap

输入：

```text
客户需要招聘 AI Infra Engineer，方向是企业知识库、RAG、向量数据库、LLM 应用工程化。
```

通过条件：

- 生成至少 10 个目标公司假设，并说明理由。
- 生成中英文 title 变体。
- 生成技能归一结果。
- 所有写入 TalentMap 前触发 interrupt。

### B：候选人筛选

输入：

```text
CAND-001：某云厂商后端工程师，做过企业知识库、向量检索、Embedding 服务，简历未写明团队规模和线上效果。
```

通过条件：

- 输出有 evidence、gaps、risks、questions_to_ask。
- 缺少关键证据时 decision 为“待复核”。
- 写入 Candidates 前触发 interrupt。

### C：触达话术

通过条件：

- 话术只是 draft。
- 不承诺薪资、offer、面试结果。
- 发送前必须人工确认。

### D：飞书 / Channel 复盘

通过条件：

- 只基于已有 Requisitions / TalentMap / Candidates / Interactions。
- 不生成虚假候选人数据。
- 创建外部任务前触发 interrupt。

## 4. 测试目录

```text
tests/unit/test_skill_taxonomy.py
tests/unit/test_title_alias_mapper.py
tests/unit/test_candidate_matcher.py
tests/unit/test_review_rules.py
tests/unit/test_import_case_data.py
tests/unit/test_council_opinions.py
tests/unit/test_council_synthesizer.py
tests/unit/test_agent_policy.py
tests/unit/test_artifact_store.py
tests/unit/test_agent_harness.py
tests/unit/test_context_pack_minimal.py
tests/unit/test_skill_registry.py
tests/unit/test_memory_gateway.py
tests/unit/test_vector_memory_store.py
tests/unit/test_embedding_gateway.py

tests/graphs/test_council_deliberation_graph.py
tests/graphs/test_headhunter_war_room_graph.py
tests/graphs/test_intake_calibration_graph.py
tests/graphs/test_talent_mapping_graph.py
tests/graphs/test_candidate_screening_graph.py
tests/graphs/test_wait_for_artifacts.py

tests/security/test_sensitive_attribute_bias.py
tests/security/test_no_side_effect_without_interrupt.py
tests/security/test_no_pii_in_logs.py
tests/security/test_council_rejects_unsafe_requests.py
tests/security/test_business_api_cannot_bypass_council.py
tests/security/test_agent_policy_enforcement.py
tests/security/test_agent_cannot_call_other_agents.py
tests/security/test_db_scope_redaction.py
tests/security/test_memory_write_requires_approval.py

tests/integration/test_feishu_gateway_contract.py
tests/integration/test_feishu_event_to_war_room_graph.py
tests/integration/test_feishu_event_fast_ack.py
tests/integration/test_feishu_event_idempotency.py
tests/integration/test_feishu_card_action_resume.py
tests/integration/test_discord_gateway_contract_optional.py
tests/integration/test_feishu_message_rate_limit.py
tests/integration/test_feishu_bitable_batching.py
tests/integration/test_feishu_auth_provider.py
tests/integration/test_interrupt_resume_with_thread_id.py
tests/integration/test_postgres_repositories.py
tests/integration/test_postgres_checkpointer.py
tests/integration/test_pgvector_memory_retrieval.py
tests/integration/test_run_memory_vectorization.py
tests/integration/test_hermes_adapter_public_api_only.py
tests/integration/test_search_gateway_source_refs.py
tests/integration/test_feishu_war_room_cards.py
tests/integration/test_agent_task_rerun.py
tests/integration/test_container_network_policy.py
```

## 5. 调用链测试

必须覆盖：

```text
飞书消息或卡片 -> /feishu/events 或 /feishu/card-actions -> 3 秒内 ACK/toast -> async dispatcher -> TaskIntakeParser -> double check -> headhunter_war_room_graph -> council_deliberation_graph
模糊输入 -> human_questions -> 飞书追问卡片
完整 JD -> CouncilDecision -> intake_calibration_graph
TalentMap 或 Bitable 写入前 -> interrupt -> 飞书确认卡片/表单
approve/edit/reject -> 飞书卡片 -> HumanApproval -> Command(resume=...)
相同 thread_id -> 状态可恢复
每次响应和 War Room 卡片展示 council_mode 和 mode_reason
用户要求“三省六部” -> council_mode=full_council
```

## 6. 绕过防护测试

必须覆盖：

```text
业务 API 不能直接执行子图，必须生成或接收 CouncilDecision。
业务子图不能直接解析 user_input。
HermesAdapter 只能调用公开 API，不能直接操作数据库、FeishuGateway、FeishuBitableGateway 或 Discord optional adapter。
未 approve 不得写入业务表、创建外部文档、创建外部任务、保存推荐结论或发送外部触达。
任务授权后的 War Room 进度卡、追问卡、确认卡和结果卡允许自动发送，并写入 AgentRuns。
reject 只写审计，不执行副作用。
edit 使用人工修改内容继续执行。
```

## 7. Agent 协作测试

必须覆盖：

```text
SourcingMappingAgent 必须等待 StrategyDraftArtifact。
MarketCompAgent 读取 TalentMapDraft 后才能输出 MarketSupplyOpinion。
ComplianceRiskAgent 读取所有 draft 脱敏版本后才能输出 ComplianceReview。
CouncilSynthesizer 缺少任一必需 CouncilOpinion 时不得输出 ready_to_execute=true。
下游 Agent 缺少 artifact 时返回 missing_artifacts，不猜测结果。
Artifact version 增加后，重跑只读最新可用版本或明确指定版本。
```

## 8. 权限与 Gateway 测试

必须覆盖：

```text
未授权 Agent 调 SearchGateway 会被拒绝。
SearchGateway 返回必须包含 source_refs。
公司背调当前事实没有 SearchGateway source_refs 时不得通过 EvidenceConsistencyReviewer。
CandidateJudgementAgent 默认不可读取联系方式明文。
DatabaseGateway 对 PII 字段默认脱敏。
Agent 不能直接调用其他 Agent endpoint。
Agent 不能绕过 ActionGateway 写飞书、Bitable、Discord optional adapter 或业务数据库。
SkillRegistry 只能加载 AgentPolicy.allowed_skills。
```

## 9. Token / Harness / 记忆测试

必须覆盖：

```text
高风险任务自动加入 ComplianceRiskAgent。
简单任务可走 lite 会审并减少 optional_agents。
用户明确要求“三省六部”时强制 full_council。
所有运行都记录 council_mode、mode_reason、required_agents、optional_agents。
超出 max_tool_calls_per_task 会停止工具调用。
超出 token_budget 会降级展示模式或返回人工追问。
LangGraph state 只保存 artifact refs 和摘要，不保存完整 artifact 内容。
ContextPack 不包含完整聊天历史、完整 RecruitmentState、完整 node_history、全量 AgentRuns、全量 artifacts 或全量长期记忆。
TaskIntakeParser 每个关键字段必须有 field_source 和 confidence；无 source 推断只能进入 assumptions。
Double Check approve 后冻结 CanonicalTaskBrief version，下游 Agent 只能读取冻结版。
RunMemory 自动写入并向量化，但只能通过 MemoryGateway 检索命中后以 MemoryRef 注入。
MemoryGateway 只能返回 tenant/guild/user/project/requisition/candidate scope filter 和 policy 允许 scope 的 top-k MemoryRef，不能越权返回全文。
简历关键词抽取以当前简历/JD artifact 为主，长期记忆只提供 taxonomy、项目规则、用户修正和 SOP。
超出 max_context_tokens 时先裁剪低相关 memory_refs，再裁剪 optional source_refs，不允许硬塞全历史。
长期记忆只能 propose_update，未经审批不得进入 ProjectMemory / AgentMemory / CaseMemory / UserCorrectionMemory。
长期记忆未经审批、撤销、过期、低置信或 pii_level 超出 policy 时不可被检索为 active memory。
CandidateJudgementAgent 不得读取联系方式明文记忆。
CouncilSynthesizerAgent 不得读取各 Agent 原始 ContextPack。
错误记忆可以撤销或标记失效。
```

## 10. 飞书 / War Room 可观测测试

必须覆盖：

```text
每个 Agent 输出一张 War Room 卡片。
卡片显示任务目标、读取 artifacts、工具轨迹、证据、风险、token 消耗。
卡片显示 council_mode 和 mode_reason。
brief / standard / debug 展示模式可切换。
用户评论能进入 AgentRuns。
用户修改某个建议后形成 UserCorrection artifact。
用户要求重跑指定 AgentTask 时，不重跑整条 graph。
```

## 11. 飞书平台契约测试

必须覆盖：

```text
`/feishu/events` 对 URL verification/challenge 在 3 秒内返回。
`/feishu/events` 和 `/feishu/card-actions` 必须先校验签名、timestamp、nonce、verification token/encrypt key 和 tenant/chat allowlist。
ACK/toast 前已经把 feishu_event_logs / feishu_card_actions / feishu_outbox 写入 PostgreSQL 并提交成功。
重复 event_id / action_id 只 ACK，不重复创建 graph run、War Room 卡片或 Bitable 写入。
并发重复 event_id / action_id 通过数据库唯一约束和原子 insert / claim 只产生一个处理者。
飞书卡片 action 能解析 thread_id、action_id、interrupt_id、idempotency_key、decision。
重复 idempotency_key 不重复 resume，不重复执行副作用。
并发重复 idempotency_key 通过数据库唯一约束和原子 insert / claim 只 resume 一次。
飞书机器人不在群、用户不在可用范围、缺少消息权限时，不发送卡片并写入可读错误。
遇到 429 或 飞书限频错误时尊重 Retry-After，进入持久退避重试；超过上限进入 dead letter，不丢失 AgentRuns 审计。
Bitable 契约测试作为第一版主链路的一部分，但必须在 HumanApproval 后通过 outbox 执行。Discord 契约测试仅作为 optional adapter。
```

## 12. 容器隔离测试

必须覆盖：

```text
agent-* 容器无模型 Key。
agent-* 容器无飞书 Key。
agent-* 容器无数据库写凭证。
agent-* 容器不可直接访问公网。
agent-* 容器不可访问其他 agent-* endpoint。
只读文件系统、cap_drop、非 root 用户配置存在。
```

## 13. 设计级阻断项

以下任一项未满足，不能开工：

```text
没有案例数据导入规范。
没有三省六部会审层。
没有 council_mode 自动选择和用户强制 full_council 规则。
没有 headhunter_war_room_graph 根图。
业务 graph 能绕过 CouncilDecision 直接执行。
Codex / LangGraph / 飞书 / Hermes 的职责边界不清楚。
没有 AgentRuns 审计模型。
没有 interrupt 人工确认设计。
没有飞书事件/卡片 3 秒内 ACK/toast + 异步 graph 派发设计。
没有飞书 event_id / action_id / idempotency_key 幂等设计。
没有飞书卡片到 `HumanApproval` / resume 的设计。
没有 FeishuGateway 的 tenant_access_token、卡片回调、限频退避和消息/卡片更新设计。
没有 PostgreSQL + pgvector 第一版向量记忆主链路设计。
没有 ContextPack 最小上下文注入和 Agent allowlist 设计。
没有 MemoryGateway 的权限过滤、top-k、rerank、MMR 去重、token budget 裁剪和检索审计设计。
没有 policy-engine / AgentHarness / ArtifactStore 设计。
Agent 能直接互相调用。
Agent 能直接访问数据库、公网、飞书、Discord optional adapter 或模型 Key。
没有 Feishu fake/mock gateway 契约测试能力。
第一版主链路没有真实 Postgres、Postgres checkpointer、真实飞书事件、交互卡片、War Room 群消息和 Bitable 同步 接入方案。
候选人评分没有证据链。
Mapping 结果没有来源和理由。
敏感属性没有测试样例。
PII 日志策略不清楚。
长期记忆没有审批和撤销机制。
```

## 14. 最终验收

- 本地 API 可启动。
- 飞书消息或卡片能先进入 TaskIntakeParser，并在用户 double check approve 后进入同一个 `headhunter_war_room_graph`。
- TaskIntakeParser 输出关键字段有 field_source 和 confidence；无 source 推断只进入 assumptions。
- double check approve 后冻结 CanonicalTaskBrief version；edit 生成新 version 并再次确认。
- 飞书事件/卡片入口先快速 ACK/toast 并异步处理，不会因 LLM/graph 超时导致 飞书回调失败。
- `/council/deliberate` 能处理模糊和完整输入。
- 每次运行输出 `council_mode` 和 `mode_reason`；用户要求“三省六部”时使用 `full_council`。
- `/requisitions/calibrate` 能输出岗位作战单。
- `/talent-map` 能输出目标公司、title、搜索语句。
- 候选人筛选输出证据、缺口、风险、追问问题。
- 未人工确认不得写入业务数据、发布报告、创建外部任务、保存推荐结论或发送外部触达。
- 任务授权后的 War Room 进度卡、追问卡、确认卡和结果卡可自动发送，并写入 AgentRuns。
- `Command(resume=...)` 能用同一个 `thread_id` 恢复执行。
- `/feishu/card-actions` 能承接飞书卡片 approve/edit/reject，且重复点击不会重复执行。
- FeishuGateway 能处理 tenant_access_token、权限不足、限频、消息/卡片更新失败和 dead letter。
- 第一版主链路使用 Postgres、pgvector 和真实飞书事件、交互卡片和 Bitable 同步；mock / fake gateway 仅用于测试和失败隔离。
- Hermes/MCP 只能通过公开 API 调用，不能绕过根图、policy 或 action-gateway。
- Agent 权限、Artifact 依赖、token 预算、记忆审批和 War Room 可观测全部有测试。
- 第一版使用 Postgres + pgvector + 真实 MemoryGateway；mock / fake 只用于测试。
- 每个 Agent 的 ContextPack 只包含当前节点必要的 artifact refs、memory refs、source refs 和预算信息。
- ReviewGate 是 artifact-level quality gate，通过 conditional edge 路由 pass / needs_fix / needs_human。
- needs_fix 只回对应 repair_node 一次，不重跑完整 graph；第二次失败进入人工确认。
- 公司当前事实依赖 SearchGateway source_refs，长期记忆不得替代实时搜索。
- AgentRuns 可完整复盘。
