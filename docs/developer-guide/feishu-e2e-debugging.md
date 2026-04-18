# Feishu E2E Debugging

这份文档用于真实飞书链路联调，目标是稳定复现下面这条消息路径：

`人工/机器 -> lark-cli -> 飞书服务 -> openagent gateway -> harness/runtime -> openagent gateway -> 飞书服务 -> lark-cli/飞书客户端`

这里的 `lark-cli` 指飞书官方 CLI：<https://github.com/larksuite/cli>

对当前 OpenAgent 而言，它的角色是：

- 飞书侧的消息发送入口
- 飞书侧的结果观察入口
- 一个真实的 E2E 联调工具

它不是：

- `openagent` 的 Python 依赖
- gateway 的内部组件
- CI 或单测的一部分

## What This Runbook Covers

当前 Runbook 只覆盖这些场景：

- 机器人私聊 `p2p`
- 普通文本消息
- `/approve`
- `/reject`
- `/interrupt`
- `/resume`

当前不覆盖：

- 群聊自动化
- `@mention` 自动化
- thread 深入联调
- 自动化真实网络测试

## Prerequisites

本地需要满足这些前置条件：

- 已激活当前项目的 Python 开发环境
- 能正常启动 `openagent-feishu`
- 已配置可用的模型 provider
- 已拥有可接收机器人私聊的飞书账号
- 已安装 Node.js 和 `npm`

## Install And Log In To `lark-cli`

官方推荐从 npm 安装：

```bash
npm install -g @larksuite/cli
```

初始化本地配置：

```bash
lark-cli config init
```

登录飞书账号：

```bash
lark-cli auth login --recommend
```

确认当前登录状态：

```bash
lark-cli auth status
```

如果你需要重新选择应用或账号，再回到：

```bash
lark-cli config init
```

## Start The OpenAgent Feishu Host

在当前项目根目录启动 Python 侧 host。有两种推荐方式。

方式一：启动时预加载 Feishu：

```bash
export OPENAGENT_FEISHU_APP_ID=cli_xxx
export OPENAGENT_FEISHU_APP_SECRET=xxx
export OPENAGENT_PROVIDER=openai
export OPENAGENT_BASE_URL=http://127.0.0.1:8001
export OPENAGENT_MODEL=gpt-4.1
openagent-feishu
```

如果你当前是源码环境，也可以用：

```bash
python -m openagent.cli.feishu
```

方式二：先启动统一 host，再在任意已接入 channel 中运行：

```bash
python -m openagent.cli.host
```

然后在 TUI 或已加载的 Feishu chat 中执行：

```text
/channel
/channel feishu
```

如果当前进程中没有 Feishu 配置，host 会提示继续输入：

```text
/channel-config feishu app_id <value>
/channel-config feishu app_secret <value>
```

这些运行中输入的值只对当前 host 进程有效，不落盘。

这个进程负责：

- 从飞书长连接接收事件
- 归一化消息并注入 gateway
- 将 agent 的回复重新发送回飞书

## Find The Target Contact

在真实联调前，先用 `lark-cli` 确认你的联系人或机器人目标是正确的。

官方 CLI 支持 Contact 与 Messenger 域能力，推荐先查用户、再决定发给谁。具体命令以你本机安装版本的帮助输出为准：

```bash
lark-cli --help
lark-cli contact --help
lark-cli messenger --help
```

如果需要确认某个具体子命令的参数，再继续查看：

```bash
lark-cli messenger <subcommand> --help
```

## Send A Real Message

确认 Python host 已启动后，用 `lark-cli` 给机器人发一条私聊消息。当前已经验证过的自动化路径是直接向既有私聊 `chat_id` 发消息：

```bash
lark-cli im +messages-send --as user --chat-id <p2p_chat_id> --text hello
```

消息发送命令仍以你本机版本帮助输出为准。

建议第一条消息内容直接使用：

```text
hello
```

然后按这个顺序继续联调：

1. `hello`
2. `admin rotate`
3. `/approve`
4. `/resume`

如果你的当前模型或工具配置没有启用管理员工具流，也可以先只验证：

1. `hello`
2. 第二次再发 `hello again`
3. `/resume`

## Expected Log Signals

当链路正常时，`openagent-feishu` 终端里应该依次看到这些关键信号：

