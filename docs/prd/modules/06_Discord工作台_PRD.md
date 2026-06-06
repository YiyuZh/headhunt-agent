# 模块 06：Discord War Room 工作台 PRD

## 1. 模块目标

第一版主工作台从飞书切换为 Discord。Discord 不是简单通知通道，而是普通用户发起任务、确认结构化任务、查看进度、审批副作用、编辑草稿和查询状态的日常入口。

飞书 / Bitable 不再作为第一版主链路，只保留为后续可选 adapter。现有 Feishu 代码可以暂存，但 PRD 验收口径以 Discord First 为准。

核心目标：

```text
用户不需要懂 API 或 JSON
-> 用 slash command 发起猎头任务
-> Bot 快速 ACK/defer
-> TaskIntakeParser 生成结构化任务
-> 用户 double check
-> 确认后进入 LangGraph
-> War Room thread 展示进度、证据、审查、token 和待确认动作
-> 用户通过 button / select / modal approve、reject、edit
```

## 2. Discord 入口

第一版只依赖 Discord application commands 和交互组件：

```text
/headhunt new
/headhunt candidate
/headhunt map
/headhunt status thread_id
/headhunt memory
button approve / reject / edit
select menu 选择 council_mode 或候选操作
modal 编辑岗位、候选人、话术、报告草稿和 reject reason
```

v1 不依赖 `MESSAGE_CONTENT` privileged intent，不读取普通频道聊天内容作为主入口。用户输入必须来自 slash command option、modal input、button/select value 或附件引用。

## 3. Interaction 快 ACK

新增公网入口：

```text
POST /discord/interactions
```

处理规则：

- 校验 `X-Signature-Ed25519`、`X-Signature-Timestamp` 和 raw body。
- 处理 Discord PING 并返回 PONG。
- 3 秒内完成 ACK / defer，不在 HTTP 请求里跑完整 AI 工作流。
- ACK 前把 interaction payload、signature 校验结果、guild/channel/user、idempotency_key 写入 PostgreSQL。
- 写入 `discord_event_logs` 与 `discord_outbox` 后，由 worker 异步派发 `task_intake`、`graph_dispatch`、`message_update`、`followup_send` 或 `modal_response`。
- interaction token 只用于短期 follow-up / edit original response；长期消息更新必须通过 Bot token 和 durable outbox。

## 4. 用户 Double Check

系统不得在用户原始输入后直接启动正式 graph。必须先生成结构化任务确认卡：

```text
用户输入
-> TaskIntakeParser
-> CanonicalTaskBrief
-> RequisitionBrief / ResumeProfile / CandidateEvidencePack / OutreachDraftInput
-> SchemaValidator
-> Discord 任务确认卡
-> 用户 approve / reject / edit
-> approve 后才进入 headhunter_war_room_graph
```

任务确认卡必须展示：

```text
任务类型
岗位/候选人/输出目标
系统理解的关键字段
缺失字段
风险等级
推荐 council_mode
本次是否强制三省六部
会传给下游 Agent 的 artifact refs / memory refs 摘要
不会传入的上下文及原因
```

用户动作：

- `approve`：写入 `TaskDoubleCheckApproval`，排队 `graph_dispatch`。
- `edit`：打开 modal，用户修正字段后重新生成结构化任务并再次展示确认卡。
- `reject`：记录原因，不进入 graph；reject reason 可生成 `UserCorrectionMemoryProposal`，但长期记忆仍需审批后才 active。

## 5. War Room Thread

每个任务创建一个 Discord War Room thread 或指定频道消息串。War Room 展示：

```text
thread_id / task_id
council_mode / mode_reason
当前阶段
结构化任务摘要
ReviewGate 结果
artifact_refs
memory_refs 命中原因和 token 估算
sop_refs
source_refs
待人工确认动作
下一步建议
```

默认展示 `standard` 信息密度：结论、证据摘要、风险、审查状态和待办。`debug` 仅对管理员开放，且仍不展示原始 prompt、完整历史、未授权全文 artifact 或隐私内容。

## 6. 副作用边界

可自动写入：

```text
discord_event_logs
discord_outbox
discord_interactions
discord_message_map
AgentRuns
ArtifactStore 摘要和 content_ref
RunMemory
Postgres checkpoint
任务确认卡、进度卡、追问卡、确认卡、结果卡
```

必须人工确认：

```text
业务数据写入
候选人推荐结论保存
报告发布
Discord 对外触达发送
飞书/Bitable deferred adapter 写入
外部邮件/消息/招聘平台触达
长期记忆 active
```

## 7. Discord Gateway

第一版新增 `DiscordGateway`，所有 Discord 调用都通过 Gateway，不允许 graph node 或 Agent 直接访问 Discord SDK / HTTP。

```python
class DiscordGateway:
    def verify_interaction(self, raw_body: bytes, signature: str, timestamp: str) -> None: ...
    def defer_response(self, interaction_id: str, token_ref: str, *, ephemeral: bool) -> None: ...
    def send_followup(self, token_ref: str, payload: dict) -> str: ...
    def edit_original_response(self, token_ref: str, payload: dict) -> None: ...
    def create_thread(self, channel_id: str, name: str, reason: str) -> str: ...
    def send_channel_message(self, channel_id: str, payload: dict) -> str: ...
    def update_message(self, channel_id: str, message_id: str, payload: dict) -> None: ...
    def open_modal_response(self, interaction_id: str, token_ref: str, modal: dict) -> None: ...
```

Gateway 约束：

- 所有请求写 `discord_outbox`，worker claim 后执行。
- 对 guild/channel 做 allowlist 校验。
- 使用 idempotency_key 防止重复创建 thread、重复发确认卡、重复 resume。
- rate limit 必须读取 Discord 响应并持久化退避。
- ephemeral 只用于用户私密 ACK、错误提示和敏感编辑提示；任务过程默认进 War Room thread。

## 8. 数据表

第一版新增：

```text
discord_installations
discord_event_logs
discord_interactions
discord_outbox
discord_message_map
task_double_check_approvals
agent_sop_registry
review_results
memory_retention_jobs
```

PostgreSQL 仍是主数据底座，pgvector 仍是第一版向量记忆底座。

## 9. 飞书 / Bitable Adapter 状态

飞书与 Bitable 进入 deferred adapter：

- 不作为第一版主用户入口。
- 不作为第一版验收必需链路。
- 不删除现有 Feishu 代码，以免破坏历史测试和可回滚路径。
- 文档中所有“第一版真实飞书主链路”口径必须改为“后续可选 adapter / 旧代码待迁移”。

## 10. 验收标准

- 用户能通过 Discord slash command 发起任务。
- `/discord/interactions` 能验证签名并 3 秒内 ACK/defer。
- TaskIntakeParser 生成结构化任务后必须让用户 double check。
- 未 double check approve 不得进入正式 graph。
- War Room 必须展示 council_mode、mode_reason、ReviewGate、memory_refs、sop_refs 和 token 估算。
- v1 不依赖 `MESSAGE_CONTENT` privileged intent。
- 飞书/Bitable 不再是第一版主链路。
- 真实 Discord 联调未完成前必须标注“未验证”。
