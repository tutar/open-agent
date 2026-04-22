# Tools And Capability Surface

这一页聚合 OpenAgent 的 tools、builtin tool baseline、capability surface 和与工具体验直接相关的 Feishu channel UX。

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
- pluggable web backends for builtin web tools
  - default stdlib URL fetch backend
  - default placeholder search backend
  - optional Firecrawl-backed scrape/search backend
  - optional Tavily-backed `WebSearch`
  - optional Brave Search-backed `WebSearch`
  - builtin web backend environment variables can be read from the process
    environment or a project-root `.env` file
  - GitHub blob URL normalization for Firecrawl-backed `WebFetch`
- review command baseline via `CommandKind.REVIEW`
- tool provenance / visibility metadata
- MCP tool / prompt / skill adaptation seam
- runtime 默认注入 builtin tool baseline；host 默认 demo tools 只是额外叠加
- builtin file / shell tools 默认作用于当前工作目录，或显式 `OPENAGENT_WORKSPACE_ROOT`
- externalized tool results are exposed back to the model as internal references with previews, not raw workspace file paths

### 当前不支持

- 更细的 tool retry / recovery policy
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

## Feishu Channel UX

当前 Feishu channel 已支持基础的消息状态反馈：

- 私聊收到正常消息后，会先对原消息加“处理中” reaction（`emoji_type=OneSecond`）
- 群聊被 `@openagent` 命中后，也会先对原消息加“处理中” reaction（`emoji_type=OneSecond`）
- 当本次消息处理完成后，会移除开始态 reaction，并补一个“完成” reaction（`emoji_type=DONE`）
- 这套 reaction 状态不进入 transcript，不进入 session memory，也不写入 model context
- 当模型 provider 启用 streaming 时，reply card 也会消费 `assistant_delta` 并增量更新正文
