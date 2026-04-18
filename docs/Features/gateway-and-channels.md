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

## Terminal TUI

terminal TUI 当前基于 `React + Ink + Yoga`。

### 已支持

- 直连 openagent host 的 terminal 端口
- 多 session 创建和切换
- 消息发送
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

