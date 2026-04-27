# WeCom Private Chat Channel

OpenAgent 的企业微信通道面向 AI Bot 私聊文本消息，使用 `aiohttp` 直连企业微信 AI Bot WebSocket。

第一版不引入第三方 WeCom SDK，也不引入 sidecar。OpenAgent 自己实现很窄的一层协议 client：连接 WebSocket、发送 `aibot_subscribe`、发送 ping、接收消息 frame、回复文本 frame。这样可以把供应链风险控制在成熟基础库上。

企业微信 AI Bot 的私聊文本被动回复需要走 `aibot_respond_msg` 下的 `msgtype=stream`。OpenAgent 收到普通用户消息后会先发送一条 `stream.finish=false` 的“处理中，请稍候...”占位回复，让企业微信尽快接收到当前回调的响应；agent 完成推理或搜索后，再用同一个 stream id 发送 `stream.finish=true` 的最终结果。如果误用普通 `msgtype=text`，服务端会返回 `errcode=40008 invalid message type`。

## Install

```bash
uv sync --extra wecom
```

源码开发时也可以和 dev 依赖一起安装：

```bash
uv sync --dev --extra wecom
```

## Configuration

必填：

```bash
export OPENAGENT_WECOM_BOT_ID=your_bot_id
export OPENAGENT_WECOM_SECRET=your_secret
export OPENAGENT_PROVIDER=openai
export OPENAGENT_BASE_URL=http://127.0.0.1:8080
export OPENAGENT_MODEL=Qwen3.5-9B-Q4_K_M.gguf
```

可选：

```bash
export OPENAGENT_ROOT=.openagent
export OPENAGENT_WECOM_WS_URL=wss://openws.work.weixin.qq.com
export OPENAGENT_WECOM_ALLOWED_USERS=userid_1,userid_2
export OPENAGENT_WECOM_PING_INTERVAL_SECONDS=30
```

`openagent-host` 会从 `OPENAGENT_ROOT` 推导默认目录：

- `${OPENAGENT_ROOT}/sessions`
- `${OPENAGENT_ROOT}/agent_<role_id|default>/agents/local-agent/model-io`
- `${OPENAGENT_ROOT}/sessions/<session_id>/bindings/`

一般不需要再单独配置 `OPENAGENT_SESSION_ROOT` / `OPENAGENT_BINDING_ROOT`。

`OPENAGENT_WECOM_ALLOWED_USERS` 为空时允许所有私聊用户驱动当前 agent。真实使用时建议配置允许列表。

如果使用 OpenAI-compatible 本地模型服务，`OPENAGENT_BASE_URL` 写服务根地址，不要带 `/v1`。OpenAgent 会自动拼接 `/v1/chat/completions`。

正确：

```bash
export OPENAGENT_BASE_URL=http://127.0.0.1:8080
```

错误：

```bash
export OPENAGENT_BASE_URL=http://127.0.0.1:8080/v1
```

错误写法会导致模型服务收到 `/v1/v1/chat/completions`，通常表现为 `404`。

## Run

启动时预加载：

```bash
uv run --extra wecom openagent-host --channel wecom
```

源码 checkout 下如果 `.python-version` 指向本机未被 uv 自动发现的 patch 版本，可以显式指定：

```bash
uv run --extra wecom --python 3.11.15 openagent-host --channel wecom
```

启动成功后应看到类似日志：

```text
openagent-host> ready terminal=127.0.0.1:8765 channels=wecom
wecom-host> websocket connected url=wss://openws.work.weixin.qq.com
wecom-host> sent aibot_subscribe frame
wecom-host> subscription acknowledged
```

或者先启动 host，再通过任意已连接通道加载：

```text
/channel wecom
```

也可以在运行中写入仅当前进程有效的配置：

```text
/channel-config wecom bot_id your_bot_id
/channel-config wecom secret your_secret
/channel-config wecom allowed_users userid_1,userid_2
/channel wecom
```

可选配置：

```text
/channel-config wecom ws_url wss://openws.work.weixin.qq.com
```

## Troubleshooting

`errcode=853000 invalid bot_id or secret`

