# AI 猎头隔离 Agent 协作系统 PRD：总入口与拼接说明

> 模块化目录 `/Users/w/Documents/lietou/docs/prd/modules/` 把 PRD 拆成核心模块，便于逐步实现、逐步审核、逐步追加案例数据。完整拼接版同步到 `/Users/w/Documents/lietou/docs/prd/AI猎头作战系统_PRD_完整拼接版.md` 和 `/Users/w/Documents/lietou/docs/prd/ai猎头_agent体系_codex搭建说明.md`。

## 1. 总体定位

本系统不是“多 Agent 聊天机器人”，也不是一个主 Agent 随意指挥子 Agent 的黑盒系统，而是一个真实猎头作战系统和隔离 Agent 协作系统：

```text
用户输入
-> orchestrator-api / FastAPI 运行时入口
-> 飞书事件 / 卡片回调快 ACK
-> TaskIntakeParser 生成 CanonicalTaskBrief
-> 用户 double check 结构化任务
-> headhunter_war_room_graph
-> policy-engine 创建 TaskPlan / 权限 / 预算
-> 自动选择 council_mode（triage / lite / standard / full_council）
-> council_deliberation_graph 会审
-> AgentHarness 调度专业 Agent 并构造最小 ContextPack
-> MemoryGateway 通过 pgvector 检索必要记忆 refs
-> ArtifactStore 交换结构化产物
-> 客户需求校准
-> 人才地图 Mapping
-> 搜索策略与候选人导入
-> 候选人证据筛选
-> 个性化触达 / 客户推荐
-> Review + interrupt + action-gateway
-> 飞书 War Room 跟进与 Bitable 同步
```

核心目标：

```text
高效率：减少手工整理，快速产出下一步动作。
高质量：每个判断有证据链，每个输出可复核。
低风险：不自动抓取、不自动群发、不绕平台风控、不替代人做最终判断。
```

运行边界：

```text
开发层：Codex 负责写代码、改 PRD、跑测试，不是生产运行时。
运行层：FastAPI + LangGraph 承载根图、state、Postgres checkpoint、pgvector 记忆、interrupt/resume。
隔离协作层：policy-engine + AgentHarness + Gateway + ArtifactStore 管理 Agent 权限和协作。
入口层：飞书事件回调、飞书交互卡片、本地 API、后续可选 Hermes/MCP 调用公开入口。
工作台层：飞书 War Room 展示结构化过程、证据、评论、修改、批准和复盘；Bitable 只做业务展示和同步表。
```

目标系统一步到位设计；工程实现可以按波次拆分，但第一版主链路必须直接接入真实 Postgres + pgvector、真实飞书事件回调、飞书交互卡片、飞书 War Room 群消息和 Bitable 同步。Discord 作为后续可选 adapter，不进入第一版主验收路径。mock / fake gateway 只用于单元测试、CI 和失败隔离，不作为第一版主运行路径。

## 2. 模块文件顺序

按以下顺序拼接就是完整 PRD：

```text
00_总PRD_拼接入口.md
01_产品总纲_顶级猎头工作流.md
02_三省六部会审层_PRD.md
03_数据体系与案例导入_PRD.md
04_Mapping子系统_PRD.md
05_LangGraph业务工作流_PRD.md
06_飞书工作台_PRD.md
07_质量合规与安全_PRD.md
08_工程代码设计_PRD.md
09_测试验收与模拟执行_PRD.md
10_运行时调用与部署_PRD.md
11_Agent隔离与容器化架构_PRD.md
12_Agent协作可观测记忆Harness_PRD.md
13_飞书工作台与AgentSOP交付_PRD.md
```

## 3. 拼接命令

后续需要生成完整 PRD 时，在 `/Users/w/Documents/lietou/docs/prd/modules/` 运行：

```bash
cat \
  00_总PRD_拼接入口.md \
  01_产品总纲_顶级猎头工作流.md \
  02_三省六部会审层_PRD.md \
  03_数据体系与案例导入_PRD.md \
  04_Mapping子系统_PRD.md \
  05_LangGraph业务工作流_PRD.md \
  06_飞书工作台_PRD.md \
  07_质量合规与安全_PRD.md \
  08_工程代码设计_PRD.md \
  09_测试验收与模拟执行_PRD.md \
  10_运行时调用与部署_PRD.md \
  11_Agent隔离与容器化架构_PRD.md \
  12_Agent协作可观测记忆Harness_PRD.md \
  13_飞书工作台与AgentSOP交付_PRD.md \
  > ../AI猎头作战系统_PRD_完整拼接版.md

cp ../AI猎头作战系统_PRD_完整拼接版.md ../ai猎头_agent体系_codex搭建说明.md
```

## 4. 实现顺序

```text
1. 运行时调用和部署边界
2. Agent 隔离、Gateway、PolicyEngine、ArtifactStore
3. 数据体系和案例导入
4. council_mode 路由和会审层
5. 客户需求校准
6. Talent Mapping
7. 候选人筛选
8. 触达/推荐报告
9. 飞书 War Room 工作台
10. AgentSOPRegistry、ReviewGate、记忆、Harness、审计、合规、测试
```

## 5. 总验收标准

- 模糊需求不会直接执行，必须先由会审层输出追问。
- 每次会审必须输出当前使用的 `council_mode` 和选择原因；用户明确要求“三省六部”时必须使用 `full_council`。
- 完整 JD 能转成岗位作战单和 TalentMap。
- 候选人筛选必须输出证据、缺口、风险和追问问题。
- 任务正式运行前必须展示结构化任务确认卡，用户 double check approve 后才进入正式 graph。
- TaskIntakeParser 的关键字段必须带 `field_source` 和 `confidence`；无 source 推断只能进入 assumptions；double check approve 后冻结 `CanonicalTaskBrief.version`。
- 业务数据写入、外部发送、报告发布、推荐结论和第三方任务创建都必须人工确认；任务确认后的 War Room 进度卡、追问卡、确认卡和结果卡可自动发送。
- 飞书是第一版猎头工作台；Bitable 是人工确认后的业务展示和同步表；Discord 是后续可选 adapter。
- 案例数据可以用 Markdown / CSV / JSON 导入，且可重复导入。
- Agent 不能互相直接调用，只能通过 ArtifactStore 交换结构化产物。
- LangGraph state 只保存 artifact 摘要和引用，artifact 全文通过 ArtifactStore 的 `content_ref` 读取。
- Agent 上网、查库、读记忆、用 skills 都必须经过 policy 和 gateway。
- 飞书 War Room 能看到每个 Agent 的结构化过程、证据、工具轨迹、token 消耗、ReviewGate 结果和可编辑建议。
- ReviewGate 是 artifact-level quality gate，必须通过 conditional edge 路由 `pass / needs_fix / needs_human`；`needs_fix` 只回对应 repair_node 一次，第二次失败进入人工确认。
- SOPRegistry 只能以 `sop_refs` 和少量摘要进入 ContextPack；不得整包注入所有 SOP。
- 长期记忆必须有 30 天、90 天或 permanent 的 retention policy，过期或撤销后不得被检索为 active memory。
- MemoryGateway 必须按 tenant/guild/user/project/requisition/candidate scope filter 检索；公司当前事实必须由 SearchGateway source_refs 支撑，长期记忆不得替代实时搜索。
- AgentRuns 能完整复盘每次执行的输入、会审、节点、审核、人工确认和副作用结果。
- Codex、LangGraph、飞书、Hermes/MCP 的职责边界清楚；Hermes/MCP 只能作为外部入口，不能绕过根图、policy 和 action-gateway。
