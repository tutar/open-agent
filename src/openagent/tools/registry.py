"""Static tool registry baseline."""

from __future__ import annotations

from typing import Any

from openagent.tools.compat import tool_aliases, tool_is_enabled
from openagent.tools.interfaces import ToolDefinition
from openagent.tools.models import ToolRecord, ToolSource, ToolVisibility


class StaticToolRegistry:
    """Registry backed by a fixed list of tool definitions."""

    def __init__(self, tools: list[ToolDefinition]) -> None:
        self._records: list[tuple[ToolRecord, ToolDefinition]] = []
        self._by_name: dict[str, ToolDefinition] = {}
        for tool in tools:
            aliases = tool_aliases(tool)
            record = ToolRecord(
                tool_name=tool.name,
                aliases=aliases,
                source=_tool_source(tool),
                visibility=_tool_visibility(tool),
                provenance=dict(getattr(tool, "provenance", {})),
            )
            self._records.append((record, tool))
            self._by_name[tool.name] = tool
            for alias in aliases:
                self._by_name[alias] = tool

    def list_tools(self, scope: Any | None = None) -> list[ToolDefinition]:
        del scope
        return [tool for _, tool in self._records]

    def list_tool_records(self, scope: Any | None = None) -> list[ToolRecord]:
        del scope
        return [record for record, _ in self._records]

    def resolve_tool(self, name_or_alias: str) -> ToolDefinition:
        try:
            return self._by_name[name_or_alias]
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {name_or_alias}") from exc

    def filter_visible_tools(self, policy: Any, runtime: Any) -> list[ToolRecord]:
        del policy
        visible: list[ToolRecord] = []
        for record, tool in self._records:
            if tool_is_enabled(tool, runtime):
                visible.append(record)
        return visible

    def refresh(self, runtime_context: Any | None = None) -> list[ToolRecord]:
        return self.filter_visible_tools(policy=None, runtime=runtime_context)


def _tool_source(tool: object) -> ToolSource:
    raw = getattr(tool, "source", ToolSource.GENERATED)
    if isinstance(raw, ToolSource):
        return raw
    try:
        return ToolSource(str(raw))
    except ValueError:
        return ToolSource.GENERATED


def _tool_visibility(tool: object) -> ToolVisibility:
    raw = getattr(tool, "visibility", ToolVisibility.BOTH)
    if isinstance(raw, ToolVisibility):
        return raw
    try:
        return ToolVisibility(str(raw))
    except ValueError:
        return ToolVisibility.BOTH