```text
feishu-host> starting long connection
feishu-host> received raw event ...
feishu-host> agent add_reaction message_id=... reaction=...
feishu-host> normalized input kind=user_message conversation=...
feishu-host> sending outbound event=assistant_message chat=...
feishu-host> agent send_text chat=... thread=... text=...
feishu-host> agent remove_reaction message_id=... reaction_id=...
feishu-host> agent add_reaction message_id=... reaction=...
feishu-host> skipped duplicate inbound message_id=...
```

这些日志对应当前实现里的几个关键阶段：

- 已建立飞书长连接
- 已收到飞书服务推送事件
- 已归一化为 gateway 可处理的输入
- 已为原消息打上“处理中” reaction（`emoji_type=OneSecond`）
- 已将 agent 回复投影为飞书消息
- 已通过官方 Python SDK 调用发送接口
- 已将原消息从“处理中”切换到“完成” reaction（`emoji_type=DONE`）
- 同一条飞书消息如果被平台重复投递，会按 `message_id` 被 host 去重

注意：飞书 SDK 的 `reaction_type` 请求体字段本身是对象，OpenAgent 会按
`{"reaction_type": {"emoji_type": "OneSecond"}}`
这类结构发送，而不是把 `OneSecond` 或 `DONE` 当作裸字符串直接传给 `reaction_type`。

## Session Behavior To Verify

当前真实链路下建议确认这些行为：

1. 第一条私聊消息会自动创建并绑定 session
2. 同一个 chat 的后续消息继续落到同一个 session
3. `/resume` 会回放当前 chat 绑定 session 的事件
4. 如果没有已绑定 session 就直接发控制命令，会收到提示消息
5. 同一条消息如果被飞书重复投递，只会处理一次

当前 session id 规则来自 host：

```text
feishu-session:<conversation_id>
```

## Recommended Smoke Checklist

### Case 1: Basic reply

- 向机器人发送 `hello`
- 期望飞书侧收到一条 assistant reply
- 期望 host 日志出现 `received raw event` 和 `agent send_text`

### Case 2: Session reuse

- 再发送一条普通文本
- 期望消息继续落到同一 chat 对应 session
- 期望没有新的 chat-to-session 冲突

### Case 3: Approval flow

- 发送 `admin rotate`
- 期望飞书侧提示 approval required
- 发送 `/approve`
- 期望工具继续执行并最终回复

### Case 4: Resume flow

- 发送 `/resume`
- 期望收到当前会话回放结果

### Case 5: Missing-session control

- 在一个尚未建立 session 的 chat 中先发 `/approve`
- 期望收到：

```text
No active session is bound for this chat yet. Send a normal message first.
```

## Troubleshooting

如果没有收到任何飞书回复，按这个顺序排查：

1. `lark-cli auth status` 是否显示当前账号已登录
2. Python host 是否已经打印 `feishu-host> starting long connection`
3. 同一台机器上是否已经有另一个 `openagent-feishu` / `python -m tests.support.feishu_e2e_host` 在运行
4. 环境变量 `OPENAGENT_FEISHU_APP_ID` 和 `OPENAGENT_FEISHU_APP_SECRET` 是否正确
5. provider 是否可用，特别是 `OPENAGENT_BASE_URL` 和 `OPENAGENT_MODEL`
6. host 是否打印了 `received raw event`
7. host 是否打印了 `agent send_text`
8. 如果你走的是统一 host 动态加载路径，是否已经执行过 `/channel feishu`

如果看到了 `received raw event` 但没有回复，重点检查：

- gateway 是否正常处理了该消息
- 模型 provider 是否返回了 assistant message
- 是否触发了工具审批流但尚未批准

如果控制命令没有生效，先确认：

- 当前 chat 是否已经通过普通消息建立过 session
- 发出的命令是否是 `/approve`、`/reject`、`/interrupt`、`/resume`

如果私聊正常、但群聊 `@openagent` 完全没有任何 `received raw event`，优先检查飞书开放平台里的群消息事件投递配置。
当前 OpenAgent 的群聊 E2E 代码路径已经准备好，但如果应用侧没有把群消息投递给 bot，Python host 端不会收到任何原始事件。

如果启动 host 时直接报“Another local Feishu host is already running for this app_id”，说明同一台机器上已经有另一个进程占用了这只 bot 的长连接。先停掉旧进程，再继续联调。

## Related Docs

- [Get Started](../get-started.md)
- [Architecture](./architecture.md)
