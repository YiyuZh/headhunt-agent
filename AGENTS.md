# AGENTS.md

本项目的规范工作根目录以当前 clone 的仓库根目录为准，文档中统一写作：

```text
<repo-root>
```

已知协作 clone 路径：

- Windows 当前目录：`D:\apps\headhunt-agent`
- 历史 macOS/Linux 目录：`/Users/w/Documents/lietou`

## 必读顺序

每次修改本项目之前，先读：

1. `任务记忆文档.md`
2. `项目文件索引.md`
3. `项目文件索引.json`
4. 当前任务涉及的 PRD、代码或测试文件

## 协作规则

- 默认基于现有 PRD 和项目结构增量推进，不要无依据重写体系。
- 重要改动后必须更新 `任务记忆文档.md`。
- 新增、移动、删除关键文件后必须更新 `项目文件索引.md` 和 `项目文件索引.json`。
- 没有验证的事项必须明确写“未验证”，不能写成已通过。
- 当前项目已有工程骨架和多阶段本地测试；继续写代码前必须先读最新 PRD 和工程文档。第一版主链路直接使用 PostgreSQL / pgvector / 真实飞书事件回调、飞书交互卡片、飞书 War Room 群消息和 Bitable 同步；Discord 只作为 optional adapter；mock / fake 只用于测试、CI 和失败隔离。

## 当前架构方向

```text
FastAPI + LangGraph
policy-engine + AgentHarness + ArtifactStore
SearchGateway / DatabaseGateway / MemoryGateway / ActionGateway / ChannelGateway
飞书 War Room
TaskIntakeParser + 用户 double check
ReviewGate + AgentSOPRegistry
人工确认 interrupt/resume
AgentRuns 审计
```

Agent 不能直接互相调用，不能直接访问公网、数据库、飞书、Discord optional adapter 或模型 Key。所有访问必须经过授权 Gateway、Registry 和 policy。下游 Agent 默认不得重新解析原始任务或原始简历，只能消费已确认的结构化 JSON、ArtifactRef、MemoryRef、SOPRef 和 SourceRef。
