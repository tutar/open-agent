# Module Structure

这份文档定义 `src/openagent` 的目录结构与职责边界。

它的目标只有两个：

- 说明 OpenAgent 的代码应该放到哪里
- 给后续目录调整提供稳定的判断标准

目录调整时，优先按职责归属，而不是按文件长短或历史路径习惯推进。

## Top-Level Structure

当前 OpenAgent 的目标结构按这些顶层目录组织：

- `object_model/`
  - canonical objects
  - runtime events
  - shared records and payloads
- `capability_surface/`
  - shared capability surface
  - capability projection
  - cross-domain capability exposure seam
- `durable_memory/`
  - bounded recall
  - resident index / manifest layering
  - consolidation
  - payload taxonomy
  - overlay scopes
  - durable store baselines
- `role/`
  - role definition loading
  - role-owned instruction assets
  - role capability refs
  - role runtime assembly helpers
- `harness/`
  - turn runtime
  - model providers
  - context assembly
  - multi-agent coordination
  - task lifecycle
- `session/`
  - transcript
  - event log
  - checkpoint / cursor / resume
  - short-term memory
- `tools/`
  - builtin tools
  - commands
  - skills
  - mcp
  - executor / policy
  - web backends
- `sandbox/`
  - execution boundary
  - environment boundary
  - capability negotiation
- `gateway/`
  - frontend / channel integration boundary
  - bindings
  - projection
  - channel-oriented assembly helpers
- `observability/`
  - observability facade
  - normalized observability models
  - sink interface
  - local/dev sinks
- `host/`
  - host app
  - startup surface
  - host-local config and transport wiring
- `shared/`
  - shared versioning
  - cross-cutting constants or helpers that do not belong to a domain package
- `cli.py`
  - top-level CLI entrypoint
- `local.py`
  - top-level local runtime facade

## Harness Structure

`harness/` 是 OpenAgent 的核心运行时目录，目标上应按这些子主题组织：

- `runtime/`
  - `core/`
    - agent runtime facade
    - turn loop
    - terminal / failure state
    - retry / timeout control
    - runtime event pipeline
  - `io.py`
    - model turn request / response
    - provider exchange
    - model I/O capture records
  - `projection/`
    - runtime-to-observability projection
    - runtime-visible state projection
  - `post_turn/`
    - turn terminal 后处理
    - memory / continuity maintenance
  - `hooks/`
    - runtime lifecycle hook plane
- `providers/`
  - provider adapters
  - streaming integration
  - provider transport
- `context_engineering/`
  - `entry/`
    - bootstrap prompts
    - startup / turn-zero context
  - `assembly/`
    - structured context planes
    - attachments / evidence / capability exposure
  - `governance/`
    - context governance
    - context editing
    - prompt cache strategy
  - `instruction_markdown/`
    - AGENTS / RULES loading
    - include expansion
    - conditional rules
- `assemblies/`
  - local runtime assembly
  - runtime wiring helpers
- `task/`
  - task registry / implementation registry
  - background task lifecycle
  - verifier task runtime
  - task persistence / handles / events
  - output cursor / output slice
  - terminal notification / retention / eviction
- `multi_agent/`
  - delegated worker identity
  - task-notification routing
  - direct-view input routing
  - viewed transcript projection
  - local delegation facade

这里的关键边界是：

- task 和 multi-agent 编排都属于 `harness`
- 后续与 task 有关的代码应优先向 `harness/task/` 收敛
- delegated worker routing/projection 应优先向 `harness/multi_agent/` 收敛
- `runtime/` 是 harness 的主子域，但 provider、context、task 仍保持平级目录

## Stable Top-Level Boundaries

下面这些目录继续保留在顶层，并视为稳定边界：

### `gateway/`

- 负责 frontend / channel 接入
- 负责 binding、inbound normalization、egress projection
- channel-specific config resolve / host startup 应优先放在 `gateway/assemblies/`
- 目录上继续保留顶层，不要求强行并进 `harness/`
- 但职责判断上应始终视为 runtime 的接入边界，而不是独立业务域

### `observability/`

- 负责 observability facade、normalized models、sink interface、local/dev sinks
- 当前主要承载：
  - trace span emission
  - progress projection
  - runtime metric emission
  - session-state signal emission
- 当前同时被 `harness/runtime/projection/`、tools、gateway、task 路径复用
- 作为 shared seam 保留顶层更合理

### `capability_surface/`

- 负责 capability descriptors、origin metadata、projection helpers
- 当前被 tools、context engineering、gateway-facing exposure 共同使用
- 保持顶层 shared seam 更符合当前代码边界

### `shared/`

- 放真正轻量的 shared helpers
- 当前主要是版本信息，不应扩张成新的领域实现目录

