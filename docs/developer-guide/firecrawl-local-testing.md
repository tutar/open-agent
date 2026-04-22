# Firecrawl Local Testing

这份文档说明如何在本地为 OpenAgent 的 `WebFetch / WebSearch` 拉起 Firecrawl backend。

## What This Is

当前 OpenAgent 里的 builtin web tools 已经支持 backend abstraction：

- `WebFetch`
- `WebSearch`

Firecrawl 是其中一种可选 backend。

它的作用是：

- `WebFetch` 通过 Firecrawl `scrape` 直接获取 markdown
- `WebSearch` 通过 Firecrawl `search` 返回结果列表

注意：

- Firecrawl 不是唯一实现
- 默认 backend 仍然保留
- 只有显式配置 backend 为 `firecrawl` 时才会切换

## Start Firecrawl With Docker Compose

项目根目录下已经提供：

- `docker-compose.firecrawl.yml`
- `.env.firecrawl.example`

最小启动方式：

```bash
cp .env.firecrawl.example .env.firecrawl
docker compose --env-file .env.firecrawl -f docker-compose.firecrawl.yml up -d
```

默认会拉起：

- `nuq-postgres`
- `redis`
- `rabbitmq`
- `playwright-service`
- `firecrawl`

默认本地入口：

```text
http://127.0.0.1:3002
```

这份 compose 面向 OpenAgent 的本地功能测试，不等于 Firecrawl 官方完整生产部署。

当前 compose 会显式提供 `NUQ_DATABASE_URL`，并直接使用 Firecrawl 官方 `apps/nuq-postgres` 初始化镜像，避免 Firecrawl 容器在内部再尝试调用 Docker/Podman 去拉起自己的 NUQ PostgreSQL。

## Point OpenAgent To Firecrawl

启动 host 前设置：

```bash
export OPENAGENT_WEBFETCH_BACKEND=firecrawl
export OPENAGENT_WEBSEARCH_BACKEND=firecrawl
export OPENAGENT_FIRECRAWL_BASE_URL=http://127.0.0.1:3002
```

如果你的 Firecrawl 实例启用了鉴权，还可以额外设置：

```bash
export OPENAGENT_FIRECRAWL_API_KEY=...
```

然后再启动：

```bash
uv run openagent-host
```

## Optional Smoke Tests

仓库里有一组 opt-in smoke tests：

```bash
export OPENAGENT_RUN_FIRECRAWL_SMOKE=1
export OPENAGENT_FIRECRAWL_BASE_URL=http://127.0.0.1:3002
pytest -q tests/tools/test_web_backends.py
```

默认不会跑 live Firecrawl smoke。

## Semantics

切到 Firecrawl backend 后：

- `WebFetch`
  - 输入仍然是 `url`
  - 返回以 markdown 为主内容
- `WebSearch`
  - 输入仍然是 `query`
  - 返回结果列表
  - 如果 Firecrawl search 同时返回 markdown，会作为 result metadata 附带保留

这保证了 tool surface 稳定，而 backend 可以继续扩展到其他实现。

另外，Firecrawl-backed `WebFetch` 现在会对常见的 GitHub `blob` URL 做一次规范化，
自动改成对应的 `raw.githubusercontent.com` 地址再抓取。这样 agent 在处理
`README.md`、`SELF_HOST.md` 这类源码文档时更容易直接拿到 markdown，而不是 GitHub
页面壳子的 HTML 内容。

当 Firecrawl 返回很长的 scrape 失败 JSON 时，OpenAgent 也会在工具失败层先压成简短摘要，
例如“页面可能阻止自动访问、需要认证或暂时不可用”，而不是把整段底层 500 错误原样回给
前端或 Feishu。
