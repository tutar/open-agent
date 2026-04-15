# openagent Python SDK

`openagent` 是一个按 `agent-sdk-spec` 组织的 Python SDK 骨架工程。

当前项目已具备最小本地运行基线：对象模型、内存 session、静态 tool registry、简单 tool executor、
本地 harness、基础 orchestration，以及最小 sandbox 适配层。

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
- `src/openagent/tools/`: tool interfaces
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

当前 Python SDK 只面向 `TUI / Desktop` 本地场景，不考虑 `Cloud`。模块之间默认使用同进程直接函数调用，
优先降低复杂度和调用开销，而不是为远程绑定或 IPC 预留抽象成本。

`frontend/` 目录现在位于 `agent-python-sdk/` 内。terminal TUI 采用 `React + Ink + Yoga`，
terminal TUI 和 desktop 前端都应通过 gateway 使用 agent runtime，而不是直接持有 harness。
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

Terminal TUI 前端还需要一个本地 Node 运行时。启动方式：

```bash
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

- Done: project scaffolding, object model base, in-memory session store, static tool registry,
  simple tool executor, minimal local harness, in-memory task manager, and local sandbox baseline
- Next: expand session persistence semantics, richer permission policies, and conformance cases

## CI

GitHub CI should block merges to `main` unless the `agent-python-sdk` checks pass:

- `pytest -q`
- `ruff check .`
- `ruff format --check .`
- `mypy .`
