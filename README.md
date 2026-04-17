# openagent Python SDK

`openagent` 是一个按 `agent-sdk-spec` 组织的 Python SDK 骨架工程。

当前项目已具备完整的本地优先运行基线：对象模型、session/memory、builtin tool baseline、
tool policy/executor、commands/skills/MCP compatibility、本地 harness、gateway、多 channel host，
以及最小 sandbox / orchestration 适配层。

## Quick Try

统一启动方式是先拉起 Python host，再接入 channel。

本地 terminal TUI：

```bash
python -m openagent.cli.host
cd frontend/terminal-tui
npm install
npm run dev
```

Feishu：

```bash
export OPENAGENT_PROVIDER=openai
export OPENAGENT_BASE_URL=http://127.0.0.1:8001
export OPENAGENT_MODEL=gpt-4.1
export OPENAGENT_WORKSPACE_ROOT=$PWD
python -m openagent.cli.host
```

然后在 TUI 里执行：

```text
/channel
/channel feishu
```

如果没有预设飞书环境变量，再补：

```text
/channel-config feishu app_id <value>
/channel-config feishu app_secret <value>
```

`openagent-feishu` 仍然可用，但它现在只是统一 host 的 Feishu 预加载包装。

## Model Data Capture

模型输入输出现在默认会沉淀到：

```text
.openagent/data/model-io/
```

这里不是调试 console 输出，而是 agent 级原始数据采集层。它会保留：

- assembled `ModelTurnRequest`
- provider payload
- provider raw response
- parsed `ModelTurnResponse`
- provider 明确返回的 reasoning / thinking blocks

如需改目录，可设置：

- `OPENAGENT_DATA_ROOT`
- `OPENAGENT_MODEL_IO_ROOT`

## Goals

- 对齐 `agent-sdk-spec` 的五大模块边界
- 提供跨语言 canonical object model 的 Python 占位实现
- 为后续本地 gateway / frontend 装配与行为实现预留稳定入口

## Docs

- [Get Started](./docs/get-started.md)
- [Features](./docs/features.md)
- [Developer Guide](./docs/developer-guide/README.md)

## Layout

- `src/openagent/object_model/`: canonical objects and schema envelope
- `src/openagent/harness/`: harness interfaces, runtime, and provider-backed model integration
- `src/openagent/session/`: session interfaces
- `src/openagent/tools/`: tools domain, builtin tools, commands, skills, MCP, policy, executor
- `src/openagent/sandbox/`: sandbox interfaces
- `src/openagent/orchestration/`: orchestration interfaces
- `src/openagent/shared/`: shared constants and helpers
- `src/openagent/local.py`: local runtime / gateway assembly helpers
- `tests/`: unit tests for skeleton contracts

## Spec Mapping

本项目中的目录边界映射到 `agent-sdk-spec`：

- `harness`
- `session`
- `tools`
- `sandbox`
- `orchestration`
- `object_model`

当前 Python SDK 只面向本地 `terminal` 和 `feishu` 场景，不考虑 `Cloud`。模块之间默认使用同进程直接函数调用，
优先降低复杂度和调用开销，而不是为远程绑定或 IPC 预留抽象成本。

`frontend/` 目录现在位于 `agent-python-sdk/` 内。terminal TUI 采用 `React + Ink + Yoga`，
terminal TUI 通过 gateway 使用 agent runtime，而不是直接持有 harness。
因此 Python SDK 当前推荐的集成入口是 `openagent.local.create_*_runtime(...)`
和 `openagent.local.create_gateway_for_runtime(...)`。

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy .
```

当前本地开发环境基线是 `Python 3.11.15`。

Terminal TUI 前端还需要一个本地 Node 运行时。统一 host 模型下，先启动 Python host，再启动 TUI：

```bash
python -m openagent.cli.host
cd frontend/terminal-tui
npm install
npm run dev
```

## Development Standards

- Use Python 3.11.15 or a compatible 3.11 runtime
- Keep the package in `src/` layout
- Prefer dataclasses and protocols for stable cross-module contracts
- Keep comments short and only where module boundaries or spec intent are not obvious
- Run `pytest`, `ruff check`, `ruff format --check`, and `mypy` before merging

## Status

- Done: project scaffolding, object model base, session/memory baseline, builtin tool baseline,
  policy-aware executor, local harness, gateway/host baseline, and local sandbox baseline
- Next: deepen tool recovery semantics, expand orchestration-backed tool bridges, and keep conformance aligned

## CI

GitHub CI should block merges to `main` unless the `agent-python-sdk` checks pass:

- `pytest -q`
- `ruff check .`
- `ruff format --check .`
- `mypy .`
