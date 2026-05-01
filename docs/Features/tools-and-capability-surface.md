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
  - builtin 实现按 tool 独立目录维护，便于单独演进与维护
  - per-tool `prompt.py` is the source of truth for model-visible `DESCRIPTION`
  - harness bootstrap prompt only consumes root-level tool-name constants, not per-tool descriptions
- core local builtin tools now expose richer execution semantics
  - `Read` supports line-based partial reads with numbered output
  - `Edit` supports strict single-match editing plus `replace_all`
  - `Glob` supports scoped search with result limits
  - `Grep` is now ripgrep-backed, supports regex search, `content/files_with_matches/count`
    output modes, `glob` and `type` filters, context flags, pagination via
    `head_limit/offset`, and multiline search
  - `Bash` supports explicit `timeout_ms` and stronger workspace-bound behavior
  - core local tools now have a layered verification story:
    unit behavior, provider contract, harness integration, and optional live model selection eval
  - live core-tool evals are environment-gated and can reuse the current model config by setting
    `OPENAGENT_RUN_TOOL_SELECTION_EVAL=1` together with `OPENAGENT_PROVIDER`,
    `OPENAGENT_BASE_URL`, and `OPENAGENT_MODEL`
  - a runtime tool-surface eval can enumerate the actual mounted tool registry and report which
    tools are only disclosed vs which have live task scenarios
- pluggable web backends for builtin web tools
  - default stdlib URL fetch backend
  - default placeholder search backend
  - optional Firecrawl-backed scrape/search backend
  - optional Tavily-backed `WebSearch`
  - optional Brave Search-backed `WebSearch`
  - builtin web backend environment variables can be read from the process
    environment or a project-root `.env` file
  - GitHub blob URL normalization for Firecrawl-backed `WebFetch`
  - backend transport/search failures are returned as failed `ToolResult` payloads so a turn can
    continue and explain the upstream problem instead of immediately hard-failing the whole turn
- review command baseline via `CommandKind.REVIEW`
- tool provenance / visibility metadata
- MCP tool / prompt / skill adaptation seam
- runtime 默认注入 builtin tool baseline；host 默认 demo tools 只是额外叠加
- builtin file / shell tools 默认作用于当前 session/subagent 的 workspace
- builtin file / shell tools 只在显式 `ToolExecutionContext.working_directory` 下执行，不再存在全局 workspace fallback
- `Bash` 在自己 workspace 内默认允许执行，但不能删除或替换 workspace 根目录本身；越界访问仍需授权
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
