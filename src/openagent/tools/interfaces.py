"""Tool interface definitions."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol

from openagent.object_model import RuntimeEvent, ToolResult
from openagent.tools.models import (
    ToolCall,
    ToolExecutionContext,
    ToolExecutionHandle,
    ToolExecutionSummary,
    ToolPolicyOutcome,
    ToolRecord,
    ToolStreamItem,
)


class ToolDefinition(Protocol):
    name: str
    input_schema: dict[str, Any]
    aliases: list[str]

    def description(self, *args: Any, **kwargs: Any) -> str:
        """Return a human-readable tool description."""

    def call(self, *args: Any, **kwargs: Any) -> ToolResult:
        """Execute the tool with validated arguments."""

    def check_permissions(self, *args: Any, **kwargs: Any) -> str:
        """Return allow, deny, ask, or passthrough."""

    def is_concurrency_safe(self, *args: Any, **kwargs: Any) -> bool:
        """Return whether this tool can run concurrently."""


class StreamingToolDefinition(ToolDefinition, Protocol):
    def stream_call(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Iterator[ToolStreamItem]:
        """Execute the tool with incremental progress and final result."""


class ToolRegistry(Protocol):
    def list_tools(self, scope: Any | None = None) -> list[ToolDefinition]:
        """List registered tools."""

    def list_tool_records(self, scope: Any | None = None) -> list[ToolRecord]:
        """List tool records with source and visibility metadata."""

    def resolve_tool(self, name_or_alias: str) -> ToolDefinition:
        """Resolve a tool by name or alias."""

    def filter_visible_tools(self, policy: Any, runtime: Any) -> list[ToolRecord]:
        """Return visible tools for the current policy and runtime state."""

    def refresh(self, runtime_context: Any | None = None) -> list[ToolRecord]:
        """Refresh dynamic tool records."""


class ToolPolicyEngine(Protocol):
    def evaluate(
        self,
        tool: ToolDefinition,
        tool_call: ToolCall,
        context: ToolExecutionContext,
    ) -> ToolPolicyOutcome:
        """Return the effective policy outcome for the tool call."""


class ToolExecutor(Protocol):
    def execute_stream(
        self,
        tool_calls: list[ToolCall],
        context: ToolExecutionContext,
    ) -> Iterator[RuntimeEvent]:
        """Run tools and yield tool lifecycle events."""

    def get_summary(self, execution_handle: ToolExecutionHandle) -> ToolExecutionSummary:
        """Return an execution summary for a completed tool run."""

    def execute(
        self,
        tool_calls: list[ToolCall],
        context: ToolExecutionContext,
    ) -> list[ToolResult]:
        """Run a batch of tool calls under the provided execution context."""

    def run_tool_stream(
        self,
        tool_calls: list[ToolCall],
        context: ToolExecutionContext,
    ) -> Iterator[RuntimeEvent]:
        """Backward-compatible alias for `execute_stream`."""

    def run_tools(
        self,
        tool_calls: list[ToolCall],
        context: ToolExecutionContext,
    ) -> list[ToolResult]:
        """Backward-compatible alias for `execute`."""


class StreamingToolExecutor(Protocol):
    def add_tool(self, tool_call: ToolCall, assistant_message_ref: str | None = None) -> None:
        """Add a single tool use for incremental execution."""

    def get_completed_results(self) -> list[ToolStreamItem]:
        """Return completed incremental updates."""

    def get_remaining_results(self) -> list[ToolStreamItem]:
        """Drain remaining updates after turn completion."""

    def discard(self) -> None:
        """Discard in-flight state during streaming fallback or abort."""
