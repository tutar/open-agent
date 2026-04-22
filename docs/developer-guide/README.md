# Developer Guide

这份文档现在作为开发文档入口页使用。

## Scope

当前活跃开发范围聚焦在本地 `terminal/TUI` 路径：

- 不做 `Cloud`
- 不引入 remote binding
- 不为了未来分布式场景提前引入 IPC / daemon 复杂度
- frontend 通过 gateway 接入，不直接碰 harness

## Developer Docs Map

- [Contributing](./contributing.md)
  - 本地环境准备
  - 提交流程
  - 测试和 CI 门禁
  - 代码与文档维护约束
- [Architecture](./architecture.md)
  - 系统模块划分
  - runtime 主链路
  - gateway 边界
  - terminal TUI 集成方式
- [Feishu E2E Debugging](./feishu-e2e-debugging.md)
  - `lark-cli` 真实消息联调
  - 飞书服务到 gateway 的链路验证
  - host 日志观测点与 smoke checklist
- [Feishu E2E Tests](./feishu-e2e-tests.md)
  - 可执行的本地真实网络测试
  - `pytest -m feishu_e2e`
  - 私聊与指定群聊验证
- [WeChat Private Chat Channel](./wechat-private-chat.md)
  - `wechatbot-sdk` 私聊通道接入方式
  - `/channel wechat` 运行时加载
  - allowlist、session binding 和代码地图
- [WeCom Private Chat Channel](./wecom-private-chat.md)
  - 企业微信 AI Bot WebSocket 接入方式
  - 只依赖 `aiohttp/httpx`，不引入第三方 WeCom SDK
  - `/channel wecom`、allowlist、session binding 和代码地图
- [Firecrawl Local Testing](./firecrawl-local-testing.md)
  - `WebFetch / WebSearch` 的 Firecrawl backend 本地联调
  - Docker Compose 启动方式
  - OpenAgent backend 切换环境变量
- [Web Search Backends](./web-search-backends.md)
  - `WebSearch` 的 default、Firecrawl、Tavily 和 Brave backend 配置
  - `.env` 兜底加载方式
  - Tavily / Brave opt-in smoke tests
- [Pre-Merge Checklist](./pre-merge-checklist.md)
  - 合入 `main` 前的最终验收顺序
  - Python / TUI / Feishu E2E / 文档同步检查
- [Internals](./internals/README.md)
  - 逐模块解释 object model、harness、session、tools、gateway、sandbox、host 和共享层
  - 包括 model integration / providers 边界

## Repository Layout

核心目录：

- `src/openagent/object_model`
- `src/openagent/harness`
- `src/openagent/session`
- `src/openagent/tools`
- `src/openagent/sandbox`
- `src/openagent/gateway`
- `src/openagent/host`
- `src/openagent/shared`
- `src/openagent/local.py`
- `frontend/terminal-tui`
- `tests`
- `docs`

## Current Priorities

当前 backlog 重点在这些方向：

1. richer policy engine implementation
2. provider-aware prompt cache integration 深化
3. 更强的 session/restore lifecycle semantics
4. sandbox capability negotiation 深化
5. memory extraction / consolidation 深化

## Change Checklist

继续开发时默认按这个顺序推进：

1. 明确模块归属
2. 先补 object model 或 interface
3. 再做最小实现
4. 立刻补测试
5. 更新 `docs/Features/`、`docs/Proposals/` 和相关文档

优先同步这些文档：

- `README.md`
- `docs/Features/`
- `docs/Proposals/`
- `docs/developer-guide/contributing.md`
- `docs/developer-guide/architecture.md`
- `docs/developer-guide/internals/`
