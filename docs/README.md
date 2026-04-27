# Docs

- [Get Started](./get-started.md)
- [Features Directory](./Features/README.md)
- [Feature Proposals](./Proposals/README.md)
- [Developer Guide](./developer-guide/README.md)
- [Contributing](./developer-guide/contributing.md)
- [Architecture](./developer-guide/architecture.md)
- [Internals](./developer-guide/internals/README.md)

这几份文档分别面向不同目标：

- `Get Started`：第一次把 OpenAgent 和 terminal TUI 跑起来
- `Features Directory`：按 feature 拆分后的已实现能力文档目录
- `Feature Proposals`：计划中、待讨论、待实现 feature 的 proposal 目录
- `Developer Guide`：开发文档入口，聚合贡献方式和系统架构
- `Contributing`：本地开发、提交流程和合入前检查
- `Architecture`：运行时主链路、模块边界和前后端集成方式
- `Internals`：逐模块解释内部实现原理和当前取舍

## Feature Reading Guide

- 想快速看运行主链路：先读 [`Runtime`](./Features/runtime.md)、[`Providers`](./Features/providers.md)、[`Gateway And Channels`](./Features/gateway-and-channels.md)
- 想看 agent 的可调用能力：读 [`Tools And Capability Surface`](./Features/tools-and-capability-surface.md)
- 想看会话、记忆和上下文：读 [`Session`](./Features/session.md)、[`Durable Memory`](./Features/durable-memory.md)、[`Context Engineering`](./Features/context-engineering.md)
- 想看 delegated worker、本地多 agent、task 通知和 viewed transcript：读 [`Multi-Agent`](./Features/multi-agent.md)
- 想看 Skills / MCP / Commands 兼容层：读 [`Ecosystem Compatibility`](./Features/ecosystem-compatibility.md)
- 想看排障和数据沉淀：读 [`Observability And Model I/O`](./Features/observability-and-model-io.md)

## Feature Status Model

- `docs/Features/` 只维护当前已经落地、可交付的能力边界
- `docs/Proposals/` 维护计划中、待讨论、待实现的 feature 方案
- `todo.md` 只是本地未跟踪临时文件，不再作为仓库协作文档
