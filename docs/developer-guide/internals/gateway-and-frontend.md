# Gateway And Frontend

`gateway` 是当前 frontend 的稳定接入边界，但它在关系上属于 harness 域。

## Why Gateway Exists

frontend 不应该直接碰：

- `SimpleHarness`
- `SessionStore`
- `ToolExecutor`

因为这些都是 runtime 内部实现细节。

gateway 的作用是把 frontend 看到的世界收敛成：

- inbound envelope
- session binding
- harness instance handle
- control routing
- egress projection
- channel-specific default projection policy

## Current Main Path

当前 terminal TUI 主链路是：

`Ink TUI -> terminal TCP client -> Gateway -> InProcessSessionAdapter -> HarnessInstance -> SimpleHarness -> RalphLoop`

其中：

- TUI 负责 UI
- TUI 内建 terminal client，直接和 host 的 terminal 端口收发 line-delimited JSON
- gateway 负责协议归一化
- session adapter 负责把 frontend session 映射到 runtime session

当前 `Gateway` 已拆成独立包：

- `core.py`
- `models.py`
- `interfaces.py`
- `binding_store.py`
- `session_adapter.py`
- `control.py`
- `projector.py`
- `channels/`

## Session Binding

gateway 当前维护：

- channel identity
- conversation id
- session id
- projected event type filter
- binding persistence
- checkpoint metadata

这让 frontend 不需要自己管理 runtime session 细节。

binding 现在还会同步：

- `checkpoint_event_offset`
- `checkpoint_last_event_id`
- `restore_marker`

这让 gateway 在重启或 replay 后能够知道当前前端绑定到的是 session 的哪个 durable 位置。

当前 gateway 不直接拥有 `AgentRuntime`。它实际管理的是：

- chat-to-session binding
- session 对应的 `HarnessInstance` 句柄
- session adapter
- channel-specific projection policy

当前还支持：

- `get_binding(...)`
- `observe_session(...)`
- `resume_bound_session(...)`
- `control.subtype=resume`

其中 `resume` 用于 channel reconnect 后重放当前已 durable 的 session 事件。

## Egress Projection

runtime 内部产生的是 `RuntimeEvent`。

frontend 实际拿到的是 `EgressEnvelope`。

中间多了一层 projection，目的是：

- 把 runtime 事件包装成 channel-aware 输出
- 做 event filtering
- 保留 frontend 真正关心的 session/conversation 信息

当前内置的本地 channel adapter 只有：

- `TerminalChannelAdapter`

它现在归到 `gateway/channels/tui/terminal.py`。

terminal TUI 直接作为 terminal channel 的本地 client：

- TUI 直接通过本地 TCP transport 连接已运行的 Python host
- `Gateway` 负责 session binding / control / egress
- `/channel`、`/channel-config` 这类 host management command 不进入 session，而是由 host 直接处理

对应的本地 terminal transport 也已经归到同一个 channel 包里：

- `gateway/channels/tui/transport.py`

## Why The TUI Connects Directly

terminal TUI 是 Node/Ink 写的，但 runtime 是 Python。当前 terminal channel 直接走 host 暴露的本地 TCP transport，原因是：

- 本地调用链更短
- 调试简单
- 不需要额外的桥接进程
- 符合当前项目“只做本地 TUI 主路径”的约束

## Terminal TUI Internals

terminal TUI 当前内部负责：

- 输入读取
- 命令解析
- 日志展示
- session 状态展示
- 多 session 切换

它并不负责：

- session persistence
- tool execution
- approval state machine

这些都在 Python runtime 里完成。

## Current Limitation

当前 gateway / frontend 这层仍有待增强的部分：

- richer terminal transport abstraction
- more complete reconnect cursor / dedup semantics
- channel-native outbound projection for non-local adapters
