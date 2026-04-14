"""Static tool registry baseline."""

from __future__ import annotations

from typing import Any

from openagent.tools.interfaces import ToolDefinition


class StaticToolRegistry:
    """Registry backed by a fixed list of tool definitions."""

    def __init__(self, tools: list[ToolDefinition]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def resolve_tool(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {name}") from exc

    def filter_visible_tools(self, policy: Any, runtime: Any) -> list[ToolDefinition]:
        del policy, runtime
        return self.list_tools()
