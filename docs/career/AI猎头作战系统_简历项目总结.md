# AI 猎头作战系统｜产品方向解决方案简历表达与技术复盘

适用岗位：产品方向解决方案、售前解决方案、AI 应用解决方案、行业数字化方案助理/实习。

这份材料的写法重点不是“我做了一个猎头工具”，而是：

> 设计并实现了一个面向专业服务场景的 AI 解决方案原型，围绕业务流程数字化、多 Agent 协同、结构化任务确认、可审计记忆和人工审批，构建端到端作战系统。

如果面试官偏技术，可以升级成：

> 我围绕一个复杂业务流程，持续做需求澄清、PRD 重构、架构收敛、集成方案取舍、AI Agent 治理、部署容器化和安全审查修复，最终形成一个可部署、可审计、可扩展的 AI 业务流程解决方案原型。

## JD 对齐判断

用户给出的岗位关键词是：

- 业务架构、应用架构、系统解决方案设计。
- 售前阶段需求分析、方案交流、方案设计、招投标。
- 跟踪产业和行业数字化技术趋势，形成行业研究和长期规划建议。
- 赋能合作伙伴，进行方案培训和宣讲。
- 研究竞争对手，提出竞争性策略。
- 主动使用 AI 工具辅助行业研究、方案撰写和数据分析。

所以简历表达要偏“业务问题 + 方案设计 + 架构落地 + 文档表达”，不要偏“我会写 FastAPI / Docker / LangGraph”。技术栈可以写，但要服务于“复杂业务流程 AI 化解决方案”这个主线。

公开资料里，京东物流超脑强调将 AI、大数据、运筹学和大模型等技术用于智能规划、仓储、运配、客服、营销等全链路降本增效；京慧平台也围绕销量预测、库存预警、库存仿真、智能补货和库存营销提供智能决策能力。这说明该岗位更看重“把业务流程、数据、AI 和工程交付串起来”的能力，而不是单点模型调用能力。

参考资料：

