# Tasks And Sandbox

这两个模块当前都属于“本地 baseline”，但它们分别承担不同角色。

## Tasks

本地 task 生命周期已经收进 `harness/task/`。

它负责：

- generic task
- background task
- verifier task
- task registry
- task implementation registry
- checkpoint / progress event
- complete / fail / kill
- event cursor read
- output cursor read
- chat/session observer retention
- terminal notification dedupe
- local background agent execution

multi-agent routing / projection 不再放在这里。

这些语义已经收进 `harness/multi_agent/`：

- delegated worker identity
- task-notification routing
- direct-view input
- viewed transcript projection

## Why Local Task Handles Exist

即使当前不做托管控制面，runtime 仍然需要表达：

- 某个任务在后台跑
- 某个 verifier 在检查结果
- 某个任务已经失败或完成

所以 `TaskManager` 现在不仅提供 task record update APIs，还提供：

- `spawn / register / update`
- `append_event / append_output`
- `read_events / read_output`
- `await / kill`
- `attach_observer / detach_observer`
- `mark_notified / evict_expired`

`TaskRegistry` 现在是 task state 的单一事实来源；
`TaskImplementationRegistry` 则负责把 `await / kill / read_output / read_events`
路由到具体的 background/verifier 本地实现。

现在还补上了：

- `LocalBackgroundAgentOrchestrator`
- `LocalVerificationRuntime`

- 父链路先拿到独立 task handle
- 真正执行在后台线程里进行
- 通过 task events 观测 `task_started / task_progress / task_completed / task_failed / task_killed`
- verifier task 会产出结构化 `VerificationResult`

task 的 observer 语义在 OpenAgent 里按实际产品面落成：

- observer 不是 terminal viewer
- observer = channel chat / session chat 对 task 的附着
- terminal task 在 chat 仍持有期间不会立刻回收
- 解绑后才进入 grace period / eviction

这让本地实现已经覆盖：

- runtime task lifecycle
- task output cursor and resume
- task retention and eviction
- background-agent baseline
- local verifier baseline

## Sandbox

`sandbox` 当前实现非常保守。

核心目标不是“完整沙箱产品”，而是：

- 给 runtime 一个明确的执行边界接口
- 让测试可以验证 allowlist 行为

当前核心实现是：

- `LocalSandbox`
- `SandboxExecutionRequest`
- `SandboxExecutionResult`
- `SandboxCapabilityView`
- `SandboxNegotiationResult`

执行路径现在分成两步：

1. `negotiate(...)`
2. `execute(...)`

`negotiate(...)` 负责返回：

- 是否允许执行
- 为什么拒绝
- 当前实际授予的 network / filesystem / credential 能力

`execute(...)` 会复用 negotiation 结果，在 deny-path 上给出结构化原因，而不只是一个模糊失败。

## Why It Is Still Useful

虽然现在只是 allowlist baseline，但它已经把运行时对“执行环境”的依赖显式化了。

这意味着：

- tool 或 runtime 不需要直接到处调用 shell
- 测试可以断言哪些命令允许、哪些命令禁止
- 以后做更细的能力协商时，有清晰插入点

## Current Limitation

当前这一层还没补齐：

- 更细的 network boundary
- 更细的 credential boundary
- provider-specific sandbox negotiation
