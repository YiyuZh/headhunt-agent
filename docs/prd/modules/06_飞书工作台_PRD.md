# 模块 06：飞书 War Room 工作台 PRD

## 1. 模块目标

第一版主工作台固定为飞书。飞书不是简单通知通道，而是普通用户发起任务、确认结构化任务、查看进度、审批副作用、编辑草稿、配置模型和同步 Bitable 展示数据的日常入口。

Discord 保留为后续 optional adapter，不进入第一版主验收。现有 Discord `/model` 和 interaction 代码可保留用于历史测试和后续接入，但 PRD、手册和第一版交付口径以 Feishu First 为准。

核心流程：

```text
用户在飞书群 @机器人或提交卡片
-> /feishu/events 快 ACK 并写入 PostgreSQL/outbox
-> TaskIntakeParser 生成 CanonicalTaskBrief
-> 飞书任务确认卡 double check
-> 用户 approve / edit / reject
-> approve 后进入 headhunter_war_room_graph
-> 飞书 War Room 群消息展示进度、证据、审查和待确认动作
-> 飞书卡片 approve/edit/reject 恢复 graph 或触发人工确认
-> 人工确认后同步 Bitable 展示表
```

## 2. 飞书入口

第一版主入口：

```text
POST /feishu/events
POST /feishu/card-actions
```

`/feishu/events` 承接：

- URL verification / challenge。
- 机器人入群、被 @、收到用户任务输入或附件引用。
- 事件验签、解密、去重、payload 入库和 outbox 排队。

`/feishu/card-actions` 承接：

- 任务 double check 的 approve / edit / reject。
- HumanApproval 的 approve / reject / edit。
- 模型 profile 的 add / list / use / test / revoke。
- 卡片按钮、表单、选项值和幂等参数解析。

飞书回调必须先校验签名、timestamp、nonce、verification token / encrypt key。非 challenge 请求缺签名或签名错误必须拒绝，不能假 ACK。

## 3. 快 ACK 与异步 Graph

飞书 HTTP 回调只做轻量同步处理：

```text
验签 / challenge
-> 解析 event 或 card action
-> 写 feishu_event_logs / feishu_card_actions
-> 写 feishu_outbox
-> 3 秒内 ACK / toast
-> worker 异步执行 task_intake、graph_dispatch、card_send、card_update、bitable_write、resume
```

不得在 `/feishu/events` 或 `/feishu/card-actions` 请求内运行完整 AI graph、调用外部模型、写 Bitable 或发送长期任务结果。

所有飞书消息发送、卡片更新、Bitable 写入和 graph resume 必须通过 durable outbox claim 执行，使用 `idempotency_key` 防止重复投递。

## 4. 用户 Double Check

系统不得在用户原始输入后直接启动正式 graph。必须先生成结构化任务确认卡：

```text
用户飞书输入
-> TaskIntakeParser
-> CanonicalTaskBrief
-> RequisitionBrief / ResumeProfile / CandidateEvidencePack / OutreachDraftInput
-> SchemaValidator
-> 飞书任务确认卡
-> 用户 approve / reject / edit
-> approve 后冻结 CanonicalTaskBrief.version
-> graph_dispatch
```

任务确认卡必须展示：

```text
任务类型
岗位 / 候选人 / 输出目标
关键字段及 field_source / confidence
缺失字段和 assumptions
风险等级
推荐 council_mode 和 mode_reason
是否强制三省六部
下游将读取的 ArtifactRef / MemoryRef / SOPRef / SourceRef 摘要
不会传入的上下文及原因
```

用户动作：

- `approve`：写入 `TaskDoubleCheckApproval`，冻结结构化任务版本，排队 `graph_dispatch`。
- `edit`：打开飞书卡片表单或编辑卡，用户修正字段后重新 parse 并再次发确认卡。
- `reject`：记录原因，不进入 graph；reject reason 可生成 `UserCorrectionMemoryProposal`，长期记忆仍需审批后才 active。

## 5. 飞书 War Room

每个任务绑定一个飞书 War Room 群、话题串或消息线程。War Room 展示：

```text
thread_id / task_id
council_mode / mode_reason
当前阶段和下一步
结构化任务摘要
ReviewGate 结果
artifact_refs
memory_refs 命中原因和 token 估算
sop_refs
source_refs
待人工确认动作
```

默认展示结论、证据摘要、风险、审查状态和待办。管理员 debug 视图也不得展示原始 prompt、完整聊天历史、未授权 artifact 全文、明文 API Key 或隐私内容。

## 6. 飞书模型配置 BYOK

多人 BYOK 保留，但入口从 Discord `/model` 改为飞书机器人和交互卡片：

```text
飞书卡片：模型配置
-> provider: openai / deepseek
-> model_name
-> usage: chat（当前飞书落地卡先开放 chat；embedding profile 是后续入口）
-> display_name
-> api_key
-> optional base_url
-> 服务端用 MODEL_SECRET_ENCRYPTION_KEY 加密保存
```

约束：

- 用户级 profile 默认只允许本人使用；guild/project 共享 profile 后续需管理员授权。
- API Key 永不在卡片、日志、AgentRun、War Room 或查询接口中明文返回。
- OpenAI / DeepSeek 可作为 chat profile；OpenAI embedding profile 单独配置；DeepSeek chat model 不允许作为 embedding profile。
- AgentRun 只记录 `model_profile_id`、provider、model_name、owner_user_id 和调用摘要，不记录 key。

## 7. 副作用边界

可自动写入：

```text
feishu_event_logs
feishu_card_actions
feishu_outbox
Feishu message/card delivery audit
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
Bitable 业务同步写入
外部触达发送
第三方任务创建
长期记忆 active
```

Bitable 写入必须走 `ActionProposal -> HumanApproval -> feishu_outbox -> FeishuBitableGateway`，不得由 Agent 或 graph node 直接调用。

## 8. Gateway 与数据底座

第一版主实现：

```text
FeishuGateway：发送 / 更新消息和交互卡片、处理限频、权限、幂等和失败审计。
FeishuBitableGateway：同步业务展示表，支持 client_token、分片写入、record_id 映射。
PostgreSQL：主业务数据、checkpoint、outbox、AgentRuns、Artifacts、Memory 和审批。
pgvector：第一版向量记忆底座。
```

Bitable 是业务展示和同步表，不是唯一主库，也不得替代 PostgreSQL 审计、权限、记忆和幂等记录。

## 9. Discord Optional Adapter

Discord 降级为后续 optional adapter：

- 不作为第一版普通用户入口。
- 不作为第一版验收必需链路。
- 不删除现有 Discord 代码和文档；相关文档必须标注 optional adapter / 历史实现状态。
- 后续接入 Discord 时仍必须遵守同一套 `CanonicalTaskBrief`、ReviewGate、MemoryGateway、HumanApproval 和 Gateway 边界。

## 10. 验收标准

- 飞书事件回调能验签、challenge 和 3 秒内 ACK。
- 飞书卡片回调能承接 approve / edit / reject，并写入 HumanApproval 或 double check 记录。
- 未 double check approve 不得进入正式 graph。
- 飞书 War Room 必须展示 council_mode、mode_reason、ReviewGate、memory_refs、sop_refs 和 token 估算。
- Bitable 同步必须在人工确认后通过 outbox 执行。
- PostgreSQL / pgvector 仍是主库和记忆底座。
- Discord 只能出现在 optional adapter 或历史已实现代码语境。
- 真实飞书后台、真实 Bitable 权限、真实 OpenAI/DeepSeek 和服务器 Docker Compose 联调未完成前必须标注“未验证”。
