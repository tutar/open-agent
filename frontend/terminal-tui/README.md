# Terminal TUI

`frontend/terminal-tui` 是 `openagent` 的 terminal channel 前端，技术栈是 `React + Ink + Yoga`。

它不再自己拉起 Python runtime。现在的模型是：

- Python host 持有唯一的 `Gateway + runtime`
- terminal TUI 只连接这个 host
- terminal channel 会在首次连接时自动加载

## Run

先启动 Python host：

```bash
cd agent-python-sdk
export OPENAGENT_WORKSPACE_ROOT=$PWD
python -m openagent.cli.host
```

再启动 TUI：

```bash
cd frontend/terminal-tui
npm install
npm run dev
```

如果你已经以 `--channel feishu` 启动了 host，这里仍然直接运行 `npm run dev` 即可，不需要重启 host。

如果 host 没有预加载 Feishu，也可以在 TUI 里运行：

```text
/channel
/channel feishu
```

如果缺少 Feishu 配置，host 会提示继续输入：

```text
/channel-config feishu app_id <value>
/channel-config feishu app_secret <value>
```

## Real Provider Mode

在启动 host 前设置真实模型 provider：

```bash
export OPENAGENT_PROVIDER=openai
export OPENAGENT_BASE_URL=http://127.0.0.1:8001
export OPENAGENT_MODEL=gpt-4.1
export OPENAGENT_WORKSPACE_ROOT=$PWD
python -m openagent.cli.host
```

或者：

```bash
export OPENAGENT_PROVIDER=anthropic
export OPENAGENT_BASE_URL=http://127.0.0.1:8001
export OPENAGENT_MODEL=claude-sonnet-4-5
export OPENAGENT_WORKSPACE_ROOT=$PWD
python -m openagent.cli.host
```

如果没配置模型，host 会自动回退到 demo model。

## Demo Commands

- Plain text: normal assistant reply
- `tool <text>`: trigger the demo echo tool
- `admin <text>`: trigger a permission-gated tool
- `/new <name>`: create and bind a local session
- `/switch <name>`: switch to an existing local session and replay its event log
- `/sessions`: list known local sessions
- `/channel`: list loaded channels, loadable channels, and usage
- `/channel <name>`: load a channel on the running host
- `/channel-config feishu <key> <value>`: set runtime Feishu config for the current host process
- `/approve`: approve the pending tool request
- `/reject`: reject the pending tool request
- `/interrupt`: interrupt the current session handle
- `/session`: print local session state
- `/clear`: clear the local log view
- `/help`
- `/exit`
