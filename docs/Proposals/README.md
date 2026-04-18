# Feature Proposals

这个目录维护 OpenAgent 中计划中、待讨论、待实现的 feature proposal。

## What Belongs Here

- 用户提出、但还未形成已交付能力的 feature
- 需要跨模块设计的能力增强
- 需要明确状态、取舍和接口边界的方案文档
- 从历史 `todo.md` backlog 审计后确认“仍未落地”的能力缺口

## What Does Not Belong Here

- 已经落地的能力边界
  - 这些应写入 `docs/Features/`
- 纯工程待办
  - 这些只保留在本地未跟踪 `todo.md`

## Status Values

每篇 proposal 建议在文档头部明确当前状态：

- `proposed`
- `in-discussion`
- `planned`
- `in-progress`
- `completed`
- `rejected`

## Lifecycle

- proposal 在讨论和设计阶段维护在 `docs/Proposals/`
- feature 落地后，应将最终能力说明写入 `docs/Features/`
- proposal 可删除、归档，或在头部标记为 `completed`

## Audit Rule

从 backlog 迁移 proposal 前，必须先核对：

- 当前代码实现
- 当前测试覆盖
- 当前 `docs/Features/`

已经落地的能力不要重新写回 proposal；只有仍未落地或仅部分落地的剩余缺口才进入这里。

## Current Proposals

- [Harness Runtime Follow-ups](./harness-runtime-followups.md)
- [Context Governance Enhancements](./context-governance-enhancements.md)
- [Session And Memory Enhancements](./session-and-memory-enhancements.md)
- [Tools And Web Backends Follow-ups](./tools-and-web-backends-followups.md)
- [MCP Runtime Expansion](./mcp-runtime-expansion.md)
- [Sandbox Contract Expansion](./sandbox-contract-expansion.md)
- [Conformance Expansion](./conformance-expansion.md)
- [Feishu Cards And Streaming Replies](./feishu-cards-and-streaming-replies.md)
