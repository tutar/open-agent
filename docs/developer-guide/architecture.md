# Architecture

这份文档描述当前 `openagent` 的本地架构边界和主流程。

## Design Constraints

当前实现遵循这些约束：

- 只做本地 `terminal` 主路径
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
  - single active harness lease
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
- `gateway`
  - harness-owned frontend integration boundary
  - channel bindings and harness-instance management
- `observability`
  - session signal
  - progress projection
  - span-based tracing

## Main Runtime Flow

主运行链路是：

`frontend -> bridge -> gateway -> session adapter -> harness instance -> agent runtime -> tools/session`

更具体地说：

1. terminal TUI 采集用户输入
2. bridge 把输入转成 JSON lines 协议
3. `Gateway` 做 input normalization、chat-to-session binding、egress projection，并管理当前 `HarnessInstance`
4. `InProcessSessionAdapter` 为 session 获取 single active harness lease，并调用本地 runtime
5. `SimpleHarness` 作为 harness facade，把 turn 交给显式的 `RalphLoop`
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
- observability：debug/runtime signal projection

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
- active harness lease baseline
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
- `SimpleStreamingToolExecutor`
- richer `ToolDefinition` metadata and provenance
- `allow / deny / ask / passthrough`
- optional policy engine override seam
- rule-based policy engine baseline
- builtin tool baseline injected by default in local runtimes
- command / review / skill / MCP capability bridges in the same tools domain

基础事件包括：

- `tool_started`
- `tool_progress`
- `tool_result`
- `tool_failed`
- `tool_cancelled`
- `requires_action`

并发上，executor 对 concurrency-safe tool 提供基础并发执行语义。
tool 事件由 executor 单点发出，harness 负责持久化这些事件，并把失败或取消折叠成 turn 终态。

默认 runtime 装配会注入 builtin tools：

- `Read / Write / Edit / Glob / Grep / Bash`
- `WebFetch / WebSearch`
- `AskUserQuestion`

host 默认 demo tools 只是在此基础上额外叠加，不会覆盖 builtin baseline。

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

## Agent Observability

当前 runtime 还带有独立 observability 平面，而不是把所有调试信息塞进 `RuntimeEvent`。

当前已接入：

- interaction span
- llm request span
- tool span
- background task span
- session lifecycle signal
- task / background progress projection
- local stdout sink baseline

这层的职责是“让开发者和 host 看见发生了什么”，而不是承担 replay 或 durability。

## Session And Memory Boundaries

当前 session 负责 transcript、working state、short-term continuity 和 single active harness lease。

当前 `ShortTermSessionMemory` 由 session 域维护，并在安全点异步更新：

- turn completed
- turn failed
- requires_action before persistence
- approval continuation completion

当前 durable memory 不直接改写 transcript，也不承担 restore。

如果 runtime 配置了 memory store，`SimpleHarness.build_model_input(...)` 会在 context assembly
阶段把 recalled memories 放进 `ModelTurnRequest.memory_context`。这满足了：

- `1 Chat = 1 Session = 1 Short-Term Memory`
- `1 Agent = 1 Global Long-Term Memory`
- recall 进入 context plane
- transcript 仍保持原始消息边界
- short-term continuity 进入 `short_term_memory`
- consolidation 更新 durable memory，而不是覆盖历史 session

当前 `AGENTS.md` file-backed memory injection 也走同一个 context plane：

- `~/.openagent/AGENTS.md`
- `<workdir>/AGENTS.md`
- `<target subtree>/AGENTS.md`

后加载者优先级更高，但不会伪装成 transcript message。

## Capability Surface

capability surface 当前统一投影三类能力：

- tools
- commands
- skills

支持：

- origin metadata
- model/user visibility
- host projection
- skill scope / trust / diagnostics metadata
- catalog disclosure vs activation disclosure separation

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

## Local Assembly

当前不再保留 `profile` 抽象。OpenAgent 直接暴露本地装配 helper：

- `create_in_memory_runtime(...)`
- `create_file_runtime(...)`
- `create_gateway_for_runtime(...)`

这样 frontend/host 可以直接按部署形态装配 runtime 和 gateway，而不是再包一层 profile 概念。

## What Is Intentionally Missing

当前架构里有意没有做这些内容：

- cloud orchestration
- remote binding
- daemon / IPC transport
- model token streaming
- full cancellation / retry / timeout semantics

这些都在未来待办里，但不是当前架构的前提。
