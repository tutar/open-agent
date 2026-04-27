# Multi-Agent

当前 `open-agent` 已经落下本地 multi-agent baseline，正式代码路径是
`src/openagent/harness/multi_agent/`。

本轮已实现：

- delegated subagent invocation
- synchronous delegation
- background delegation
- task-notification routing
- direct-view input routing
- viewed transcript projection
- viewed worker retention hold/release
- builtin `Agent` tool 对接本地 delegated runtime

当前边界：

- `harness/task/` 继续拥有 task lifecycle、cursor、retention、verifier
- `harness/multi_agent/` 拥有 delegated worker identity、routing、projection
- teammate execution 不在当前实现范围内
- mailbox 只落对象模型，不落执行 runtime

## Delegation Modes

当前支持两种 delegated worker 运行模式：

- synchronous delegation
  - 立即返回结构化 worker result
- background delegation
  - 返回 task handle
  - 终态通过 task notification 回流给 parent leader

当前 delegated worker 的本地目录也改成 agent-instance 布局：

- `agent_<role_id|default>/<delegated_agent_id>/workspace/`
- `agent_<role_id|default>/<delegated_agent_id>/parent_agent`
- `agent_<role_id|default>/<parent_agent_id>/subagents`

delegated subagent 现在默认继承 parent agent 的 `role_id`，因此：

- role `USER.md` 会在子 agent 的 instruction assembly 中继续生效
- role memory 会继续落到同一个 `roles/<role_id>/memory` durable-memory 根

## Routing

当前实现了两条本地通道：

- `task_notification`
- `direct_view_input`

关键约束：

- task notification 不是广播
- direct-view input 只投递给目标 worker
- viewed transcript 是 projection，不是 worker source of truth

## Builtin Agent Tool

默认 local runtime 现在会注入 builtin `Agent` tool。

这层默认注入由 runtime assembly 负责，不由 host 重复装配。

它会把调用转成 `DelegatedAgentInvocation`，并根据 `run_in_background` 选择：

- 前台同步 delegated result
- 后台 delegated task
