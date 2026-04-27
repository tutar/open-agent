# WeChat Private Chat Channel

OpenAgent 的微信通道面向私聊文本消息，基于 `wechatbot-sdk` 直连 WeChat iLink Bot API。

第一版不引入 sidecar、Node bridge 或自定义 `/getupdates` 轮询进程。SDK 负责二维码登录、长轮询、`context_token` 管理和 `reply()` 路由；OpenAgent 只负责把消息接入现有 `Gateway -> runtime -> Gateway egress` 链路。

## Install

```bash
uv sync --extra wechat
```

源码开发时也可以和 dev 依赖一起安装：

```bash
uv sync --dev --extra wechat
```

## Configuration

可选环境变量：

```bash
export OPENAGENT_ROOT=.openagent
export OPENAGENT_WECHAT_BASE_URL=https://ilinkai.weixin.qq.com
export OPENAGENT_WECHAT_CRED_PATH=.openagent/wechat/credentials.json
export OPENAGENT_WECHAT_ALLOWED_SENDERS=wx_user_1,wx_user_2
```

`openagent-host` 会从 `OPENAGENT_ROOT` 推导默认目录：

- `${OPENAGENT_ROOT}/sessions`
- `${OPENAGENT_ROOT}/agent_<role_id|default>/agents/local-agent/model-io`
- `${OPENAGENT_ROOT}/sessions/<session_id>/bindings/`

一般不需要再单独配置 `OPENAGENT_SESSION_ROOT` / `OPENAGENT_BINDING_ROOT`。

`OPENAGENT_WECHAT_ALLOWED_SENDERS` 为空时允许所有私聊联系人驱动当前 agent，适合本地 demo；真实使用时建议配置允许列表。

## Run

启动时预加载：

```bash
uv run openagent-host --channel wechat
```

或者先启动 host，再通过任意已连接通道加载：

```text
/channel wechat
```

也可以在运行中写入仅当前进程有效的配置：

```text
/channel-config wechat base_url https://ilinkai.weixin.qq.com
/channel-config wechat cred_path .openagent/wechat/credentials.json
/channel-config wechat allowed_senders wx_user_1,wx_user_2
/channel wechat
```

首次启动 SDK 时会触发二维码登录流程。凭据状态由 `wechatbot-sdk` 使用 `OPENAGENT_WECHAT_CRED_PATH` 对应路径管理。

## Message Flow

```text
WeChat private chat
-> wechatbot-sdk on_message(msg)
-> WechatSdkClient.event_from_message(msg)
-> WechatChannelAdapter.normalize_inbound()
-> Gateway.process_input()
-> OpenAgent runtime
-> WechatChannelAdapter.project_outbound()
-> wechatbot-sdk bot.reply(msg, text)
```

目前支持：

- 私聊文本消息
- `/channel` 和 `/channel-config ...` 管理命令
- 基于微信用户 id 的 lazy session binding
- 入站 message id 去重
- `allowed_senders` 私聊发送人限制

暂不覆盖：

- 群聊
- 图片、文件、语音等非文本消息
- 直接手写 iLink 原始协议
- 自研 `context_token` 或 `get_updates_buf` 持久化

## Code Map

- `src/openagent/gateway/channels/wechat/adapter.py`：微信事件和 Gateway envelope 的双向投影。
- `src/openagent/gateway/channels/wechat/client.py`：`wechatbot-sdk` 的同步外观包装。
- `src/openagent/gateway/channels/wechat/host.py`：启动 SDK、去重、allowlist、session binding 和回复分发。
- `src/openagent/gateway/channels/wechat/dedupe.py`：内存和文件型入站消息去重。
- `src/openagent/gateway/channels/wechat/assembly.py`：配置、runtime、gateway 和 host 组装。
- `src/openagent/gateway/assemblies/channel_manager.py`：统一 host 的 `/channel wechat` 和 `/channel-config wechat ...` 管理入口，以及 channel config resolve / host startup。

## Tests

聚焦测试：

```bash
uv run pytest tests/gateway/test_wechat_gateway.py tests/gateway/test_host_management.py -q
```

如果本机 `.python-version` 指向未安装的 patch 版本，可以显式指定兼容的 Python 3.11：

```bash
uv run --python 3.11.14 pytest tests/gateway/test_wechat_gateway.py tests/gateway/test_host_management.py -q
```
