# Tools And Capability Surface

这一层负责两件事：

- 执行可调用能力
- 把这些能力统一投影给 runtime 或 frontend

## Tool Execution

当前 tools 主体由三部分组成：

- `StaticToolRegistry`
- `SimpleToolExecutor`
- `SimpleStreamingToolExecutor`
- `ToolCall / ToolExecutionContext`
- `ToolRecord / ToolExecutionSummary / PersistedToolResultRef`

registry 解决“有哪些 tool”。

executor 解决“怎么执行这些 tool”。

当前 `ToolDefinition` 已经不是最小的 `description + call` 协议，而是带有：

- aliases
- enabled / visibility
- read-only / concurrency-safe hints
- per-tool permission hook
- result mapping
- persistence hint

当前本地事件流是：

- `tool_started`
- `tool_progress`
- `tool_result`
- `tool_failed`
- `tool_cancelled`

这里的边界很明确：

- executor 负责生成 tool lifecycle 事件
- harness 负责把这些事件写入 session event log
- harness 再把 `tool_failed / tool_cancelled` 转成对应的 turn 终态

## Permission Flow

每个 tool 当前通过 `check_permissions(...)` 返回：

- `allow`
- `deny`
- `ask`
- `passthrough`

当返回 `ask` 时，executor 不直接执行，而是抛出 `RequiresActionError`。

上层 `SimpleHarness` 捕获后，把它写成：

- `requires_action` runtime event
- pending tool calls in session

这就是 terminal TUI 审批流的基础。

当前还提供了 `RuleBasedToolPolicyEngine`：

- 可以按 `tool_name`
- 或按 `session_id_prefix`
- 或按 `working_directory_prefix`
- 或按 read-only / write intent
- 覆盖默认 `allow / deny / ask`
- 并带有 denial tracking 和 fallback-to-ask baseline

如果规则未命中，仍可回退到 tool 自身的权限策略。

## Concurrency Strategy

当前 executor 会区分 tool 是否 concurrency-safe：

- concurrency-safe：可并发执行
- 非 concurrency-safe：串行执行

这不是完整调度系统，但已经形成了最小调度语义。

## Commands / Skills / MCP

当前 OpenAgent 里不只有 tool。

还存在三类相关能力：

- `Command`
- `SkillDefinition`
- MCP-derived capability

它们的作用不同：

- tool：偏执行
- command：偏 prompt / local UI action / review
- skill：偏可复用 prompt 能力

当前 skill 子接口已经显式区分三层：

- discovery/import
- catalog disclosure
- activation result

`FileSkillRegistry` 现在会做 deterministic precedence、frontmatter import、lenient parsing
和 shadow diagnostics；`SkillActivator` 返回结构化 activation wrapper，并通过
`SkillContextManager` 维护 dedupe、compaction protection 和 bound-resource allowlisting；
`SkillInvocationBridge` 继续只负责把 skill 暴露成模型可调用入口。

更完整的 skills 生命周期单独整理在 [Skills](./skills.md)。

当前还补上了 builtin tool baseline：

- `Read / Write / Edit / Glob / Grep / Bash`
- `WebFetch / WebSearch`
- `AskUserQuestion`
- optional `Agent / Skill` bridge

这些 builtin tools 现在按 tool 独立目录维护，直接位于 `src/openagent/tools/` 下，
例如 `FileReadTool/`、`FileWriteTool/`、`BashTool/`。聚合入口仍然保留，但真实实现
不再集中在单一 `builtin.py` 文件。

每个 builtin tool 目录还应自带自己的 `prompt.py`：

- `prompt.py` 维护该 tool 的 `TOOL_NAME` 和 model-visible `DESCRIPTION`
- tool 类本身从本目录 `prompt.py` 取 name / description
- `harness/bootstrap_prompts.py` 不直接依赖 per-tool descriptions，只通过
  `tools/tool_constants.py` 这类根层 facade 取稳定 tool-name constants

其中 core local file / shell tools 现在有更明确的行为约束：

- `Read`
  - 支持按行偏移和按行数量读取
  - 返回带行号的稳定文本视图，便于后续定位和编辑
- `Edit`
  - 默认要求 `old` 精确匹配一次
  - 多处命中时必须显式使用 `replace_all`
- `Glob / Grep`
  - 支持限定搜索子目录
  - `Glob` 支持结果数量上限
  - `Grep` 直接调用 `rg`/ripgrep，而不是退化成 Python substring search
  - `Grep` 暴露 regex、`output_mode`、context flags、`type`、`head_limit/offset`、
    `multiline` 这组 model-visible schema
  - `Grep` 的 `prompt.py` description 明确要求优先用工具本身，不要通过 `Bash`
    调 `grep` 或 `rg`
