# Discord 接入操作手册

## 状态

本文是 Discord First 第一版主入口的人工操作手册。当前 DiscordGateway、`/discord/interactions`、slash command 注册、button、modal 和 War Room thread/message 尚未实现；真实 Discord 联调未验证。

## 你需要准备

- 一个 Discord 账号。
- 一个可管理的 Discord Server。
- 一个可公网访问的 HTTPS 回调地址，本地可用 ngrok 或 Cloudflare Tunnel。
- 本项目服务已启动，且 PostgreSQL / pgvector / OpenAI 配置可用。

## 创建 Discord Application

1. 打开 [Discord Developer Portal](https://discord.com/developers/applications)。
2. 点击 `New Application`。
3. 填写应用名，例如 `AI Headhunter War Room`。
4. 进入 `General Information`：
   - 复制 `Application ID`，写入 `.env` 的 `DISCORD_APPLICATION_ID`。
   - 复制 `Public Key`，写入 `.env` 的 `DISCORD_PUBLIC_KEY`。

## 创建 Bot

1. 进入 `Bot` 页面。
2. 点击 `Reset Token` 或复制当前 token。
3. 写入 `.env`：

```env
DISCORD_BOT_TOKEN=
```

4. v1 不需要开启 `MESSAGE CONTENT INTENT`。
5. 建议只启用必要权限，不给管理员权限。

## 配置 Interaction Endpoint

本地联调时：

```bash
uvicorn app.main:app --reload --port 8000
ngrok http 8000
```

把公网地址填到 Discord Developer Portal：

```text
Interactions Endpoint URL:
https://your-domain/discord/interactions
```

Discord 会发送 PING 测试，服务必须通过 Ed25519 signature 校验并返回 PONG。

当前尚未实现 `/discord/interactions`，所以该步骤真实联调未验证。

## 邀请 Bot 到服务器

1. 进入 `OAuth2 -> URL Generator`。
2. Scopes 选择：
   - `bot`
   - `applications.commands`
3. Bot Permissions 建议选择：
   - Send Messages
   - Use Slash Commands
   - Create Public Threads
   - Send Messages in Threads
   - Embed Links
   - Attach Files
4. 打开生成的 URL，把 Bot 邀请到目标 Discord Server。
5. 记录 guild id 和允许使用的 channel id：

```env
DISCORD_ALLOWED_GUILD_IDS=123456789
DISCORD_ALLOWED_CHANNEL_IDS=987654321
```

## 注册 Slash Commands

第一版命令：

```text
/headhunt new
/headhunt candidate
/headhunt map
/headhunt status
```

本项目目标提供：

```bash
curl -X POST http://127.0.0.1:8000/discord/commands/register
```

联调阶段建议先注册到单个 guild，避免 global command 缓存延迟：

```env
DISCORD_COMMAND_REGISTER_GUILD_ID=123456789
```

## 用户怎么使用

### 发起岗位任务

在允许的 Discord channel 输入：

```text
/headhunt new role_title:AI 平台后端负责人 location:上海 output_goal:生成岗位校准和人才地图
```

Bot 应立即 ephemeral 回复：

```text
已收到，正在解析任务。
```

随后发送任务确认卡，要求用户 double check。

### Double Check

确认卡展示：

```text
任务类型
用户目标
岗位摘要
候选人摘要
输出目标
缺失字段
风险等级
推荐 council_mode / mode_reason
系统假设
```

确认卡中的每个关键字段必须同时展示：

```text
字段值
field_source：来自 slash command / modal / 简历 artifact / JD artifact / SearchGateway source_ref
confidence：0-1 置信度
字段类型：fact 或 assumption
```

没有 `field_source` 的推断只能放进“系统假设”，不能放进岗位事实、候选人事实或公司当前事实。用户点击 Approve 后，系统必须冻结本次 `CanonicalTaskBrief.version`，下游 Agent 只能读取冻结版 `CanonicalTaskBrief`、ArtifactRef、MemoryRef、SOPRef 和 SourceRef。用户点击 Edit 时，系统必须生成新版本、重新解析并再次发送确认卡，不能在旧冻结版本上暗改。

用户可点：

```text
Approve：确认无误，启动正式 graph。
Edit：打开 modal 修改字段，重新解析后再次确认。
Reject：停止任务并记录原因。
```

### 强制三省六部

用户可以在命令中写：

```text
请用三省六部完整会审
```

或选择 council mode hint。系统必须显示：

```text
council_mode: full_council
mode_reason: 用户明确要求三省六部
```

### 查看状态

```text
/headhunt status thread_id:<thread_id>
```

返回摘要、pending approvals、artifact refs、memory_refs 和 token 估算，不展示原始 prompt 或隐私全文。

## 人工确认边界

任务授权后的进度卡、追问卡、确认卡和结果卡可以自动发送。

这些动作仍必须单独 approve：

```text
业务数据写入
报告发布
外部触达发送
候选人推荐结论保存
长期记忆进入 active
```

## 你需要给系统什么数据

最小数据：

```text
岗位：岗位名、地点、职级、薪资范围、JD、must-have、nice-to-have。
候选人：候选人摘要、当前公司、职位、地点、证据摘要。
历史案例：成功/失败案例摘要，可作为长期记忆提案。
用户偏好：目标公司、排除公司、话术风格、交付格式。
```

联系方式、微信、手机号、邮箱等高敏字段可以存，但默认不进入 CandidateJudgementAgent 的明文上下文。

数据来源边界：

```text
公司背调、在职状态、融资、新闻、裁员、组织变化：必须来自 SearchGateway source_refs。
长期记忆：只可提供 taxonomy、项目规则、用户修正、历史案例和 SOP，不得替代实时搜索。
简历关键词抽取：以当前简历 artifact 和当前 JD artifact 为主。
```

## 常见问题

### ACK 很快，但 graph 没跑完怎么办

这是正常设计。Discord 只要求 interaction 快速 ACK。完整 graph 在后台 worker 中跑，进度通过 War Room message/thread 更新。

### 为什么要 double check

因为系统会把自然语言拆成结构化任务。如果拆错了，后续所有 Agent 都会基于错误事实工作。Double check 是防止误推荐、误触达和 token 浪费的第一道门。

Approve 后版本会被冻结，这是为了让所有下游 Agent 使用同一份任务事实源，避免每个 Agent 重读原始输入后理解不一致。需要修改时走 Edit，新版本重新 double check。

### 为什么不用 MESSAGE_CONTENT

v1 主要用 slash command、button、select menu 和 modal，减少权限申请和普通消息隐私面。

## 未验证事项

- `/discord/interactions` 尚未实现。
- Discord signature、PING/PONG、slash command、button、modal、War Room thread/message 真实联调未验证。
- Discord Bot 权限、OAuth2 URL 和 command 注册需在实现后按官方后台实际页面校准。
