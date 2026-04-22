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
- `harness/`
  - turn runtime
  - model providers
  - context assembly
  - sub-agent coordination
  - task lifecycle
- `session/`
  - transcript
  - event log
  - checkpoint / cursor / resume
  - short-term memory
  - `session.memory` linkage
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
- `observability/`
  - tracing
  - metrics
  - progress / signal projection
- `host/`
  - host app
  - startup surface
  - host-local config and transport wiring

## Harness Structure

`harness/` 是 OpenAgent 的核心运行时目录，目标上应按这些子主题组织：

- `runtime/`
  - turn loop
  - terminal / failure state
  - retry / timeout control
- `providers/`
  - provider adapters
  - streaming integration
  - provider exchange capture
- `context/`
  - bootstrap prompts
  - context governance
  - prompt cache
  - tool-result externalization
- `assemblies/`
  - local runtime assembly
  - runtime wiring helpers
- `task/`
  - task manager
  - background task lifecycle
  - task persistence / handles / events
- `subagents/`
  - sub-agent coordination
  - verifier / reflection / delegated execution flows

这里的关键边界是：

- task 和 sub-agent 编排都属于 `harness`
- `orchestration` 不是目标顶层模块
- 后续与 task 有关的代码应优先向 `harness/task/` 收敛

## Stable Top-Level Boundaries

下面这些目录继续保留在顶层，并视为稳定边界：

### `gateway/`

- 负责 frontend / channel 接入
- 负责 binding、inbound normalization、egress projection
- 目录上继续保留顶层，不要求强行并进 `harness/`
- 但职责判断上应始终视为 runtime 的接入边界，而不是独立业务域

### `observability/`

- 负责 tracing、progress、metrics、session/task signal
- 当前同时被 harness、tools、gateway、task 路径复用
- 作为 shared seam 保留顶层更合理

### `host/`

- 负责 host app、启动方式、host-local 装配和 transport
- 不是运行时核心域，但它是 OpenAgent 的稳定入口层

## Facades And Compatibility Layers

这些路径可以继续存在，但应被视为 facade / compatibility 层，而不是长期承载真实实现的主目录：

- `local.py`
  - 顶层 facade
  - 真实装配逻辑应继续放在 `harness/assemblies/`
- `context_governance.py`
  - compatibility re-export
  - 真实实现应继续放在 `harness/context/`
- `host/service.py`
  - compatibility export
  - 真实 host 实现应在 `host/` 子模块中维护
- `gateway/assemblies/feishu.py`
  - compatibility export
  - Feishu-specific assembly 应继续在 channel 子包旁维护

## Current Codebase Status

当前仓库中，下面这些核心目录已经稳定：

- `object_model/`
- `harness/`
- `session/`
- `tools/`
- `sandbox/`
- `gateway/`
- `observability/`
- `host/`

最近已经完成的一项目录收口是：

- local task 代码已从顶层 `src/openagent/orchestration/` 迁入 `src/openagent/harness/task/`
  - `TaskManager`
  - task manager persistence baselines
  - background task handles and context
  - `LocalBackgroundAgentOrchestrator`

当前后续仍可继续细化的是：

- `harness/subagents/`
  - 目录职责已经确定
  - 但更完整的 sub-agent coordination 代码还会继续向这里收敛

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
- sub-agent coordination
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

## Recent Follow-Through

这些调整已经按上面的结构原则落地：

### `local.py`

- 顶层 `local.py` 只保留公开 helper
- 真实装配逻辑已经下沉到 `harness/assemblies/local_runtime.py`

### `capability_surface`

- 已从单文件拆成 shared package
- 继续保留顶层导入语义，但内部职责已经分开

### `context_governance`

- 已归位到 `harness/context/`
- 顶层 `context_governance.py` 只保留兼容 re-export

## Recommended Reading Order

如果后续要调整 `src/openagent` 目录结构，建议按这个顺序看：

1. 本文档
2. `docs/developer-guide/architecture.md`
3. `docs/developer-guide/internals/harness-and-session.md`
4. `docs/developer-guide/internals/tools-and-capability-surface.md`
5. `docs/developer-guide/internals/gateway-and-frontend.md`

这样会先收清目录边界，再进入具体实现重构。
