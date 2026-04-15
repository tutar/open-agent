# Gateway And Frontend

`gateway` 是当前 frontend 的稳定接入边界。

## Why Gateway Exists

frontend 不应该直接碰：

- `SimpleHarness`
- `SessionStore`
- `ToolExecutor`

因为这些都是 runtime 内部实现细节。

gateway 的作用是把 frontend 看到的世界收敛成：

- inbound envelope
- session binding
- control routing
- egress projection

## Current Main Path

当前 terminal TUI 主链路是：

`Ink TUI -> bridge.py -> Gateway -> InProcessSessionAdapter -> SimpleHarness`

其中：

- TUI 负责 UI
- bridge 负责 stdio JSON lines
- gateway 负责协议归一化
- session adapter 负责把 frontend session 映射到 runtime session

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

## Egress Projection

runtime 内部产生的是 `RuntimeEvent`。

frontend 实际拿到的是 `EgressEnvelope`。

中间多了一层 projection，目的是：

- 把 runtime 事件包装成 channel-aware 输出
- 做 event filtering
- 保留 frontend 真正关心的 session/conversation 信息

## Why The Bridge Is In Python

terminal TUI 是 Node/Ink 写的，但 runtime 是 Python。

当前 bridge 用 stdio JSON lines，而不是 HTTP 或 IPC daemon，原因是：

- 本地调用链更短
- 调试简单
- 没有多余服务进程
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

当前 gateway / frontend 这层还没补齐：

- 更完整的 host mode semantics
- 更丰富的 frontend channel adapter 抽象
