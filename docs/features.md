# Features

这份文档描述当前 `openagent` 已经落地的能力边界，不讨论未来规划。

## Runtime

当前 runtime 是本地、同进程、低复杂度实现。

已支持：

- `SimpleHarness` facade over an explicit `RalphLoop` turn runtime
- `run_turn_stream(...)`
- `turn_started`
- `assistant_delta`
- `assistant_message`
- `tool_started`
- `tool_progress`
- `tool_failed`
- `tool_cancelled`
- `tool_result`
- `requires_action`
- `turn_completed`
- `turn_failed`
- 审批后的 `continue_turn(...)`
- single-shot `generate(...)`
- streaming `stream_generate(...)`
- turn-level cancellation baseline
- timeout baseline
- retry baseline

当前不支持：

- model token streaming
- tool-aware cancellation recovery
- full retry policy customization
- full timeout semantics for every streaming backend

## Providers

当前已经有真实 LLM provider adapter：

- `OpenAIChatCompletionsModelAdapter`
- `AnthropicMessagesModelAdapter`
- `load_model_from_env()`
- stdlib-based `UrllibHttpTransport`

这些 provider adapter 当前归属 `harness/providers`，而不是 agent 根目录。

当前 terminal bridge 会在设置 `OPENAGENT_MODEL` 时自动尝试加载真实 provider。

当前支持：

- `OPENAGENT_PROVIDER=openai`
- `OPENAGENT_PROVIDER=anthropic`
- 自定义 `OPENAGENT_BASE_URL`
- provider-level tool definition projection
- provider response -> `ToolCall` 解析 baseline

当前不支持：

- provider-native streaming SSE
- provider-specific advanced options 全量映射

## Session

当前 session 子系统支持本地内存和文件两种存储。

已支持：

- `InMemorySessionStore`
- `FileSessionStore`
- `InMemoryShortTermMemoryStore`
- `FileShortTermMemoryStore`
- append-only event log baseline
- session checkpoint baseline
- session cursor baseline
- restore marker baseline
- wake / resume snapshot baseline
- event replay baseline
- approval continuation state
- single active harness lease
- short-term session memory persistence
- short-term memory safe-point update and stabilization
- terminal TUI 的多 session 切换与 replay

当前不支持：

- 分支化 event log
- 更完整的 wake / restore mode 设计
- richer short-term salience / eviction policy

## Context Governance

当前 context governance 已支持：

- token estimate baseline
- warning threshold
- continuation budget planning
- recommended output-token reservation
- proactive compact
- reactive overflow recovery
- long tool result externalization
- prompt-cache-aware shaping baseline
- provider cache key baseline
- prompt-cache stable-prefix / dynamic-suffix baseline
- prompt-cache break detection baseline
- prompt-cache fork-sharing baseline
- prompt-cache strategy-equivalence baseline
- harness-level `last_context_report`

当前不支持：

- provider-native prompt cache integration
- model-specific token accounting

## Tools

当前 tools 子系统支持：

- `StaticToolRegistry`
- `SimpleToolExecutor`
- `SimpleStreamingToolExecutor`
- richer `ToolDefinition` surface
  - aliases
  - context-aware description
  - `is_enabled / is_read_only / is_concurrency_safe`
  - permission check hook
  - result mapping
- `ToolRecord`
- `ToolExecutionHandle / ToolExecutionEvent / ToolExecutionSummary`
- `PersistedToolResultRef`
- 完整的本地 tool event stream baseline
- `tool_started / tool_progress / tool_result / tool_failed / tool_cancelled`
- per-tool permission: `allow / deny / ask / passthrough`
- `RuleBasedToolPolicyEngine`
- denial tracking and fallback-to-ask baseline
- approval continuation
- concurrency-safe tool 的并发执行 baseline
- builtin tool baseline
  - `Read / Write / Edit / Glob / Grep / Bash`
  - `WebFetch / WebSearch`
  - `AskUserQuestion`
  - optional `Agent / Skill` bridge
- review command baseline via `CommandKind.REVIEW`
- tool provenance / visibility metadata
- MCP tool / prompt / skill adaptation seam
- runtime 默认注入 builtin tool baseline；host 默认 demo tools 只是额外叠加
- builtin file / shell tools 默认作用于当前工作目录，或显式 `OPENAGENT_WORKSPACE_ROOT`

当前不支持：

- 更细的 tool retry / recovery policy
- real host-integrated search backend for `WebSearch`
- full orchestration-backed default implementation for `Agent` / review commands

## Capability Surface

当前 capability surface 支持：

- capability origin metadata baseline
- capability descriptor projection
- `list_capabilities(...)`
- `list_command_surface(...)`
- `resolve_capability(...)`
- `project_for_host(...)`
- model-visible / user-visible filtering
- host projection for `terminal` vs `feishu`

## Ecosystem Compatibility

当前已经有三类兼容层：

- Commands
- Skills
- MCP baseline

具体包括：

- `StaticCommandRegistry`
- `FileSkillRegistry`
- `SkillActivator`
- `SkillInvocationBridge`
- `SkillCatalogEntry`
- `SkillActivationResult`
- `InMemoryMcpClient`
- `TransportBackedMcpClient`
- `InMemoryMcpTransport`
- `StdioMcpTransport`
- `StreamableHttpMcpTransport`
- `McpProtocolClient`
- `McpAuthorizationAdapter`
- `McpRootsProvider`
- `McpSamplingBridge`
- `McpElicitationBridge`
- `McpToolAdapter`
- `McpPromptAdapter`
- `McpResourceAdapter`
- `McpSkillAdapter`
- MCP tool/prompt/skill conformance baseline

