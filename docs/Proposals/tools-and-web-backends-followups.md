# Tools And Web Backends Follow-ups

Status: proposed

## Summary

当前 tools 基线已经较完整，但 orchestration-backed agent/review 默认实现和默认 web backend 策略仍有剩余工作。

## Current State

- builtin tool baseline 已实现
- Firecrawl-backed `WebFetch` / `WebSearch` 已实现
- review command baseline 已实现
- 这里的 `Agent` 指 builtin `Agent` tool，不是 terminal / Feishu / MCP 这类 channel 或 transport bridge

## Remaining Gaps

- replace the default placeholder `WebSearch` fallback with a stronger default backend strategy
- bridge the builtin `Agent` tool and review commands to orchestration-backed default implementations
- deepen tool retry / recovery / cancellation semantics
