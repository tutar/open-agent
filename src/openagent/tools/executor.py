"""Tool executor baseline."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from time import perf_counter

from openagent.object_model import RequiresAction, RuntimeEvent, RuntimeEventType, ToolResult
from openagent.observability import AgentObservability, ProgressUpdate, RuntimeMetric
from openagent.tools.errors import (
    RequiresActionError,
    ToolCancelledError,
    ToolExecutionFailedError,
    ToolPermissionDeniedError,
)
from openagent.tools.interfaces import ToolDefinition, ToolPolicyEngine, ToolRegistry
from openagent.tools.models import (
    PermissionDecision,
    ToolCall,
    ToolExecutionContext,
    ToolPolicyOutcome,
    ToolProgressUpdate,
)


class SimpleToolExecutor:
    """Execute tools with basic permission checks and concurrency grouping."""

    def __init__(
        self,
        registry: ToolRegistry,
        policy_engine: ToolPolicyEngine | None = None,
        observability: AgentObservability | None = None,
    ) -> None:
        self._registry = registry
        self._policy_engine = policy_engine
        self._observability = observability

    def set_observability(self, observability: AgentObservability) -> None:
        """Attach an observability facade after executor construction."""

        self._observability = observability

    def run_tool_stream(
        self,
        tool_calls: list[ToolCall],
        context: ToolExecutionContext,
    ) -> Iterator[RuntimeEvent]:
        if not tool_calls:
            return

        safe_calls: list[tuple[int, ToolCall]] = []

        for index, tool_call in enumerate(tool_calls):
            tool = self._registry.resolve_tool(tool_call.tool_name)
            outcome = self._evaluate_policy(tool, tool_call, context)
            decision = outcome.decision

            if (
                decision is PermissionDecision.ASK
                and tool_call.tool_name in context.approved_tool_names
            ):
                decision = PermissionDecision.ALLOW

            if decision is PermissionDecision.ASK:
                description = (
                    outcome.reason
                    or f"Permission required for tool {tool_call.tool_name}"
                )
                raise RequiresActionError(
                    requires_action=RequiresAction(
                        action_type="tool_permission",
                        session_id=context.session_id,
                        tool_name=tool_call.tool_name,
                        description=description,
                        input=tool_call.arguments,
                        request_id=tool_call.call_id,
                    )
                )

            if decision is PermissionDecision.DENY:
                raise ToolPermissionDeniedError(
                    tool_name=tool_call.tool_name,
                    reason=outcome.reason or "Permission denied by tool policy",
                )

            yield self._tool_started_event(context.session_id, tool_call)

            if context.cancellation_check is not None and context.cancellation_check():
                yield self._tool_cancelled_event(
                    context.session_id,
                    tool_call.tool_name,
                    tool_call.call_id,
                    "cancelled_before_execution",
                )
                continue

            if tool.is_concurrency_safe():
                safe_calls.append((index, tool_call))
                continue

            yield from self._execute_tool_call(tool_call, context)

        if safe_calls:
            with ThreadPoolExecutor(max_workers=len(safe_calls)) as pool:
                futures = []
                for index, tool_call in safe_calls:
                    tool = self._registry.resolve_tool(tool_call.tool_name)
                    futures.append(
                        (
                            index,
                            pool.submit(
                                list,
                                self._execute_tool_call(tool_call, context),
                            ),
                        )
                    )

                for index, future in futures:
                    del index
                    yield from future.result()

    def run_tools(
        self,
        tool_calls: list[ToolCall],
        context: ToolExecutionContext,
    ) -> list[ToolResult]:
        results: list[ToolResult] = []
        for event in self.run_tool_stream(tool_calls, context):
            if event.event_type is RuntimeEventType.TOOL_RESULT:
                payload = dict(event.payload)
                payload.pop("tool_use_id", None)
                results.append(ToolResult.from_dict(payload))
            if event.event_type is RuntimeEventType.TOOL_FAILED:
                raise ToolExecutionFailedError(
                    tool_name=str(event.payload.get("tool_name", "unknown")),
                    reason=str(event.payload.get("reason", "tool_failed")),
                )
            if event.event_type is RuntimeEventType.TOOL_CANCELLED:
                raise ToolCancelledError(
                    tool_name=str(event.payload.get("tool_name", "unknown")),
                    reason=str(event.payload.get("reason", "cancelled")),
                )
        return results

    def _execute_tool_call(
        self,
        tool_call: ToolCall,
        context: ToolExecutionContext,
    ) -> Iterator[RuntimeEvent]:
        tool = self._registry.resolve_tool(tool_call.tool_name)
        stream_call = getattr(tool, "stream_call", None)
        span = None
        started_at = perf_counter()
        if self._observability is not None:
            span = self._observability.start_span(
                "tool",
                {
                    "tool_name": tool_call.tool_name,
                    "concurrency_safe": tool.is_concurrency_safe(),
                },
                session_id=context.session_id,
            )
        try:
            if callable(stream_call):
                yield from self._stream_tool_call(tool_call, context)
                if self._observability is not None and span is not None:
                    duration_ms = (perf_counter() - started_at) * 1000
                    self._observability.emit_runtime_metric(
                        RuntimeMetric(
                            name="tool.duration_ms",
                            value=duration_ms,
                            unit="ms",
                            session_id=context.session_id,
                            attributes={"tool_name": tool_call.tool_name},
                        )
                    )
                    self._observability.end_span(
                        span,
                        {"tool_name": tool_call.tool_name},
                        status="completed",
                        duration_ms=duration_ms,
                    )
                return

            result = tool.call(tool_call.arguments)
            yield self._tool_result_event(
                context.session_id,
                self._attach_call_id(tool_call, result),
            )
            if self._observability is not None and span is not None:
                duration_ms = (perf_counter() - started_at) * 1000
                self._observability.emit_runtime_metric(
                    RuntimeMetric(
                        name="tool.duration_ms",
                        value=duration_ms,
                        unit="ms",
                        session_id=context.session_id,
                        attributes={"tool_name": tool_call.tool_name},
                    )
                )
                self._observability.end_span(
                    span,
                    {"tool_name": tool_call.tool_name},
                    status="completed",
                    duration_ms=duration_ms,
                )
        except ToolCancelledError as exc:
            yield self._tool_cancelled_event(
                context.session_id,
                tool_call.tool_name,
                tool_call.call_id,
                str(exc),
            )
            if self._observability is not None and span is not None:
                duration_ms = (perf_counter() - started_at) * 1000
                self._observability.end_span(
                    span,
                    {"tool_name": tool_call.tool_name, "error": str(exc)},
                    status="cancelled",
                    duration_ms=duration_ms,
                )
        except Exception as exc:
            yield self._tool_failed_event(
                context.session_id,
                tool_call.tool_name,
                tool_call.call_id,
                str(exc),
            )
            if self._observability is not None and span is not None:
                duration_ms = (perf_counter() - started_at) * 1000
                self._observability.end_span(
                    span,
                    {"tool_name": tool_call.tool_name, "error": str(exc)},
                    status="error",
                    duration_ms=duration_ms,
                )

    def _evaluate_policy(
        self,
        tool: ToolDefinition,
        tool_call: ToolCall,
        context: ToolExecutionContext,
    ) -> ToolPolicyOutcome:
        if self._policy_engine is not None:
            return self._policy_engine.evaluate(tool, tool_call, context)
        decision = PermissionDecision(tool.check_permissions(tool_call.arguments))
        return ToolPolicyOutcome(decision=decision)

    def _stream_tool_call(
        self,
        tool_call: ToolCall,
        context: ToolExecutionContext,
    ) -> Iterator[RuntimeEvent]:
        tool = self._registry.resolve_tool(tool_call.tool_name)
        stream_call = getattr(tool, "stream_call")
        for item in stream_call(tool_call.arguments, context):
            if context.cancellation_check is not None and context.cancellation_check():
                raise ToolCancelledError(tool_name=tool_call.tool_name)
            if item.progress is not None:
                if self._observability is not None:
                    self._observability.emit_progress(
                        ProgressUpdate(
                            scope="tool",
                            session_id=context.session_id,
                            summary=item.progress.message,
                            last_activity="tool_progress",
                            attributes={
                                "tool_name": item.progress.tool_name,
                                "progress": item.progress.progress,
                            },
                        )
                    )
                yield self._tool_progress_event(
                    context.session_id,
                    item.progress,
                    tool_call.call_id,
                )
            if item.result is not None:
                yield self._tool_result_event(
                    context.session_id,
                    self._attach_call_id(tool_call, item.result),
                )

    def _tool_progress_event(
        self,
        session_id: str,
        progress: ToolProgressUpdate,
        call_id: str | None,
    ) -> RuntimeEvent:
        payload = progress.to_dict()
        payload["tool_use_id"] = call_id
        return RuntimeEvent(
            event_type=RuntimeEventType.TOOL_PROGRESS,
            event_id=f"tool_progress:{call_id or progress.tool_name}",
            timestamp=datetime.now(UTC).isoformat(),
            session_id=session_id,
            payload=payload,
        )

    def _attach_call_id(self, tool_call: ToolCall, result: ToolResult) -> ToolResult:
        if tool_call.call_id is None:
            return result
        metadata = dict(result.metadata or {})
        metadata["tool_use_id"] = tool_call.call_id
        result.metadata = metadata
        return result

    def _tool_started_event(self, session_id: str, tool_call: ToolCall) -> RuntimeEvent:
        return RuntimeEvent(
            event_type=RuntimeEventType.TOOL_STARTED,
            event_id=f"tool_started:{tool_call.call_id or tool_call.tool_name}",
            timestamp=datetime.now(UTC).isoformat(),
            session_id=session_id,
            payload={
                "tool_name": tool_call.tool_name,
                "arguments": tool_call.arguments,
                "tool_use_id": tool_call.call_id,
            },
        )

    def _tool_result_event(self, session_id: str, result: ToolResult) -> RuntimeEvent:
        payload = result.to_dict()
        metadata = payload.get("metadata")
        if isinstance(metadata, dict) and "tool_use_id" in metadata:
            payload["tool_use_id"] = metadata["tool_use_id"]
        return RuntimeEvent(
            event_type=RuntimeEventType.TOOL_RESULT,
            event_id=f"tool_result:{result.tool_name}",
            timestamp=datetime.now(UTC).isoformat(),
            session_id=session_id,
            payload=payload,
        )

    def _tool_failed_event(
        self,
        session_id: str,
        tool_name: str,
        call_id: str | None,
        reason: str,
    ) -> RuntimeEvent:
        return RuntimeEvent(
            event_type=RuntimeEventType.TOOL_FAILED,
            event_id=f"tool_failed:{call_id or tool_name}",
            timestamp=datetime.now(UTC).isoformat(),
            session_id=session_id,
            payload={
                "tool_name": tool_name,
                "tool_use_id": call_id,
                "reason": reason,
            },
        )

    def _tool_cancelled_event(
        self,
        session_id: str,
        tool_name: str,
        call_id: str | None,
        reason: str,
    ) -> RuntimeEvent:
        return RuntimeEvent(
            event_type=RuntimeEventType.TOOL_CANCELLED,
            event_id=f"tool_cancelled:{call_id or tool_name}",
            timestamp=datetime.now(UTC).isoformat(),
            session_id=session_id,
            payload={
                "tool_name": tool_name,
                "tool_use_id": call_id,
                "reason": reason,
            },
        )