当前 MCP 已经拆到 `src/openagent/tools/mcp/`，并按四层组织：

- protocol client
- transport + auth
- runtime adaptation
- host extension

当前已经支持：

- `initialize -> initialized`
- protocol version / capability negotiation
- deterministic in-memory transport
- real `stdio` transport
- real `Streamable HTTP` transport with JSON / SSE parity
- auth discovery + token acquire + `WWW-Authenticate` scope upgrade
- tools/prompts/resources pagination
- roots list + `list_changed`
- resource subscribe + change notification observation
- sampling / elicitation host bridge seams

`mcp skill` 继续保留，但明确属于 host extension，不属于 MCP core。

当前 skills 还支持：

- deterministic discovery precedence across scopes
- shadow diagnostics for conflicting skills
- catalog disclosure distinct from activation disclosure
- wrapped activation result for dedupe / replay / compaction-friendly semantics

## Memory

当前 memory 子系统负责 durable memory，而不是 session continuity。

当前支持：

- `InMemoryMemoryStore`
- `FileMemoryStore`
- scoped durable memory via `user / project / agent / local`
- same-agent cross-session long-term memory recall baseline
- transcript-to-durable-memory consolidation baseline
- background consolidation job baseline
- recall into `ModelTurnRequest.memory_context`
- `AGENTS.md` file-backed context injection with home -> workdir -> subtree precedence
- restart-safe durable memory recall

当前不支持：

- cross-session dream consolidation
- richer extraction policy
- richer recall ranking and scoping

## Observability

当前 observability 子系统已经独立于 runtime event log。

当前支持：

- `AgentObservability`
- `RuntimeMetric`
- `SessionStateSignal`
- `ProgressUpdate`
- span-based tracing via `start_span(...) / end_span(...)`
- `InMemoryObservabilitySink`
- `StdoutObservabilitySink`
- `NoOpObservabilitySink`
- `CompositeObservabilitySink`
- interaction / llm request / tool / background task span baseline
- session lifecycle signal: `running / requires_action / idle`
- task / background progress projection baseline
- host-local structured stdout output for debugging

当前不支持：

- OTel exporter
- vendor-specific tracing backend integration
- standalone durable trace storage
- precise provider token/cost accounting when the provider does not expose it

## Model I/O Capture

当前 OpenAgent 默认开启 agent 级模型输入输出沉淀。

当前支持：

- file-backed model dataset capture under `.openagent/data/model-io`
- append-only `index.jsonl`
- per-call record files under `records/<session_id>/`
- assembled `ModelTurnRequest` capture
- provider payload capture
- provider raw response capture
- parsed `ModelTurnResponse` capture
- provider-exposed reasoning / thinking block capture
- non-streaming and streaming final result capture
- error-path capture for provider failure / timeout / retry exhaustion
- configurable roots via `OPENAGENT_DATA_ROOT` and `OPENAGENT_MODEL_IO_ROOT`

当前不支持：

- automatic retention cleanup
- transcript-level redaction policy
- provider-hidden reasoning recovery

## Gateway

gateway 是 harness 域下的 frontend 稳定接入边界。

当前支持：

- inbound normalization
- built-in `TerminalChannelAdapter`
- built-in `FeishuChannelAdapter`
- channel-specific default event projection
- session binding
- harness instance handle projection
- file-backed session binding persistence
- binding-level session checkpoint tracking
- user message processing
- permission continuation
- interrupt control
- resume control
- mode change control routing baseline
- projected event filtering
- session replay observation
- bound-session replay via `resume_bound_session(...)`
- command-style control projection for chat channels
- host management command baseline via `/channel` and `/channel-config`
- Feishu long-connection host baseline
- Feishu inbound idempotency baseline via `message_id` dedupe

frontend 当前应通过 `Gateway` 使用 agent，不应该直接持有 harness。

## Terminal TUI

terminal TUI 当前基于 `React + Ink + Yoga`。

已支持：

- 本地 bridge 启动
- 多 session 创建和切换
- 消息发送
- tool demo
- requires_action 审批
- `/resume` 事件回放
- 事件日志面板
- session 状态面板
- 非 TTY 环境下的安全降级

命令包括：

- `/new <name>`
- `/switch <name>`
- `/sessions`
- `/approve`
- `/reject`
- `/interrupt`
- `/session`
- `/clear`
- `/help`
- `/exit`

## Orchestration

当前 orchestration 支持本地 baseline：

- generic task
- background task handle
- verifier task handle
- local background agent orchestrator
- checkpoint
- complete
- fail
- file-backed task persistence
- restart-safe task/handle recovery

## Sandbox

当前 sandbox 是本地 allowlist baseline。

已支持：

- `LocalSandbox`
- 命令前缀 allowlist
- capability negotiation
- network / filesystem / credential deny reasons
- 本地测试场景执行

当前不支持：

- 更细的凭证边界
- 更细的网络边界
- provider-specific sandbox capability negotiation