### `host/`

- 负责 host app、启动方式、host-local 装配和 transport
- 不是运行时核心域，但它是 OpenAgent 的稳定入口层

## Facades And Compatibility Layers

这些路径可以继续存在，但应被视为 facade / compatibility 层，而不是长期承载真实实现的主目录：

- `local.py`
  - 顶层 facade
  - 真实装配逻辑应继续放在 `harness/assemblies/`
- `cli.py`
  - 顶层 CLI 入口
  - 不视为业务子域
- `gateway/assemblies/feishu.py`
  - compatibility export
  - Feishu-specific assembly 应继续在 channel 子包旁维护
- `gateway/assemblies/channel_manager.py`
  - host-facing channel registry / startup helper
  - 负责 channel spec、config resolve、host startup
  - 不属于 `Gateway` core 本身

## Current Codebase Status

当前仓库中，下面这些核心目录已经稳定：

- `object_model/`
- `harness/`
- `session/`
- `durable_memory/`
- `role/`
- `tools/`
- `sandbox/`
- `gateway/`
- `observability/`
- `host/`
- `capability_surface/`
- `shared/`

当前 `harness/multi_agent/` 已经覆盖本地 baseline：

- delegated subagent invocation
- background delegation
- task-notification routing
- direct-view input
- viewed transcript projection

teammate execution 仍然不在当前实现范围内。

当前 `harness/task/` 已经不再只是 background helper：

- `TaskRegistry` 是 task state 的单一事实来源
- `TaskImplementationRegistry` 负责按 task/type 分发 `await / kill / read_output / read_events`
- background task 与 verifier task 共用同一套 task lifecycle
- task 输出通过 `output_ref + output_cursor` 暴露增量读取语义
- terminal notification、chat/session observer 持有、retention / eviction 都在这个子域内收口

当前 `harness/runtime/` 已经收口为主运行时目录：

- `core/`
- `io.py`
- `projection/`
- `post_turn/`
- `hooks/`

旧的顶层 runtime 文件不再作为正式结构保留。

`terminal` channel 当前也已经统一收口为：

- `gateway/channels/tui/terminal.py`
- `gateway/channels/tui/transport.py`

这里的 `tui` 是目录名，不是新的 channel 名；canonical channel 仍然是 `terminal`。

## Refactor Rules

后续调整 `src/openagent` 目录时，默认遵守这些规则：

### 1. 先按职责归属，再按文件大小

- 文件长不是拆分理由本身
- 真正的拆分依据是职责是否跨域

### 2. 顶层只保留稳定边界、共享层和入口

`src/openagent/` 顶层应尽量只保留：

- stable boundaries
- shared seams
- facades / re-exports
- 明确的 entry helpers

不应继续把领域实现长期停在顶层。

### 3. `harness` 承担运行时核心

- turn runtime
- providers
- context
- multi-agent coordination
- task lifecycle

都属于 `harness` 的职责范围。

### 4. `tools` 是完整能力域

- commands
- skills
- mcp
- web backends

都应继续被视为 `tools` 的稳定子面，而不是外溢成新的顶层目录。

### 5. 允许 facade，优先保持 public imports 稳定

目录下沉时：

- 可以保留旧路径 re-export
- 优先避免打破 `openagent.__init__` 和已公开导出
- 真正变的是内部归属和实现路径

如果某个子域已经被正式收进 `harness/runtime/`，则不要再新增顶层 `harness/*.py`
形式的同类入口。

## Recent Follow-Through

这些调整已经按上面的结构原则落地：

### `local.py`

- 顶层 `local.py` 只保留公开 helper
- 真实装配逻辑已经下沉到 `harness/assemblies/local_runtime.py`

### `capability_surface`

- 已从单文件拆成 shared package
- 继续保留顶层导入语义，但内部职责已经分开

### `context_engineering`

- 已归位到 `harness/context_engineering/`
- startup context、bootstrap prompt、assembly、governance、instruction markdown 都在同一子域内维护

### `harness/runtime`

- `SimpleHarness`、`RalphLoop`、runtime state、runtime I/O capture 已收进
  `harness/runtime/`
- runtime 相关符号不再从根包 `openagent` 或 `openagent.harness` 直接导出
- 正式路径应走 `openagent.harness.runtime`

## Recommended Reading Order

如果后续要调整 `src/openagent` 目录结构，建议按这个顺序看：

1. 本文档
2. `docs/developer-guide/architecture.md`
3. `docs/developer-guide/internals/harness-and-session.md`
4. `docs/developer-guide/internals/tools-and-capability-surface.md`
5. `docs/developer-guide/internals/gateway-and-frontend.md`

这样会先收清目录边界，再进入具体实现重构。
