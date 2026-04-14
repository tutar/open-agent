# openagent Python SDK

`openagent` 是一个按 `agent-sdk-spec` 组织的 Python SDK 骨架工程。

当前项目已具备最小本地运行基线：对象模型、内存 session、静态 tool registry、简单 tool executor、
本地 harness、基础 orchestration，以及最小 sandbox 适配层。

## Goals

- 对齐 `agent-sdk-spec` 的五大模块边界
- 提供跨语言 canonical object model 的 Python 占位实现
- 为后续 `TUI-first` 的装配与行为实现预留稳定入口

## Layout

- `src/openagent/object_model/`: canonical objects and schema envelope
- `src/openagent/harness/`: harness interfaces
- `src/openagent/session/`: session interfaces
- `src/openagent/tools/`: tool interfaces
- `src/openagent/sandbox/`: sandbox interfaces
- `src/openagent/orchestration/`: orchestration interfaces
- `src/openagent/profiles/`: host profile assembly points
- `src/openagent/shared/`: shared constants and helpers
- `tests/`: unit tests for skeleton contracts

## Spec Mapping

本项目中的目录边界映射到 `agent-sdk-spec`：

- `harness`
- `session`
- `tools`
- `sandbox`
- `orchestration`
- `object_model`

`TUI / Desktop / Cloud` 的差异不体现在顶层源码目录拆分上，而是通过 `profiles` 中的宿主装配层表达。当前仅预留 `TUI-first` 的 profile 入口。

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy .
```

当前本地开发环境基线是 `Python 3.11.15`。

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
