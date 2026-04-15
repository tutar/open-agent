# Architecture

这份文档描述当前 `openagent` Python SDK 的本地架构边界和主流程。

## Design Constraints

当前实现遵循这些约束：

- 只做本地 `TUI` 主路径
- 不做 `Cloud`
- 不引入 remote binding
- 不为了未来扩展提前引入 IPC / daemon 分层
- 默认使用同进程直接函数调用

这意味着当前优化目标是：

- 清晰的模块边界
- 低复杂度
- 低调用开销
- 可测试

## Module Layout

核心模块：

- `object_model`
  - canonical objects
  - runtime events
  - terminal state
  - task record
- `harness`
  - `SimpleHarness` facade
  - `RalphLoop` turn runtime
  - model input/output adaptation
  - provider adapters under `harness/providers`
- `session`
  - session record
  - event log
  - checkpoint
  - replay
  - short-term session memory
  - `session.memory` scoped durable memory recall and consolidation baseline
- `tools`
  - tool registry
  - tool executor
  - commands / skills / MCP baseline
- `sandbox`
  - local execution boundary and capability negotiation baseline
- `orchestration`
  - local task manager
  - background / verifier task baseline
- `profiles`
  - host assembly points
- `gateway`
  - frontend integration boundary

## Main Runtime Flow

主运行链路是：

`frontend -> bridge -> gateway -> session adapter -> harness -> tools/session`

更具体地说：

1. terminal TUI 采集用户输入
2. bridge 把输入转成 JSON lines 协议
3. `Gateway` 做 input normalization、session binding 和 egress projection
4. `InProcessSessionAdapter` 调用本地 runtime
5. `SimpleHarness` 把 turn 交给显式的 `RalphLoop`
6. `RalphLoop` 通过注入的 `ModelProviderAdapter` 调用 provider adapter 或 test double
7. tool execution、session event log、approval 状态在 runtime 内完成
8. runtime events 再通过 gateway 投影回 frontend

## Frontend Boundary

frontend 当前不直接调用 harness 或 session store。

terminal TUI 当前使用：

- `React`
- `Ink`
- `Yoga`
- stdio bridge

边界职责：

- frontend：渲染与输入
- bridge：协议转换
- gateway：channel / session / egress projection
- harness：turn execution

## Session And Event Model

当前 session 子系统提供：

- `InMemorySessionStore`
- `FileSessionStore`
- `InMemoryShortTermMemoryStore`
- `FileShortTermMemoryStore`
- append-only event log baseline
- checkpoint baseline
- cursor baseline
- restore marker baseline
- resume snapshot baseline
- replay baseline
- short-term continuity summary baseline

gateway 还支持：

- session binding
- file-backed binding persistence
- binding-level checkpoint metadata
- event filtering
- replay observation

terminal TUI 的多 session 工作流基于这套能力实现。

## Tool Execution

当前 tool 子系统采用：

- `StaticToolRegistry`
- `SimpleToolExecutor`
- `allow / deny / ask`
- optional policy engine override seam
- rule-based policy engine baseline

基础事件包括：

- `tool_started`
- `tool_progress`
- `tool_result`
- `tool_failed`
- `tool_cancelled`
- `requires_action`

并发上，executor 对 concurrency-safe tool 提供基础并发执行语义。
tool 事件由 executor 单点发出，harness 负责持久化这些事件，并把失败或取消折叠成 turn 终态。

## Model Integration

当前 harness/runtime 支持两类模型接入方式：

- `generate(...)`
- `stream_generate(...)`

真实 provider 当前放在 `harness/providers/`：

- `OpenAIChatCompletionsModelAdapter`
- `AnthropicMessagesModelAdapter`

如果模型提供 `stream_generate(...)`，harness 会先产出 `assistant_delta` 事件，再汇总成最终
`assistant_message`。

turn 级控制当前通过 `TurnControl` 暴露：

- `timeout_seconds`
- `max_retries`
- `cancellation_check`

这还是本地 baseline，不是完整分布式控制协议。

`SimpleHarness.run_turn(...)` 只是 convenience wrapper。
真正的 turn 状态机在 `RalphLoop.run_turn_stream(...)` 中推进，和 spec 中的
`AgentRuntime` 语义保持一致。

## Context Governance

当前 `ContextGovernance` 已经不是单纯的 compact helper。

它在架构上负责：

- token/budget 分析
- continuation budget planning
- warning threshold
- proactive compact
- overflow recovery
- tool result externalization
- prompt-cache-aware message shaping baseline
- prompt-cache break detection baseline

harness 在 `build_model_input(...)` 阶段会调用它，并把最近一次治理结果保存在
`last_context_report`，供测试和 host 层观察。

## Session And Memory Boundaries

当前 session 负责 transcript、working state 和 short-term continuity。

当前 `ShortTermSessionMemory` 由 session 域维护，并在安全点异步更新：

- turn completed
- turn failed
- requires_action before persistence
- approval continuation completion

当前 durable memory 不直接改写 transcript，也不承担 restore。

如果 runtime 配置了 memory store，`SimpleHarness.build_model_input(...)` 会在 context assembly
阶段把 recalled memories 放进 `ModelTurnRequest.memory_context`。这满足了：

- recall 进入 context plane
- transcript 仍保持原始消息边界
- short-term continuity 进入 `short_term_memory`
- consolidation 更新 durable memory，而不是覆盖历史 session

## Capability Surface

capability surface 当前统一投影三类能力：

- tools
- commands
- skills

支持：

- origin metadata
- model/user visibility
- host projection

这使 frontend 或 host 侧可以做更稳定的能力展示和筛选。

## Orchestration

当前 orchestration 只覆盖本地 baseline：

- generic task
- background task
- verifier task
- detached local background agent execution
- checkpoint / complete / fail
- file-backed task persistence and recovery

它现在不是 durable distributed orchestration，而是本地任务生命周期抽象。

## Profiles

当前 profile 主要用于装配，而不是承载业务语义。

- `TuiProfile`
  - 装配本地 runtime
  - 装配 gateway
- `DesktopProfile`
  - 目前只保留已存在的 baseline
  - 不在当前 backlog 中继续推进

## What Is Intentionally Missing

当前架构里有意没有做这些内容：

- cloud orchestration
- remote binding
- daemon / IPC transport
- model token streaming
- full cancellation / retry / timeout semantics

这些都在未来待办里，但不是当前架构的前提。
