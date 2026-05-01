"""Shared base class for local builtin tools."""

from __future__ import annotations

from dataclasses import dataclass, field

from openagent.object_model import ToolResult
from openagent.tools.models import PermissionDecision, ToolExecutionContext, ToolSource


@dataclass(slots=True)
class BuiltinToolBase:
    """Default implementation surface shared by OpenAgent builtin tools.

    Individual builtin tools override only the hooks they need. Keeping these
    defaults in one place avoids each tool re-declaring the same executor-facing
    behavior and makes directory-per-tool implementations easier to maintain.
    """

    name: str
    description_text: str
    input_schema: dict[str, object]
    aliases: list[str] = field(default_factory=list)
    source: ToolSource = ToolSource.BUILTIN
    visibility: str = "both"
    max_result_size_chars: int = 16_000
    supports_result_persistence: bool = False

    def description(
        self,
        arguments: dict[str, object] | None = None,
        describe_context: dict[str, object] | None = None,
    ) -> str:
        del arguments, describe_context
        return self.description_text

    def is_enabled(self, context: ToolExecutionContext | None = None) -> bool:
        del context
        return True

    def is_read_only(self, arguments: dict[str, object]) -> bool:
        del arguments
        return False

    def is_concurrency_safe(self, arguments: dict[str, object]) -> bool:
        del arguments
        return False

    def check_permissions(
        self,
        arguments: dict[str, object],
        tool_use_context: ToolExecutionContext | None = None,
    ) -> str:
        del arguments, tool_use_context
        return PermissionDecision.ALLOW.value

    def map_result(self, result: ToolResult, tool_use_id: str | None) -> ToolResult:
        if tool_use_id is not None:
            metadata = dict(result.metadata or {})
            metadata["tool_use_id"] = tool_use_id
            result.metadata = metadata
        return result
