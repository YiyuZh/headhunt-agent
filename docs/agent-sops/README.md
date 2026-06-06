# Agent SOP Registry

本目录保存第一版 AgentSOPRegistry 的版本化 SOP、workflow、checklist 和 template。

原则：

- SOP 是本项目自己的规则层，不是外部框架运行时依赖。
- AgentHarness 只能把 `SOPRef` 摘要、版本、`content_ref`、命中原因和 token 估算注入 ContextPack。
- 每个节点最多注入 1 个主 SOP 和 2 个审查 SOP。
- `manual` SOP 只能由用户、管理员或人工审批指定。
- SOP 变更必须 version bump，并写入 `SOPResolutionAudit`。

目录：

```text
registry.json
business/*.sop.md
reviewers/*.sop.md
workflows/*.workflow.md
checklists/*.checklist.md
templates/*.template.md
```

