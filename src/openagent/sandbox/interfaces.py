"""Sandbox interface definitions."""

from __future__ import annotations

from typing import Any, Protocol


class Sandbox(Protocol):
    def execute(self, request: Any) -> Any:
        """Execute a sandboxed request."""

    def describe_capabilities(self) -> dict[str, Any]:
        """Return the current sandbox capability view."""
