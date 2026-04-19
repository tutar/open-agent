# Gateway And Channels

gateway 是 harness 域下的 frontend 稳定接入边界。

## Gateway

当前支持：

- inbound normalization
- built-in `TerminalChannelAdapter`
- built-in `FeishuChannelAdapter`
- channel-specific default event projection
- session binding
- harness instance handle projection
- file-backed session binding persistence
- binding-level session checkpoint tracking
- user message processing
- permission continuation
- interrupt control
- resume control
- mode change control routing baseline
- projected event filtering
- session replay observation
- bound-session replay via `resume_bound_session(...)`
- command-style control projection for chat channels
- host management command baseline via `/channel` and `/channel-config`
- Feishu long-connection host baseline
- Feishu inbound idempotency baseline via `message_id` dedupe

frontend 当前应通过 `Gateway` 使用 agent，不应该直接持有 harness。

## Feishu

Feishu 是同一个 unified host 上的 chat channel。

### 已支持

- Feishu 长连接消息接收
- file-backed chat-to-session binding
- inbound `message_id` 去重
- 原消息 reaction 状态：
  - 处理中 `OneSecond`
  - 完成 `DONE`
- 每个 user turn 一张 reply card
- reply card 优先通过 CardKit 流式更新；若租户权限或平台能力不足，会自动降级为对同一张消息卡片做 patch 更新：
  - running
  - requires_action
  - completed
  - failed
  - interrupted
- reply card 会消费 `assistant_delta`，在同一张卡片上增量追加回复内容
- 为避免飞书远程更新过慢，Feishu 会按短时间窗口聚合多个 delta 再刷新卡片；终态会立即强制 flush
- 审批卡片按钮：
  - approve
  - reject
- card action 默认通过 Feishu 长连接事件进入 host
- card delivery ledger 与失败重试
- pending card 重试按当前会话隔离，不会由其他 chat 的新消息触发

### 当前仍保留

- `/channel`
- `/channel-config`

这两条仍是 Feishu 的临时 management 输入；后续迁到单独的 host management page。

## Terminal TUI

terminal TUI 当前基于 `React + Ink + Yoga`。

它是 `terminal` channel 的前端实现；gateway 侧实现现在位于：

- `gateway/channels/tui/terminal.py`
- `gateway/channels/tui/transport.py`

### 已支持

- 直连 openagent host 的 terminal 端口
- 多 session 创建和切换
- 消息发送
- `assistant_delta` 的实时增量显示
- tool demo
- requires_action 审批
- `/resume` 事件回放
- 事件日志面板
- session 状态面板
- 非 TTY 环境下的安全降级

### 命令

- `/new <name>`
- `/switch <name>`
- `/sessions`
- `/approve`
- `/reject`
- `/interrupt`
- `/session`
- `/clear`
- `/help`
- `/exit`
