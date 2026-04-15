# Get Started

`openagent` 是一个面向本地 `TUI` 工作流的 Python SDK。当前仓库提供两部分：

- Python SDK：`src/openagent`
- terminal TUI：`frontend/terminal-tui`

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

建议先按这个顺序验证：

1. 输入 `hello`
2. 输入 `tool demo`
3. 输入 `admin rotate`
4. 输入 `/approve`
5. 输入 `/sessions`

## Important Architecture Boundary

frontend 不直接调用 harness/runtime。

terminal TUI 通过本地 stdio bridge 接入 Python gateway：

`terminal-tui -> bridge.py -> Gateway -> InProcessSessionAdapter -> SimpleHarness -> RalphLoop -> ModelProviderAdapter -> harness/providers`

这条边界当前是稳定集成入口。

## Where To Look Next

- 运行时能力说明：[`features.md`](./features.md)
- 继续开发 SDK：[`developer-guide/README.md`](./developer-guide/README.md)