- `Bash`
  - 支持显式 `timeout_ms`
  - 继续保持 workspace-bound permission 语义，而不是无边界 shell

tool result 回写链路现在也需要按 richer model 维护：

- transcript 内部不再假设所有 tool result 都必须先压成字符串
- `SessionMessage(role="tool")` 可以持有 richer tool-result content
- `ContextGovernance` 负责：
  - 大结果 externalize 为内部持久化引用
  - 空结果补 `(ToolName completed with no output)` 风格占位
- provider adapters 按能力投影：
  - Anthropic-compatible provider 尽量保留 `tool_result` block 和 text/image content
  - OpenAI-compatible provider 保持 `role=tool` / `tool_call_id` 契约，并把 richer
    tool-result 编码成 JSON 格式文本放进 `content`

当前 6 个 core local tools 的第一批 result contract 为：

- `Read`
  - text block
- `Grep`
  - text blocks；`files_with_matches` 模式带文件摘要
- `Glob`
  - text summary + tool reference style file blocks
- `Write / Edit`
  - 简洁的成功摘要字符串 + structured metadata
- `Bash`
  - text block；当 stdout 是图片 data URI 时输出 image block

这组 core local tools 现在也应按四层验证来维护：

- tool behavior unit tests
- provider contract tests
- harness integration tests
- live model selection evals

其中 live eval 默认不进入常规回归，使用现有模型装配入口 `load_model_from_env()`，
通过 `OPENAGENT_RUN_TOOL_SELECTION_EVAL=1` 和当前 provider/model/base_url 配置来运行。

如果目标是评估“当前 runtime 实际挂载的所有工具”，应优先从 runtime registry 枚举：

- builtin tools
- role-mounted tools
- MCP/custom tools

然后按是否已有场景定义区分为：

- `completed`：有 live scenario 且真实模型通过
- `disclosed_only`：当前只验证能力披露和 registry 装配

其中 `WebFetch / WebSearch` 现在已经和具体实现解耦：

- `WebFetch` 保持“按 URL 定向获取内容”的语义
- `WebSearch` 保持“返回搜索结果列表”的语义
- Firecrawl backend 通过独立 backend seam 接入
- 不把 `WebSearch` 伪装成 `WebFetch`

MCP 不再只是内存兼容层；现在已经拆到 `src/openagent/tools/mcp/`，并固定成四层：

- `protocol.py`
- `transport.py` + `auth.py`
- `runtime.py`
- `extensions.py`

边界固定为：

- protocol client 负责 `initialize / initialized / ping / cancel / close`
- transport 负责 `inmemory / stdio / streamable http`
- auth 只服务 HTTP，不错误套到 stdio
- runtime adaptation 负责 `tool / command / resource / runtime event` 映射
- `mcp skill` 保留在 host extension，不算 core

默认 deterministic 测试 transport 仍然是 `InMemoryMcpTransport`，但当前已经有真实
`StdioMcpTransport` 和 `StreamableHttpMcpTransport`。

## Policy Engine Seam

executor 现在支持一个可选的 `ToolPolicyEngine`：

- 默认路径仍然走 tool 自己的 `check_permissions(...)`
- 如果注入 policy engine，则由 engine 给出最终 `allow / deny / ask`
- approval continuation 语义保持不变

这让更复杂的宿主策略可以落在 executor 边界，而不需要把 tool 定义层改成 host-aware。

## Capability Surface

`CapabilitySurface` 的目标是把不同来源的能力统一成一个投影视图。

当前它已经不是单文件，而是一个 shared package：

- `capability_surface/models.py`
- `capability_surface/projection.py`
- `capability_surface/surface.py`

当前可以统一投影：

- tools
- commands
- skills

并补上：

- origin metadata
- model-visible / user-visible
- host projection

这样 host 层就不需要分别理解 command、skill、tool 的所有细节。

## Why This Matters

如果没有 capability surface，frontend 或 host 层会直接耦合到多个 registry：

- tool registry
- command registry
- skill registry

这会导致展示和过滤逻辑分散。

统一投影之后，host 只需要做：

- list
- filter
- resolve
- project

当前 host projection 已经按 `terminal / feishu / cloud` 区分，统一使用 channel 语义。

## Current Limitation

当前这层还没补齐：

- full orchestration-backed default implementation for `Agent` / review command execution
