# Discord optional adapter 操作手册

Discord 现在不是第一版主入口，只作为后续 optional adapter。当前代码保留 `/discord/interactions` PING、`/model add/list/use/test/revoke` BYOK 和 command register 基础能力，用于历史测试或后续接入。

第一版用户接入请使用 `docs/manual/飞书接入操作手册.md`。

## 适用场景

- 后续需要把同一套 `CanonicalTaskBrief / ReviewGate / MemoryGateway / HumanApproval` 接到 Discord。
- 需要复用已有 `/discord/interactions` 验签和 `/model` BYOK 测试。
- 不影响 Feishu First 主验收。

## 保留能力

```text
POST /discord/interactions
POST /discord/commands/register
lietou-discord-register-commands
```

已覆盖：

- Ed25519 signature 校验。
- PING/PONG。
- `/model add/list/use/test/revoke` 基础流程。
- slash command schema register 基础。

未验证：

- 真实 Discord Developer Portal 保存。
- slash command 传播。
- `/headhunt` 业务处理。
- button/modal 审批。
- Discord thread/message。

## 重新启用时的硬约束

- `/discord/interactions` 只能快 ACK/defer，完整 graph 必须异步跑。
- 用户必须 double check approve 后才进入正式 graph。
- 业务副作用仍必须走 HumanApproval。
- Discord 不得绕过 Feishu First 已确定的 Gateway、ReviewGate、MemoryGateway 和 ArtifactStore 边界。
