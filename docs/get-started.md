# Get Started

`openagent` 现在按统一 host 模型运行：

- Python host 负责拉起唯一的 `Gateway + runtime + session/memory`
- `terminal` 和 `feishu` 都只是这个 host 上的 channel
- `--channel ...` 只表示启动时预加载哪些 channel
- 未预加载的 channel 可以在运行中通过 `/channel <name>` 加载

最重要的结论只有一句：

先启动 `openagent host`，再让 TUI 或 Feishu 接入它。

下面所有命令都以项目根目录为当前工作目录执行。源码 checkout 下推荐统一使用 `uv run openagent-host`。

## Requirements

- Python `3.11.15` 或兼容的 `3.11`
- Node.js `20+`
- `npm`
- 建议使用已激活的本地虚拟环境

## Install Python Dependencies

在当前项目根目录下执行：

```bash
uv sync --dev
```

## Verify OpenAgent

```bash
pytest -q
ruff check .
ruff format --check .
mypy .
```

## Quickstart 1: Start The Unified Host

最小启动方式：

```bash
export OPENAGENT_WORKSPACE_ROOT=$PWD
uv run openagent-host
```

或者使用安装后的命令：

```bash
openagent-host
```

默认行为：
- 默认监听端口8765
- host 会启动 `terminal` channel 对应的本地 TUI transport
- 不预加载任何外部 channel
- `terminal` channel 会在 TUI 首次连接时自动加载
- 如果没有配置真实模型，会自动回退到 demo model
- host 会打印 `openagent-host> model=...`，可直接确认当前是否真的接上了真实模型
- 每次模型调用默认都会沉淀到 `.openagent/data/model-io`

## Quickstart 2: Connect The Terminal TUI

terminal TUI 是 `React + Ink + Yoga` 前端。它不再自己拉起 runtime，只负责连接已经运行的 host。

先安装前端依赖：

```bash
cd frontend/terminal-tui
npm install
npm run type-check
```

然后启动 TUI：

```bash
cd frontend/terminal-tui
npm run dev
```

启动成功后你会看到：

- `openagent terminal-tui connected`
- 当前 session 为 `main`

推荐先试这些命令：

1. `hello`
2. `tool demo`
3. `admin rotate`
4. `/approve`
5. `/sessions`
6. `/resume`
7. `/channel`

## Quickstart 3: Connect A Real Model

如果要接你本地的模型代理 `http://127.0.0.1:8001`，先在启动 host 前设置 provider。

OpenAI-compatible:

```bash
export OPENAGENT_PROVIDER=openai
export OPENAGENT_BASE_URL=http://127.0.0.1:8001
export OPENAGENT_MODEL=gpt-4.1
export OPENAGENT_WORKSPACE_ROOT=$PWD
uv run openagent-host
```

Anthropic-compatible:

```bash
export OPENAGENT_PROVIDER=anthropic
export OPENAGENT_BASE_URL=http://127.0.0.1:8001
export OPENAGENT_MODEL=claude-sonnet-4-5
export OPENAGENT_WORKSPACE_ROOT=$PWD
uv run openagent-host
```

如果代理需要鉴权，也可以额外设置：

- `OPENAGENT_API_KEY`
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`

模型输入输出数据默认会落到：

```text
.openagent/data/model-io/
```

这里会同时写：

- `index.jsonl`
- `records/<session_id>/<timestamp>-<capture_id>.json`

如果你要改位置，可以在启动 host 前设置：

- `OPENAGENT_DATA_ROOT`
- `OPENAGENT_MODEL_IO_ROOT`

## Optional: Use Firecrawl For Web Tools

如果你想让 builtin `WebFetch / WebSearch` 走 Firecrawl backend：

```bash
export OPENAGENT_WEBFETCH_BACKEND=firecrawl
export OPENAGENT_WEBSEARCH_BACKEND=firecrawl
export OPENAGENT_FIRECRAWL_BASE_URL=http://127.0.0.1:3002
```

本地 Firecrawl 可以通过项目根目录下的 compose 拉起：

```bash
cp .env.firecrawl.example .env.firecrawl
docker compose --env-file .env.firecrawl -f docker-compose.firecrawl.yml up -d
```

更多说明见：

- `docs/developer-guide/firecrawl-local-testing.md`

这层数据不是 observability stdout，也不是 session event log；它是默认保留的模型原始数据资产，
适合后续做离线分析、微调和强化学习数据整理。

## Quickstart 4: Load Feishu On The Same Host

如果你要让同一个 host 同时接入飞书，有两种方式。

方式一：启动时预加载 `feishu` channel：

```bash
export OPENAGENT_FEISHU_APP_ID=cli_xxx
export OPENAGENT_FEISHU_APP_SECRET=xxx
export OPENAGENT_PROVIDER=openai
export OPENAGENT_BASE_URL=http://127.0.0.1:8001
export OPENAGENT_MODEL=gpt-4.1
export OPENAGENT_WORKSPACE_ROOT=$PWD
uv run openagent-host --channel feishu
```

安装后的脚本形态是：

```bash
openagent-host --channel feishu
```

这两种方式的语义是一样的：

- 启动同一个 Python host
- 预加载 Feishu channel
- 飞书首条正常消息进入时，为该 chat 懒创建 `SessionBinding` 和 `HarnessInstance`

方式二：运行中从 terminal TUI 或已加载的 Feishu channel 里执行：

```text
/channel
/channel feishu
```

如果当前进程里没有可用的 Feishu 配置，host 会提示你补：

```text
/channel-config feishu app_id <value>
/channel-config feishu app_secret <value>
```

这些运行中输入的值只在当前 host 进程内有效，不会写回环境变量，也不会落盘。

如果这时你再启动 terminal TUI：

- 不需要重启 host
- 不需要再执行一次 `--channel terminal`
- terminal channel 会在首次连接时自动加载

## Runtime Model

统一 host 模型下，这两条链路分别是：

- terminal:
  `terminal-tui -> openagent host terminal port -> Gateway -> HarnessInstance -> SimpleHarness`
- feishu:
  `Feishu service -> Feishu long connection host -> openagent host -> Gateway -> HarnessInstance -> SimpleHarness`

这里真正持有 runtime 的始终是 Python host，不是前端，也不是 Feishu channel。terminal 通过本地 transport 接入；Feishu 通过对飞书服务的长连接接收入站事件，不共享 terminal 那个本地端口。

## Next Docs

- 按 feature 拆分的能力文档：[`Features/README.md`](./Features/README.md)
- 计划中和待讨论 feature：[`Proposals/README.md`](./Proposals/README.md)
- 开发者文档：[`developer-guide/README.md`](./developer-guide/README.md)
- 飞书真实联调：[`developer-guide/feishu-e2e-debugging.md`](./developer-guide/feishu-e2e-debugging.md)
- 飞书自动化 E2E：[`developer-guide/feishu-e2e-tests.md`](./developer-guide/feishu-e2e-tests.md)
