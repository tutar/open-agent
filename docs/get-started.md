# Get Started

`openagent` 是一个面向本地 `TUI` 工作流的 Python SDK。当前仓库提供两部分：

- Python SDK：`src/openagent`
- terminal TUI：`frontend/terminal-tui`

另外当前也提供飞书接入基线：

- Feishu gateway host：`openagent.gateway.feishu`

当前推荐的主流程是：

1. 安装 Python 开发依赖
2. 跑通 SDK 检查
3. 配置真实 LLM provider 或使用 demo model
4. 启动 terminal TUI
5. 通过 TUI 走一遍消息、工具调用和审批流程

## Requirements

- Python `3.11.15` 或兼容的 `3.11`
- Node.js `20+`
- `npm`
- 建议使用已激活的本地虚拟环境

## Install Python Dependencies

在 `agent-python-sdk/` 目录下执行：

```bash
uv sync --dev
```

如果你当前不是用 `uv` 管理环境，也可以先保证这些开发依赖已安装：

- `pytest`
- `ruff`
- `mypy`

## Verify The SDK

先确认 Python SDK 本身可用：

```bash
pytest -q
ruff check .
ruff format --check .
mypy .
```

## Install Terminal TUI Dependencies

terminal TUI 使用 `React + Ink + Yoga`，位于 `frontend/terminal-tui/`：

```bash
cd frontend/terminal-tui
npm install
npm run type-check
```

## Start The Terminal TUI

从 `agent-python-sdk/` 目录启动：

```bash
cd frontend/terminal-tui
npm run dev
```

如果本机的 Python 解释器不是 `python3`，先指定 `PYTHON`：

```bash
PYTHON=/path/to/python npm run dev
```

## Connect A Real LLM Backend

当前 terminal bridge 支持两种真实 provider 适配格式：

- OpenAI-compatible: `/v1/chat/completions`
- Anthropic-compatible: `/v1/messages`

你当前提供的本地代理 base URL 是 `http://127.0.0.1:8001`，可以直接这样启动：

OpenAI-compatible:

```bash
export OPENAGENT_PROVIDER=openai
export OPENAGENT_BASE_URL=http://127.0.0.1:8001
export OPENAGENT_MODEL=gpt-4.1
cd frontend/terminal-tui
npm run dev
```

Anthropic-compatible:

```bash
export OPENAGENT_PROVIDER=anthropic
export OPENAGENT_BASE_URL=http://127.0.0.1:8001
export OPENAGENT_MODEL=claude-sonnet-4-5
cd frontend/terminal-tui
npm run dev
```

如果你的本地代理需要鉴权，也可以额外设置：

- `OPENAGENT_API_KEY`
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`

如果没有设置 `OPENAGENT_MODEL`，bridge 会自动回退到本地 demo model。

## Walk Through The Main Flow

启动后可以直接在 TUI 输入：

- 普通文本：触发基础 assistant reply
- `tool hello`：触发 demo tool
- `admin rotate`：触发需要审批的 tool
- `/approve`：批准 pending tool request
- `/reject`：拒绝 pending tool request
- `/sessions`：查看当前已知 session
- `/new ops`：创建并切换到新 session
- `/switch main`：切回已有 session
- `/resume`：回放当前 session 的事件流

建议先按这个顺序验证：

1. 输入 `hello`
2. 输入 `tool demo`
3. 输入 `admin rotate`
4. 输入 `/approve`
5. 输入 `/sessions`
6. 输入 `/resume`

## Important Architecture Boundary

frontend 不直接调用 harness/runtime。

terminal TUI 通过本地 stdio bridge 接入 Python gateway：

`terminal-tui -> bridge.py -> Gateway -> InProcessSessionAdapter -> SimpleHarness -> RalphLoop -> ModelProviderAdapter -> harness/providers`

这条边界当前是稳定集成入口。

## Where To Look Next

- 运行时能力说明：[`features.md`](./features.md)
- 继续开发 SDK：[`developer-guide/README.md`](./developer-guide/README.md)

## Start The Feishu Host

飞书接入第一版走长连接 host，不经过 TUI：

```bash
export OPENAGENT_FEISHU_APP_ID=cli_xxx
export OPENAGENT_FEISHU_APP_SECRET=xxx
export OPENAGENT_PROVIDER=openai
export OPENAGENT_BASE_URL=http://127.0.0.1:8001
export OPENAGENT_MODEL=gpt-4.1
python -m openagent.cli.feishu
```

如果是从已安装包运行，也可以使用：

```bash
openagent-feishu
```

默认行为：

- 私聊消息直接进入 agent
- 群聊消息只有 `@机器人` 才触发
- `/approve`、`/reject`、`/interrupt`、`/resume` 会作为 control 注入 gateway
- session 和 binding 默认走文件持久化，可跨重启恢复

## Debug Feishu End-To-End With `lark-cli`

如果你要走真实飞书链路联调，建议使用飞书官方 CLI `lark-cli` 作为飞书侧消息入口和结果观察入口。

这条真实链路是：

`人工/机器 -> lark-cli -> 飞书服务 -> openagent gateway -> harness/runtime -> openagent gateway -> 飞书服务 -> lark-cli/飞书客户端`

建议先完成：

```bash
npm install -g @larksuite/cli
lark-cli config init
lark-cli auth login --recommend
lark-cli auth status
```

然后启动上面的 `openagent-feishu`，再用 `lark-cli` 给机器人发私聊消息。

完整联调步骤、日志观测点和 smoke checklist 见：

- [`developer-guide/feishu-e2e-debugging.md`](./developer-guide/feishu-e2e-debugging.md)
