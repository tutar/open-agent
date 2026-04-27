# Channel-Managed Agent Lifecycle

Status: proposed

## Summary

定义多 agent 系统里的运行时规则：

- 每个 agent 在创建时都必须带 role
- agent 不一定拥有 session
- 只有绑定 channel 的 agent 才拥有 session
- 若存在 session，则 agent 与 session 为 `1:1`
- channel 相关配置（如 `appId` / `appSecret`）决定该 agent 是否成为一个可接入 channel 的运行实例

这篇 proposal 只定义 agent / session / channel 的生命周期与绑定规则；不重复定义 role 资产内容。
`roles/` 下也不保存 session、binding、channel card、dedupe 或其他 runtime side-state。

## Current State

当前系统已经具备这些 baseline：

- local multi-agent runtime 已存在
- delegated agent 已有独立 `agent_id`
- gateway/session/task 已能落 `agent_id`
- host/channel assembly 已能从环境变量派生 `role_id`

但仍缺正式 contract：

- agent 创建时 role 的必选规则
- agent 与 session 是否总是关联的明确结论
- channel-bound agent 与 non-channel agent 的边界
- session 与 agent 的一对一关系
- channel 配置如何驱动 agent/session 的生成与绑定

## Proposed Design

### 1. Agent Is The Runtime Unit, Session Is Optional

agent 是运行实例；session 不是必选属性。

系统中至少存在两类 agent：

- non-channel agent
  - 只参与本地 runtime 或 delegated execution
  - 可以没有 session
- channel-bound agent
  - 被某个 channel config 激活
  - 需要拥有 session，并通过 gateway/host 接收外部输入

这条边界要固定下来，避免默认把每个 agent 都做成 session-backed。

### 2. Session Exists Only For Channel-Bound Agents

session 只在 agent 绑定了 channel 后才存在。

这里的“绑定 channel”不是抽象标签，而是具备对应 channel 的有效配置与装配能力，例如：

- Feishu `appId / appSecret`
- WeChat / WeCom 对应 channel config

如果 agent 没有 channel 配置，它仍然可以存在，但不自动拥有 session。

### 3. Agent And Session Are 1:1 When Bound

当一个 agent 绑定 channel 并拥有 session 后，agent 与 session 的关系固定为 `1:1`。

也就是说：

- 一个 channel-bound agent 对应一个 session
- 一个 session 只属于一个 agent

这条规则用于稳定：

- transcript ownership
- runtime state ownership
- gateway binding routing
- model-io / task / card / event side-state 的归属

### 4. Channel Config Drives Agent Activation

channel config 不只是 host 启动参数，而是 agent lifecycle 的激活条件之一。

至少要明确：

- role + agent identity 可以先存在
- 只有当该 agent 配置了某个 channel 所需的 app credentials 时，才会成为该 channel 的 active agent
- active channel agent 才需要：
  - session
  - gateway binding
  - host routing
  - channel-side cards / bindings / dedupe / events

这条规则让“agent 是否关联 session”不再是隐式副作用，而是可解释的装配结果。

### 5. Multi-Agent Delegation Must Preserve Role Assignment

多 agent 场景下，新建 agent 时仍必须带 role。

适用于：

- delegated subagent
- background delegated worker
- future manually created agent instances

但 delegated agent 默认不因为被创建就获得 session；只有后续成为 channel-bound agent 时，才进入 session contract。

## Interface Decisions

本 proposal 固定以下术语边界：

- `agent`
  - 运行实例，创建时必须绑定 role
- `session`
  - channel-bound agent 的会话容器，不是所有 agent 的必备属性
- `channel-bound agent`
  - 具备对应 channel config、可被 host/gateway 路由的 agent
- `active channel config`
  - 使 agent 获得 channel runtime 能力的配置集合

这篇 proposal 不定义：

- `ROLE.md` / skills / MCPs / recommended models 的 role 资产模型
- evaluation suites
- role memory 的具体 schema

## Acceptance Direction

当这篇 proposal 落地后，至少应满足：

- 每个 agent 创建时都带 role
- 系统能明确区分“有 session 的 channel-bound agent”和“无 session 的 non-channel agent”
- session 不再被默认假设为 agent 的必备属性
- channel app config 与 agent/session 绑定关系有稳定、可实现的生命周期规则
- gateway、host、runtime 对 agent_id / role_id / session_id 的归属关系保持一致
