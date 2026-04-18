# MCP Runtime Expansion

Status: proposed

## Summary

在当前 MCP core baseline 之上，继续补齐更深的 runtime projection。

## Current State

- MCP core 已落地到 `src/openagent/tools/mcp/`
- stdio / streamable HTTP / auth discovery / pagination 已实现
- `mcp skill` 已明确为 host extension

## Remaining Gaps

- fuller runtime projection for MCP tasks
- projecting MCP logging into observability in addition to runtime events
- deeper mapping from MCP resources to context / observation surfaces