- [京东物流超脑赋能各行业数智化转型升级](https://www.jiemian.com/article/12057630.html)
- [京东智慧供应链战略相关报道](https://www.cs.com.cn/ssgs/gsxw/201703/t20170302_5193403.html)

## 简历可直接使用版本

### 项目名称

AI 猎头作战系统｜AI Agent 业务流程解决方案

### 一句话版

设计并实现面向猎头业务的 AI 作战系统，将岗位理解、人才地图、候选人评估、触达草稿、人工审批和复盘记忆串联为可审计的端到端工作流，验证 AI Agent 在专业服务场景中的解决方案落地能力。

### 项目经历版

**AI 猎头作战系统｜多 Agent 协同与智能业务流程解决方案**  
个人项目 / 产品方案设计 + 后端架构实现

- 从猎头业务流程出发，梳理岗位校准、候选人筛选、人才地图、触达报告、跟进复盘等核心场景，设计 Discord War Room 工作台和结构化任务确认流程，降低自然语言任务理解偏差。
- 设计 `FastAPI + LangGraph + PostgreSQL + pgvector + Docker Compose` 架构，构建多 Agent 会审、ArtifactStore、MemoryGateway、ReviewGate、人工审批和 outbox worker 等模块，形成可部署的 AI 工作流底座。
- 引入结构化交接机制，禁止 Agent 自由互聊和全量历史注入，通过 `CanonicalTaskBrief / ArtifactRef / MemoryRef / SOPRef` 控制上下文，减少 token 浪费并提升结论一致性。
- 设计长期记忆与检索式注入方案，使用 PostgreSQL + pgvector 管理 RunMemory、项目记忆、案例记忆和用户修正，支持 scope filter、审批、过期和审计，避免历史信息污染当前判断。
- 补充 PRD、接口文档、数据库设计、部署手册、Discord 接入手册和 GitHub README，完成从业务需求、系统方案到工程落地文档的完整表达。

### 更偏岗位 JD 的版本

**AI 猎头作战系统｜AI 应用解决方案设计与工程原型**

- 面向招聘/猎头场景完成业务架构拆解，将“岗位需求理解、候选人证据判断、人才地图、触达报告、复盘记忆”抽象为可配置 AI 工作流，输出完整 PRD、接口文档、数据库设计和部署手册。
- 设计应用架构：以 Discord 作为用户工作台，FastAPI 承载服务接口，LangGraph 编排多 Agent 流程，PostgreSQL/pgvector 支撑业务数据和长期记忆，Docker Compose 支持服务器一键部署。
- 设计 AI Agent 治理机制：通过结构化任务确认、最小上下文注入、ArtifactStore、ReviewGate 和人工审批，解决多 Agent 理解不一致、上下文膨胀、记忆污染和高风险动作越权问题。
- 将项目包装为可向客户讲解的行业解决方案：覆盖业务痛点、系统架构、部署方式、使用流程、风险边界和后续扩展路径，体现售前方案设计和 AI 工具提效能力。

### 简历压缩版

如果简历空间很紧，可以压缩成 3 条：

- 设计 AI 猎头作战系统原型，将岗位校准、人才地图、候选人评估、触达草稿、人工审批和复盘记忆抽象为端到端 AI 工作流。
- 基于 `FastAPI + LangGraph + PostgreSQL/pgvector + Docker Compose` 设计可部署架构，沉淀 PRD、接口文档、数据库设计、部署手册和用户操作手册。
- 通过结构化任务确认、ArtifactRef/MemoryRef 最小上下文注入、ReviewGate 审查和人工审批机制，提升多 Agent 输出一致性、可追溯性和业务安全性。

### 技术流版本

如果简历允许写得更技术，可以用这一版：

**AI 猎头作战系统｜复杂业务流程 AI 化解决方案原型**  
个人项目 / 需求分析 + 架构设计 + 后端工程骨架 + 部署文档

- 主导从业务需求到技术方案的完整拆解，将猎头场景中的岗位理解、人才地图、候选人判断、触达草稿、审批和复盘抽象为可编排的 LangGraph 工作流，并用 PRD、接口文档和数据库设计固化交付方案。
- 针对多 Agent 理解不一致和 token 膨胀问题，设计 `CanonicalTaskBrief`、`ArtifactRef`、`MemoryRef`、`SOPRef` 等结构化交接机制，禁止下游 Agent 重读全量历史或自由 agent-to-agent 对话。
- 设计 PostgreSQL + pgvector 长期记忆方案，通过 `MemoryGateway` 实现 scope filter、审批 active、过期治理和检索式注入，避免记忆污染当前判断。
- 设计 artifact-level `ReviewGate`、`ActionProposal`、`interrupt()`、`HumanApproval` 和 durable outbox，将关键产物审查、高风险动作审批、重复提交幂等和外部副作用隔离纳入系统架构。
- 补齐 Docker Compose 部署底座，组合 FastAPI API、outbox worker、PostgreSQL/pgvector 和 Caddy HTTPS 反向代理，沉淀服务器部署、Discord 接入、故障排查和 GitHub 展示文档。

## 项目技术演进时间线

这一段不要全部放进简历，但可以作为面试复盘材料。

| 阶段 | 你推动的问题/决策 | 技术结果 | 能力体现 |
| --- | --- | --- | --- |
| PRD 口径统一 | 要求把 SQLite/mock/后续接入改成第一版 PostgreSQL + 真实接入 | 统一为 PostgreSQL、pgvector、ArtifactStore 摘要引用、真实 gateway 主链路 | 能把模糊原型收敛成可验收方案 |
| 飞书调研 | 要求上网查飞书官方文档，确认卡片回调、ACK、graph 怎么跑 | 明确 ACK 负责 3 秒内响应和落库，graph 通过 outbox 异步执行 | 能识别外部平台约束并改造架构 |
| Agent 架构反思 | 质疑自由 agent-to-agent 会导致 token 浪费和理解偏差 | 改成统一 `CanonicalTaskBrief` 和结构化 artifact/ref 交接 | 能从成本、一致性和可靠性角度治理 Agent |
| 最小上下文 | 要求检查不要每轮 loop 都塞历史 | `ContextPack` 只注入当前节点需要的 refs、摘要、source 和预算 | 有 prompt 成本意识和上下文工程能力 |
| 长期记忆升级 | 要求第一版一步到位做复杂向量记忆 | 设计 PostgreSQL + pgvector + `MemoryGateway` + retention policy | 能设计企业 AI 记忆治理，而不是简单 RAG |
| Discord First 重构 | 从暂停 Discord 到决定接入 Discord，并要求普通人能直接用 | 第一版入口改为 slash command、button、modal、War Room thread、double check | 能围绕用户入口和产品体验重构方案 |
| Double Check | 要求 Discord 结构化任务给用户 double check | `TaskIntakeParser` 输出字段 source/confidence，用户确认后冻结版本 | 能把 AI 不确定性变成人工可控流程 |
| ReviewGate | 要求运行环节中增加审查者，并且分角色单任务审查 | ReviewGate 改为 artifact-level quality gate，条件路由 pass/fix/human | 能把质量控制嵌入 workflow，而不是事后聊天式复核 |
| 部署容器化 | 要求服务器直接 `docker compose up -d --build` | 补齐 API、worker、Postgres/pgvector、Caddy、entrypoint 和 `.env.example` | 具备从方案到部署交付的闭环意识 |
| 全项目逻辑审查 | 要求调用多角色审查、fix 到无 blocker/high | 修复内部 API 暴露、签名可选、readiness 误判、scope 隔离和幂等冲突 | 能用安全、DevOps、数据库、架构多视角做质量门 |
| GitHub/简历包装 | 要求 README 做成产品介绍，并把项目写进简历 | 输出产品首页、快速开始、项目经历、技术复盘和面试讲法 | 能把技术项目转化成对外可理解的解决方案 |

## 你主导提出的问题与处理结果

这部分可以在面试里用来证明：你不是照着教程做 demo，而是在不断发现问题、修正方案。

| 你提出的问题 | 背后的担心 | 最终处理 |
| --- | --- | --- |
| Agent 是否需要自由互聊？ | 每个 Agent 都重新理解任务，容易产生不同解释并浪费 token | 取消自由 agent-to-agent，改为统一事实源和结构化 artifact/ref 交接 |
| 下游 Agent 为什么还要重新分析简历和任务意图？ | 重复解析会放大歧义和成本 | 由 `TaskIntakeParser` 先生成结构化 JSON，下游只读冻结版字段 |
| Graph 和 ACK 有什么区别？ | 长任务如果卡在平台回调里，会超时或吞事件 | ACK 只做验签、落库、defer；LangGraph graph 由 outbox worker 异步执行 |
| 每次 loop 是否会塞全量历史？ | prompt 成本高，且历史信息可能污染当前判断 | `AgentHarness.build_context_pack` 只传当前节点必要 refs 和 top-k memory refs |
| 第一版能不能直接做复杂向量记忆？ | 简单记忆后续补会影响架构底座 | 第一版直接规划 PostgreSQL + pgvector，配审批、scope、过期和审计 |
| 普通人怎么直接使用？ | 不能要求用户懂 API 或手写 JSON | 使用 Discord slash command、确认卡、按钮、modal 和 War Room thread |
| 怎么避免结构化任务解析错？ | AI 把猜测当事实会导致后续全错 | 关键字段必须有 `field_source` 和 `confidence`，无来源只能进 assumptions，用户 approve 后冻结版本 |
| 审查者应该怎么做？ | 全局聊天式审查太松，发现问题也难定位 | 每个关键 artifact 后接 `ReviewGate`，只修对应 repair_node，最多一次自动修复 |
| 服务器怎么部署？ | 只在本地跑不算解决方案交付 | Docker Compose 集成 API、worker、Postgres/pgvector、Caddy，形成一键启动路径 |
| 怎么防止逻辑漏洞？ | AI workflow 容易在权限、签名、幂等、记忆隔离上出错 | 多角色审查 + blocker/high fix loop，并补安全、数据库、部署和文档校验 |

## 技术难点与解决方案

### 1. 多 Agent 理解不一致

卡点：如果每个 Agent 都读原始任务、简历和历史，它们会各自生成一套理解。三省六部越复杂，token 越高，意见越不稳定。

解决：把任务理解前置成一次 `TaskIntakeParser`，输出 `CanonicalTaskBrief / RequisitionBrief / CandidateEvidencePack`。用户 double check approve 后冻结版本，下游 Agent 只读冻结版结构化字段、ArtifactRef、MemoryRef、SOPRef 和 SourceRef。

面试说法：

> 我把多 Agent 协作从“自由聊天”改成“统一事实源 + 结构化交接”，这样减少重复理解，也降低 token 和结论漂移。

### 2. token 膨胀和上下文污染

卡点：如果每轮把完整聊天历史、完整 state、全量 artifacts、全量记忆塞进 prompt，系统会变慢、变贵，并且更容易被旧信息误导。

解决：`ContextPack` 成为最小上下文容器，只放当前节点需要的 task brief、artifact refs、memory refs、source refs、SOP refs 和预算信息。长期记忆通过 `MemoryGateway` 检索 top-k 后注入摘要和引用，默认不返回全文。

面试说法：

> 我没有把“记忆”理解成全量塞上下文，而是做检索式注入，让 Agent 只拿当前任务真正需要的少量引用。

### 3. 长期记忆污染当前判断

卡点：长期记忆如果未经审批就进入检索池，会把历史偏见、错误结论、过期信息带进新任务。

解决：长期记忆必须审批后才 `active`，并按 `tenant_id / guild_id / user_id / project_id / requisition_id / candidate_id` 做 scope filter。RunMemory 默认短周期，长期记忆默认 90 天，重要记忆才可人工续期或 permanent。

面试说法：

> 我把长期记忆当成企业知识库治理问题，而不是简单向量库。重点是权限范围、状态流转、过期机制和检索审计。

### 4. 实时事实和历史记忆边界

卡点：公司融资、裁员、在职状态、新闻变化等事实不能靠旧记忆判断，否则会得出过期结论。

解决：公司背调和当前事实必须来自 `SearchGateway source_refs`；长期记忆只提供 taxonomy、项目规则、用户修正和 SOP。简历关键词以当前简历/JD artifact 为主。

面试说法：

> 我区分了“当前事实”和“长期经验”。当前事实走搜索源，长期记忆只做辅助规则和经验，不替代实时证据。

### 5. 回调 ACK 和长任务执行冲突

卡点：Discord/飞书这类平台对回调响应有时间要求，如果在回调里直接跑 graph，容易超时、重复或吞事件。

解决：ACK 只负责验签、幂等落库和 defer，业务工作流由 durable outbox 异步触发 LangGraph。这样平台交互和 AI 长任务解耦。

面试说法：

> 我把平台回调入口设计成轻量 ACK 层，真正的 AI workflow 通过 outbox 异步执行，避免外部平台超时约束影响业务流程。

### 6. 外部副作用和人工审批

卡点：AI 生成报告、推荐结论或外部触达如果自动执行，会有误推荐、误触达、业务越权风险。

解决：业务子图只产草稿 artifact；`ActionGate` 创建 `ActionProposal`；LangGraph `interrupt()` 等待 `HumanApproval`；审批后由 `ActionExecutor` 排队 outbox。reject 不写业务表，edit 必须带修改 payload。

面试说法：

> 我把高风险动作设计成“AI 提案、人工确认、系统执行”，既保留 AI 效率，又保留业务控制权。

### 7. LangGraph state 膨胀和重复追加

卡点：子图如果把完整 reducer state 原样返回父图，会造成 `department_opinions`、`artifacts`、`node_history` 重复膨胀。

解决：子图只返回 delta，LangGraph state 只保存摘要和 refs，完整内容放 ArtifactStore。这样既能审计，又不会让 state 变成大杂烩。

面试说法：

> 我在 graph 设计里控制 state 边界，避免子图重复返回全量 state，让工作流可追踪但不膨胀。

### 8. 幂等和重复提交

卡点：用户重复点击、平台重试、worker 崩溃恢复都可能导致重复 graph run、重复 resume 或重复写业务表。

解决：用 `idempotency_key`、payload hash、thread/source_ref 冲突检测、outbox claim lease 和 ActionProposal 复用来防重复。相同 key 不同 payload 返回冲突，而不是静默覆盖。

面试说法：

> 我把幂等当成工作流系统的核心问题处理，不只是在接口层防重复，而是贯穿 outbox、审批和业务副作用。

### 9. 部署一致性和安全入口

卡点：本地能跑不代表服务器能部署；内部 API 如果暴露到公网也会有安全风险。

解决：Docker Compose 统一 API、worker、Postgres/pgvector、Caddy；数据库 URL 从 Compose 环境派生；Caddy 默认只公开健康检查和回调入口；内部控制面加 `INTERNAL_ADMIN_API_KEY`；生产关闭 docs/openapi。

面试说法：

> 我把部署也纳入方案设计，不只写业务代码，还考虑服务器启动、HTTPS 入口、内部 API 保护和配置一致性。

### 10. 假成功和未验证边界

卡点：配置齐全不代表真实联调成功；如果文档把未实现的 Discord/Feishu 能力写成已通过，会误导后续开发和使用。

解决：readiness 区分 required、warning 和未验证；Feishu/Bitable 改成 deferred adapter；Discord 主链路未实现前明确标注未验证；测试只证明本地契约，不夸大成真实生产联调。

面试说法：

> 我特别注意“不伪完成”。能本地验证的写已验证，真实平台未联调的就明确写未验证，这对解决方案交付很重要。

## 技术含量怎么讲

面试时不要只说“用了 LangGraph、pgvector、Docker”。建议讲成 4 层能力。

### 1. 业务架构能力

你不是从技术出发，而是先拆猎头工作流：

```text
岗位需求 -> 结构化确认 -> 会审 -> 人才地图 -> 候选人评估 -> 触达/报告 -> 审批 -> 复盘记忆
```

这对应 JD 里的“业务架构、客户需求分析、个性化解决方案设计”。

### 2. 应用架构能力

你设计的是完整系统，而不是单个脚本：

```text
Discord 工作台
-> FastAPI API
-> LangGraph 工作流
-> AgentHarness
-> ArtifactStore
-> MemoryGateway
-> PostgreSQL / pgvector
-> Docker Compose 部署
```

这对应 JD 里的“应用架构及系统解决方案设计”。

### 3. AI 工程治理能力

项目里最有技术含量的点是“可控 AI 工作流”：

- Agent 不自由互聊，统一使用结构化上下文。
- 不把全历史塞进 prompt，只检索必要 memory refs。
- 关键产物通过 ReviewGate 审查。
- 有外部副作用的动作必须人工批准。
- 长期记忆有 scope、审批、过期和审计。

这比“调一个大模型 API”更像真实企业 AI 应用落地。

### 4. 方案表达能力

你写了 README、PRD、接口文档、数据库设计、部署手册、Discord 接入手册。这一点要强调，因为 JD 明确要求“文档编写、方案交流、宣讲、赋能合作伙伴”。

## 和 JD 的映射表

| JD 能力 | 项目里对应的证据 | 面试表达 |
| --- | --- | --- |
| 业务架构设计 | 猎头流程拆成岗位、候选人、触达、审批、复盘 | 我先抽象业务流程，再设计 AI 节点 |
| 应用架构设计 | FastAPI、LangGraph、PostgreSQL、pgvector、Docker Compose | 我做的是可部署系统，不是 prompt demo |
| 售前方案能力 | PRD、接口文档、数据库设计、操作手册、README | 我能把技术方案讲成客户能理解的交付方案 |
| AI 工具提效 | 多 Agent 会审、长期记忆、ReviewGate | 我用 AI 提升专业服务流程效率，同时保留人工控制 |
| 行业研究与迁移 | 从猎头迁移到供应链的结构化流程方法 | 订单、库存、仓配、异常也能套用同类方案框架 |
| 风险意识 | 人工审批、最小上下文、记忆 scope、审计 | 我考虑了企业落地里的越权、误触达和数据污染 |

## 面试讲法

### 如果问：这个项目和我们岗位有什么关系？

可以答：

> 我把这个项目当成一个 AI 解决方案练习来做，不只是写功能，而是从业务流程、系统架构、AI Agent 治理、数据记忆、部署运维和使用手册完整设计。虽然场景是猎头，但方法可以迁移到供应链、物流、售前方案等复杂业务：先把业务流程结构化，再用 AI Agent 提效，用数据库和审批机制保证可追溯、可控制、可落地。

### 如果问：技术难点是什么？

可以答：

> 最大难点不是接模型，而是让多 Agent 在复杂流程里保持一致性和可控性。我做了三件事：第一，用结构化任务和 ArtifactRef 代替自由聊天；第二，用 pgvector 记忆检索代替全量历史注入；第三，用 ReviewGate 和人工审批控制高风险输出。这样既能提升效率，又能减少 token 消耗和错误传播。

### 如果问：和京东智能供应链有什么关联？

可以答：

> 京东智能供应链本质上也是把复杂业务流程数字化、智能化、协同化。我这个项目虽然不是供应链场景，但底层方法相似：业务流程建模、系统架构设计、AI 辅助决策、数据记忆、审批闭环和可部署工程化。后续如果迁移到供应链场景，可以把猎头的“岗位/候选人/报告”替换成“订单/库存/仓配/补货/履约异常”，框架仍然适用。

### 如果问：你在项目里扮演什么角色？

可以答：

> 我同时承担产品方案和工程原型角色：先把业务流程和风险边界拆清楚，再写 PRD、接口、数据库和部署文档，最后把后端骨架、Docker Compose、网关、记忆、审批和审计能力逐步落到代码里。这个过程训练的是解决方案岗位需要的“需求理解、方案表达、系统拆解和 AI 落地”能力。

## 面试复盘讲法

### 1 分钟技术流讲法

> 这个项目是一个 AI 猎头作战系统原型，但我更看重的是它背后的复杂业务流程 AI 化方法。我先把猎头流程拆成岗位校准、人才地图、候选人判断、触达草稿、人工审批和复盘记忆，再用 FastAPI、LangGraph、PostgreSQL/pgvector 和 Docker Compose 设计工程底座。项目中最大的技术取舍是多 Agent 治理：我没有让 Agent 自由互聊，而是用冻结版 CanonicalTaskBrief、ArtifactRef、MemoryRef 和 SOPRef 做结构化交接，同时用 ReviewGate、interrupt 和 HumanApproval 控制质量和副作用。最后还补了 Docker 部署、readiness、admin key、outbox 幂等和文档手册，让它更像一个可交付的 AI 解决方案原型。

### 3 分钟深挖讲法

> 这个项目一开始是围绕猎头业务做 AI 工作流，但做着做着我发现难点不是“让模型回答问题”，而是“让 AI 在复杂流程里稳定、可控、可追溯”。我先做了 PRD 拆解，把业务分成岗位理解、会审、人才地图、候选人证据、触达草稿、审批和复盘。  
>
> 第一类难点是多 Agent 协作。自由 agent-to-agent 会让每个 Agent 重复理解任务，既浪费 token，又容易产生不同解释。所以我把输入统一成 CanonicalTaskBrief，并要求用户 double check 后冻结，下游 Agent 只消费结构化字段和引用。  
>
> 第二类难点是记忆和上下文。长期记忆不能全量塞 prompt，也不能让旧信息污染新判断，所以我设计 PostgreSQL + pgvector 的 MemoryGateway，按 scope、状态、PII、过期时间和 token budget 检索，默认只返回 MemoryRef。  
>
> 第三类难点是工作流可靠性。平台回调要快速 ACK，AI graph 是长任务，所以我用 outbox 解耦；高风险动作必须通过 ActionProposal、interrupt、HumanApproval 再执行；重复提交用 idempotency_key 和 payload hash 兜住。  
>
> 第四类难点是交付。为了让别人能部署和理解，我补了 Docker Compose、Caddy、配置检查、README、操作手册、接口文档和数据库设计。这个项目训练的是从需求分析、方案设计到工程落地的完整能力，而不只是写一个 prompt demo。

### 如果问：最大卡点是什么？

可以答：

> 最大卡点是多 Agent 系统的可控性。刚开始容易直觉上想堆更多 Agent，但后来发现 Agent 越多，重复理解、上下文膨胀、记忆污染和副作用风险越明显。所以我把架构收敛成统一结构化输入、最小上下文、artifact-level ReviewGate 和人工审批。这个转变让我意识到，企业 AI 应用的核心不是 Agent 数量，而是上下文、证据、权限和执行链路的治理。

### 如果问：为什么从飞书切到 Discord？

可以答：

> 我先研究了飞书回调、卡片和 Bitable，发现它很适合企业协同，但第一版如果把飞书和 Bitable 都作为主链路，会让接入复杂度和权限面变大。后来我把飞书/Bitable 降级为 deferred adapter，把 Discord 作为第一版工作台，因为 slash command、button、modal 和 thread 更适合快速做任务输入、确认和 War Room 交互。这个决策本质上是产品入口和工程复杂度之间的取舍。

### 如果问：这个项目和供应链解决方案有什么迁移关系？

可以答：

> 猎头场景里的岗位、候选人、触达和复盘，可以迁移成供应链里的订单、库存、仓配、履约异常和复盘。底层方法是一样的：先把业务流程结构化，再设计 AI 辅助判断和人工审批，最后用数据、记忆、审计和部署机制保证可落地。比如人才地图可以类比供应链网络分析，候选人证据包可以类比订单/库存证据包，ReviewGate 可以类比异常处理前的质量门。

### 如果问：你体现了什么产品/解决方案能力？

可以答：

> 我体现的是把模糊需求变成可执行方案的能力。这个项目里我不断提出关键问题：用户怎么确认 AI 理解没错，Agent 怎么少花 token，记忆怎么不过期污染，外部动作怎么审批，服务器怎么部署，文档怎么让别人照着用。每个问题最后都落到了 PRD、工程文档、接口、数据库、测试或部署手册里。

## 建议放在简历的位置

放在“项目经历”里，不建议只放在“技能”里。

推荐标题：

```text
AI 猎头作战系统｜AI Agent 业务流程解决方案
```

推荐标签：

```text
FastAPI / LangGraph / PostgreSQL / pgvector / Docker / Discord Bot / RAG / Multi-Agent / Solution Architecture
```

## 注意别这样写

不要写：

- “已经面向客户大规模交付”
- “已经完成生产环境正式落地”
- “可以完全替代招聘顾问”
- “精通京东供应链全业务”

更稳的写法是：

- “个人项目 / 原型系统 / 解决方案原型”
- “验证复杂业务流程 AI 化的方案设计能力”
- “可迁移到供应链、物流、售前方案等专业服务场景”
- “强调业务架构、应用架构、AI 治理和文档表达能力”

## 30 秒口播稿

> 我做过一个 AI 猎头作战系统原型，核心不是做聊天机器人，而是把猎头的岗位理解、候选人判断、人才地图、触达草稿、人工审批和复盘记忆拆成可审计的 AI 工作流。架构上用 FastAPI、LangGraph、PostgreSQL/pgvector 和 Docker Compose，设计了多 Agent 会审、结构化任务确认、ReviewGate 审查、长期记忆和人工审批机制。这个项目最能体现我对 AI 应用解决方案的理解：先把业务流程结构化，再用 AI 提效，并通过数据、审批和文档保证可落地、可讲清楚、可持续优化。
