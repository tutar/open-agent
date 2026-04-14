"""Harness interface definitions."""

from __future__ import annotations

from typing import Any, Protocol

from openagent.object_model import RuntimeEvent, TerminalState, ToolResult


class Harness(Protocol):
    def run_turn(self, input: Any, session_handle: Any) -> tuple[list[RuntimeEvent], TerminalState]:
        """Run a turn and return emitted events plus the terminal state."""

    def build_model_input(self, session_slice: Any, context_providers: list[Any]) -> Any:
        """Build model input from session and context providers."""

    def handle_model_output(self, output: Any) -> Any:
        """Translate model output into next actions."""

    def route_tool_call(self, tool_call: Any) -> ToolResult:
        """Route a tool call through the tool execution layer."""
