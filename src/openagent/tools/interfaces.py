"""Tool interface definitions."""

from __future__ import annotations

from typing import Any, Protocol

from openagent.object_model import ToolResult
from openagent.tools.models import ToolCall, ToolExecutionContext


class ToolDefinition(Protocol):
    name: str
    input_schema: dict[str, Any]

    def description(self) -> str:
        """Return a human-readable tool description."""

    def call(self, arguments: dict[str, Any]) -> ToolResult:
        """Execute the tool with validated arguments."""

    def check_permissions(self, arguments: dict[str, Any]) -> str:
        """Return allow, deny, or ask."""

    def is_concurrency_safe(self) -> bool:
        """Return whether this tool can run concurrently."""


class ToolRegistry(Protocol):
    def list_tools(self) -> list[ToolDefinition]:
        """List registered tools."""

    def resolve_tool(self, name: str) -> ToolDefinition:
        """Resolve a tool by name."""

    def filter_visible_tools(self, policy: Any, runtime: Any) -> list[ToolDefinition]:
        """Return visible tools for the current policy and runtime state."""


class ToolExecutor(Protocol):
    def run_tools(
        self,
        tool_calls: list[ToolCall],
        context: ToolExecutionContext,
    ) -> list[ToolResult]:
        """Run a batch of tool calls under the provided execution context."""