企业微信拒绝了订阅认证。确认 `OPENAGENT_WECOM_BOT_ID` 是 AI Bot 的 Bot ID，`OPENAGENT_WECOM_SECRET` 是同一个 AI Bot 的 Secret；不要使用自建应用的 `AgentId`、`CorpId` 或应用 Secret。

`POST /v1/v1/chat/completions 404`

`OPENAGENT_BASE_URL` 多写了 `/v1`。改为服务根地址，例如：

```bash
export OPENAGENT_BASE_URL=http://127.0.0.1:8080
```

启动后只看到 `subscription acknowledged`，发消息没有后续 frame

WebSocket 认证已经成功，但企业微信没有把消息推给这个 AI Bot。确认你是在和同一个 Bot 聊天，且企业微信后台该 AI Bot 已启用并发布到当前成员可见范围。

`errcode=40008 invalid message type`

当前发送的回复帧消息体不符合企业微信 AI Bot 协议。私聊文本回复应走 `aibot_respond_msg`，但 body 内要使用 `msgtype=stream`，并携带 `stream.id`、`stream.content` 和 `stream.finish=true`。如果按普通文本 `msgtype=text` 回包，企业微信会直接拒绝。

搜索工具已经查到结果，但企业微信里没响应

带 Tavily、Brave 等联网工具的请求可能超过企业微信单次回调的等待窗口。OpenAgent 会在进入模型前先发 `stream.finish=false` 的占位消息，随后在最终答案生成后发 `stream.finish=true` 关闭同一个 stream。若仍无响应，检查 host 日志中是否出现 `wecom-host> send failed: ...`，这通常表示 WebSocket 发送帧被企业微信拒绝或连接已经断开。

需要查看真实企业微信 frame 时，可以打开调试：

```bash
OPENAGENT_WECOM_DEBUG=true uv run --extra wecom --python 3.11.15 openagent-host --channel wecom
```

调试模式会打印完整 frame，包含企业微信返回的字段结构；不要把包含敏感业务内容的完整 frame 发到公开渠道。

## Message Flow

```text
WeCom private chat
-> aiohttp WebSocket
-> WeComAiBotClient.handle_frame()
-> WeComChannelAdapter.normalize_inbound()
-> background handler thread
-> WeComAiBotClient.respond(finish=false) on websocket loop
-> Gateway.process_input() in handler thread
-> OpenAgent runtime in handler thread
-> WeComChannelAdapter.project_outbound()
-> WeComAiBotClient.respond(finish=true) on websocket loop
```

目前支持：

- AI Bot WebSocket 长连接
- 私聊文本消息
- `/channel` 和 `/channel-config ...` 管理命令
- 基于企业微信 user id 的 lazy session binding
- 入站 message id 去重
- `allowed_users` 私聊发送人限制
- 普通用户消息的两段式 stream 回复，占位消息先打开流，最终答案关闭流

暂不覆盖：

- 企业微信 callback 加密 XML 模式
- 群聊
- 图片、文件、语音、卡片等非文本消息
- 通讯录、部门、审批等企业微信管理 API
- 主动发送 HTTP fallback

## Code Map

- `src/openagent/gateway/channels/wecom/adapter.py`：企业微信事件和 Gateway envelope 的双向投影。
- `src/openagent/gateway/channels/wecom/client.py`：基于 `aiohttp` 的企业微信 AI Bot WebSocket client。
- `src/openagent/gateway/channels/wecom/host.py`：启动 client、去重、allowlist、session binding 和回复分发。
- `src/openagent/gateway/channels/wecom/dedupe.py`：内存和文件型入站消息去重。
- `src/openagent/gateway/channels/wecom/assembly.py`：配置、runtime、gateway 和 host 组装。
- `src/openagent/gateway/assemblies/channel_manager.py`：统一 host 的 `/channel wecom` 和 `/channel-config wecom ...` 管理入口，以及 channel config resolve / host startup。

## Tests

聚焦测试：

```bash
uv run pytest tests/gateway/test_wecom_gateway.py tests/gateway/test_host_management.py -q
```

如果本机 `.python-version` 指向未安装的 patch 版本，可以显式指定兼容的 Python 3.11：

```bash
uv run --python 3.11.14 pytest tests/gateway/test_wecom_gateway.py tests/gateway/test_host_management.py -q
```
