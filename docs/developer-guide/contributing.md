# Contributing

这份文档描述如何继续开发、测试并合入 `openagent`。

如果你需要快速理解系统结构，先看 [`architecture.md`](./architecture.md)。

## Development Scope

当前仓库的活跃开发范围是本地 terminal/TUI 路径。

默认约束：

- 不做 `Cloud`
- 不做 remote execution target
- 不做为了未来分布式场景提前引入的 IPC / daemon 复杂度
- frontend 通过 gateway 使用 runtime

## Local Setup

推荐在当前项目根目录下工作。

Python 环境基线：

- Python `3.11.15`
- 已激活的本地虚拟环境

安装 Python 依赖：

```bash
uv sync --dev
```

如果你修改 terminal TUI，再安装前端依赖：

```bash
cd frontend/terminal-tui
npm install
```

## Required Checks

OpenAgent 合入前的最低检查：

```bash
pytest -q
ruff check .
ruff format --check .
mypy .
```

terminal TUI 改动后还应执行：

```bash
cd frontend/terminal-tui
npm run type-check
```

## CI Gate

合入 `main` 前，GitHub Actions workflow 必须通过。

当前 CI 门禁包括：

- `pytest -q`
- `ruff check .`
- `ruff format --check .`
- `mypy .`
- `frontend/terminal-tui: npm ci`
- `frontend/terminal-tui: npm run type-check`

真实飞书链路不进入默认 GitHub CI。
合入 `main` 前请额外按 [`pre-merge-checklist.md`](./pre-merge-checklist.md) 执行本地 Feishu E2E 验收。

## Coding Standards

### Python

- 保持 `src/` 布局
- 优先使用 `dataclass` 和 `Protocol`
- 公共对象尽量保持 JSON-serializable
- 注释只写边界、约束和非显然决定
- 保持模块边界稳定，不要把 host 逻辑直接揉进 domain module

### TypeScript / Ink

- terminal TUI 使用 `React + Ink + Yoga`
- 不要让前端直接调用 Python runtime internals
- 当前 bridge 协议保持 stdio JSON lines，优先简单稳定
- 先保证主流程，再加交互优化

## Contribution Workflow

建议按这个顺序工作：

1. 明确改动属于哪个模块
2. 先补 interface 或 object model
3. 再补最小实现
4. 立刻补测试
5. 更新文档、`docs/Features/` 和 `docs/Proposals/`

不要先大面积改实现再回头补结构说明，这会让仓库状态漂移。

## Testing Guidance

`tests/` 按模块域分子目录维护：`object_model/`、`session/`、`durable_memory/`、`harness/`、`gateway/`、`tools/`、`conformance/`、`e2e/`。新增测试默认放到与实现职责最接近的子目录，不再继续往 `tests/` 根目录堆文件。

按改动位置补测：

- `object_model`：`tests/object_model/test_object_model.py`
- `session`：`tests/session/`
- `durable_memory`：`tests/durable_memory/`
- `harness / tools`：baseline + conformance tests
- `gateway`：`tests/gateway/test_gateway_baseline.py`
- `terminal host client`：`tests/gateway/test_terminal_client.py`
- `capability surface / local task lifecycle / local assembly`：`tests/harness/test_platform_baseline.py`

## Documentation Maintenance

如果改动影响以下内容，必须同步更新文档：

- 启动方式
- frontend 接入方式
- public API
- 支持能力边界
- CI 门禁

优先更新：

- `README.md`
- `docs/get-started.md`
- `docs/Features/`
- `docs/Proposals/`
- `docs/developer-guide/README.md`
- `docs/developer-guide/contributing.md`
- `docs/developer-guide/architecture.md`
- `docs/developer-guide/internals/`
