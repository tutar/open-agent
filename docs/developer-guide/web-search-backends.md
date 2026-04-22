# Web Search Backends

OpenAgent 的 builtin `WebSearch` 支持可插拔搜索 backend。模型可见工具保持为同一个
`WebSearch`，provider 由 host 启动环境选择。

当前支持：

- `default`：返回搜索 URL placeholder，不访问真实搜索 API。
- `firecrawl`：通过 Firecrawl search API 返回结果列表。
- `tavily`：通过 Tavily search API 返回结果列表。
- `brave`：通过 Brave Search API 返回结果列表。

`Tavily` 和 `Brave` 只接管 `WebSearch`。它们不改变 `WebFetch`；按 URL 抓取内容仍由
default 或 Firecrawl fetch backend 负责。

## Environment Loading

搜索 backend 配置优先读取真实进程环境变量。如果变量没有设置，OpenAgent 会读取当前工作
目录下的 `.env` 文件作为兜底。

示例 `.env`：

```bash
OPENAGENT_WEBSEARCH_BACKEND=tavily
OPENAGENT_TAVILY_API_KEY=...
OPENAGENT_WEBSEARCH_LIMIT=5
```

`.env` 只支持简单的 `KEY=value` 行、空行和 `#` 注释。真实环境变量优先级高于 `.env`。

## Tavily

```bash
export OPENAGENT_WEBSEARCH_BACKEND=tavily
export OPENAGENT_TAVILY_API_KEY=...
export OPENAGENT_TAVILY_BASE_URL=https://api.tavily.com
export OPENAGENT_WEBSEARCH_LIMIT=5
```

必填：

- `OPENAGENT_TAVILY_API_KEY`

可选：

- `OPENAGENT_TAVILY_BASE_URL`
- `OPENAGENT_WEBSEARCH_LIMIT`

## Brave Search

```bash
export OPENAGENT_WEBSEARCH_BACKEND=brave
export OPENAGENT_BRAVE_API_KEY=...
export OPENAGENT_BRAVE_BASE_URL=https://api.search.brave.com
export OPENAGENT_WEBSEARCH_LIMIT=5
```

必填：

- `OPENAGENT_BRAVE_API_KEY`

可选：

- `OPENAGENT_BRAVE_BASE_URL`
- `OPENAGENT_WEBSEARCH_LIMIT`

## Optional Smoke Tests

默认测试不会访问真实 provider API。需要真实联调时显式打开 smoke：

```bash
export OPENAGENT_RUN_TAVILY_SMOKE=1
export OPENAGENT_TAVILY_API_KEY=...
uv run pytest tests/tools/test_web_backends.py -q
```

```bash
export OPENAGENT_RUN_BRAVE_SMOKE=1
export OPENAGENT_BRAVE_API_KEY=...
uv run pytest tests/tools/test_web_backends.py -q
```

smoke tests 只验证 provider 能返回标准 `WebSearchResult`，不依赖具体搜索排名。
