# Contributing

## Local Setup

```bash
uv sync
```

当前默认开发环境为 `Python 3.11.15`。

## Required Checks

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy .
```

Pull requests targeting `main` should be merged only after the GitHub Actions Python SDK workflow passes.

## Coding Standards

- Follow the `src/openagent/` module boundaries from `agent-sdk-spec`
- Prefer explicit protocol interfaces at module boundaries
- Keep public types JSON-serializable where practical
- Add comments only for non-obvious decisions, invariants, or spec mappings
- Avoid introducing runtime dependencies unless they remove clear implementation risk
