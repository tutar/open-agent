"""Tool executor baseline."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from openagent.object_model import RequiresAction, ToolResult
from openagent.tools.errors import RequiresActionError, ToolPermissionDeniedError
from openagent.tools.interfaces import ToolRegistry
from openagent.tools.models import PermissionDecision, ToolCall, ToolExecutionContext


class SimpleToolExecutor:
    """Execute tools with basic permission checks and concurrency grouping."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def run_tools(
        self,
        tool_calls: list[ToolCall],
        context: ToolExecutionContext,
    ) -> list[ToolResult]:
        if not tool_calls:
            return []

        safe_calls: list[tuple[int, ToolCall]] = []
        ordered_results: list[tuple[int, ToolResult]] = []

        for index, tool_call in enumerate(tool_calls):
            tool = self._registry.resolve_tool(tool_call.tool_name)
            decision = PermissionDecision(tool.check_permissions(tool_call.arguments))

            if (
                decision is PermissionDecision.ASK
                and tool_call.tool_name in context.approved_tool_names
            ):
                decision = PermissionDecision.ALLOW

            if decision is PermissionDecision.ASK:
                raise RequiresActionError(
                    requires_action=RequiresAction(
                        action_type="tool_permission",
                        session_id=context.session_id,
                        tool_name=tool_call.tool_name,
                        description=f"Permission required for tool {tool_call.tool_name}",
                        input=tool_call.arguments,
                        request_id=tool_call.call_id,
                    )
                )

            if decision is PermissionDecision.DENY:
                raise ToolPermissionDeniedError(
                    tool_name=tool_call.tool_name,
                    reason="Permission denied by tool policy",
                )

            if tool.is_concurrency_safe():
                safe_calls.append((index, tool_call))
                continue

            ordered_results.append((index, tool.call(tool_call.arguments)))

        if safe_calls:
            with ThreadPoolExecutor(max_workers=len(safe_calls)) as pool:
                futures = []
                for index, tool_call in safe_calls:
                    tool = self._registry.resolve_tool(tool_call.tool_name)
                    futures.append((index, pool.submit(tool.call, tool_call.arguments)))

                for index, future in futures:
                    ordered_results.append((index, future.result()))

        return [result for _, result in sorted(ordered_results, key=lambda item: item[0])]
