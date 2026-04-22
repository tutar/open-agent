# Feishu E2E Tests

这份文档描述可执行的本地 Feishu/Lark E2E 测试。

它和 [`feishu-e2e-debugging.md`](./feishu-e2e-debugging.md) 的区别是：

- `feishu-e2e-debugging.md`
  面向手工联调和排障
- `feishu-e2e-tests.md`
  面向可重复执行的本地测试

## What This Test Layer Covers

当前这组测试覆盖：

- 私聊普通回复
- 私聊审批继续
- 私聊 `/resume`
- 私聊工具进度通知

当前不覆盖：

- 私聊首条 slash 控制消息
- thread 深度场景
- 多群并发
- CI 中真实飞书执行

群聊 E2E 已经有独立测试入口，但默认不纳入这组稳定基线。
原因是它依赖飞书应用侧已经正确开启群消息事件投递；如果平台侧还没打开，
本地 Python host 根本收不到任何群消息 raw event。

## Prerequisites

- 已安装 `lark-cli`
- 已完成：

```bash
lark-cli auth login --recommend
lark-cli auth status
```

- 已安装可选 Python 依赖：

```bash
uv sync --dev
pip install "openagent[feishu]"
```

## Required Environment Variables

```bash
export OPENAGENT_RUN_FEISHU_E2E=1
export OPENAGENT_FEISHU_APP_ID=cli_xxx
export OPENAGENT_FEISHU_APP_SECRET=xxx
export OPENAGENT_FEISHU_E2E_P2P_CHAT_ID=oc_xxx
export OPENAGENT_FEISHU_E2E_BOT_NAME=openagent
```

这组测试会启动一个 deterministic 的 Feishu host，因此模型 provider 不是必需前置。

如果要运行群聊 E2E，再额外设置：

```bash
export OPENAGENT_RUN_FEISHU_GROUP_E2E=1
export OPENAGENT_FEISHU_E2E_GROUP_ID=oc_3b0779b193e78de1052091fae2c272d8
```

## Optional Command Templates

默认会按 `lark-cli` README 中的快捷命令形态发送消息：

```bash
lark-cli im +messages-send --as user --chat-id <chat_id> --text <text>
```

如果你的环境里私聊或群聊需要不同的命令形态，可以覆盖：

```bash
export OPENAGENT_FEISHU_E2E_P2P_SEND_TEMPLATE="{binary} im +messages-send --as user --chat-id {chat_id} --text {text}"
export OPENAGENT_FEISHU_E2E_GROUP_SEND_TEMPLATE="{binary} im +messages-send --as user --chat-id {chat_id} --text {text}"
export OPENAGENT_FEISHU_E2E_GROUP_MENTION_TEMPLATE="{binary} im +messages-send --as user --chat-id {chat_id} --text {mention_text}"
```

可用占位符：

- `{binary}`
- `{chat_id}`
- `{text}`
- `{bot_name}`
- `{mention_text}`

## Run The Tests

只执行真实 Feishu E2E：

```bash
pytest -m feishu_e2e -q
```

当前项目里私聊 E2E 的可直接复制命令：

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy \
  OPENAGENT_RUN_FEISHU_E2E=1 \
  OPENAGENT_FEISHU_E2E_P2P_CHAT_ID=oc_b92f525093e8d758add36d57272ec6a1 \
  ./.venv/bin/python -m pytest -q tests/test_feishu_e2e.py -k 'not feishu_group_e2e'
```

只执行群聊 E2E：

```bash
pytest -m feishu_group_e2e -q
```

当前项目里群聊 E2E 的可直接复制命令：

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy \
  OPENAGENT_RUN_FEISHU_E2E=1 \
  OPENAGENT_RUN_FEISHU_GROUP_E2E=1 \
  OPENAGENT_FEISHU_E2E_P2P_CHAT_ID=oc_b92f525093e8d758add36d57272ec6a1 \
  OPENAGENT_FEISHU_E2E_GROUP_ID=oc_3b0779b193e78de1052091fae2c272d8 \
  ./.venv/bin/python -m pytest -q tests/test_feishu_e2e.py -k 'feishu_group_e2e'
```

如果你要把 terminal client 和真实 Feishu E2E 一起纳入一次完整回归，直接执行：

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy \
  OPENAGENT_RUN_FEISHU_E2E=1 \
  OPENAGENT_RUN_FEISHU_GROUP_E2E=1 \
  OPENAGENT_FEISHU_E2E_P2P_CHAT_ID=oc_b92f525093e8d758add36d57272ec6a1 \
  OPENAGENT_FEISHU_E2E_GROUP_ID=oc_3b0779b193e78de1052091fae2c272d8 \
  ./.venv/bin/python -m pytest -q tests -rs
```

默认 `pytest` 不会包含这两组真实网络测试。

## Expected Behavior

测试会启动：

```bash
python -m tests.support.feishu_e2e_host
```

这个 host 使用固定测试模型和固定工具集，确保断言稳定。

预期关键日志包括：

```text
feishu-host> starting long connection
feishu-host> received raw event
feishu-host> normalized input
feishu-host> sending outbound
feishu-host> agent send_text
```

## Notes

- 指定群目前固定为 `oc_3b0779b193e78de1052091fae2c272d8`
- 机器人名称当前固定为 `openagent`
- 私聊基线默认通过 `lark-cli --as user --chat-id <p2p_chat_id>` 驱动
- 私聊首条 slash 控制消息仍保留在单元测试中；真实飞书 p2p 对“第一条就是 `/approve`”这类消息并不稳定
- 群聊测试默认使用 `@openagent`
- 如果 `lark-cli` 未安装、未登录或环境变量缺失，测试会直接 `skip`
- 同一台机器上同一个 Feishu `app_id` 只允许一个 host 长连接；新的 host 会通过本地锁直接拒绝启动，避免“连上了但收不到事件”的假成功
- 群聊测试还要求飞书应用侧已经启用群消息事件投递；否则 host 只会一直安静，不会看到任何 `received raw event`
