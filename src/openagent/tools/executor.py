"""Tool executor baseline."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from time import perf_counter
from typing import cast
from uuid import uuid4

from openagent.object_model import (
    JsonObject,
    JsonValue,
    RequiresAction,
    RuntimeEvent,
    RuntimeEventType,
    ToolResult,
)
from openagent.observability import AgentObservability, ProgressUpdate, RuntimeMetric, SpanHandle
from openagent.tools.compat import (
    persisted_ref_to_string,
    tool_is_concurrency_safe,
    tool_map_result,
    tool_supports_result_persistence,
    tool_validate_input,
)
from openagent.tools.compat import (
    tool_call as invoke_tool_call,
)
from openagent.tools.errors import (
    RequiresActionError,
    ToolCancelledError,
    ToolExecutionFailedError,
    ToolPermissionDeniedError,
)
from openagent.tools.interfaces import (
    StreamingToolExecutor,
    ToolDefinition,
    ToolPolicyEngine,
    ToolRegistry,
)
from openagent.tools.models import (
    PermissionDecision,
    PersistedToolResultRef,
    ToolCall,
    ToolExecutionAbortReason,
    ToolExecutionContext,
    ToolExecutionEvent,
    ToolExecutionEventType,
    ToolExecutionHandle,
    ToolExecutionSummary,
    ToolPolicyOutcome,
    ToolProgressUpdate,
    ToolStreamItem,
)


class SimpleToolExecutor:
    """Execute tools with policy checks, summaries, and concurrency grouping."""

    def __init__(
        self,
        registry: ToolRegistry,
        policy_engine: ToolPolicyEngine | None = None,
        observability: AgentObservability | None = None,
    ) -> None:
        self._registry = registry
        self._policy_engine = policy_engine
        self._observability = observability
        self._summaries: dict[str, ToolExecutionSummary] = {}

    def set_observability(self, observability: AgentObservability) -> None:
        self._observability = observability

    def execute_stream(
        self,
        tool_calls: list[ToolCall],
        context: ToolExecutionContext,
    ) -> Iterator[RuntimeEvent]:
        if not tool_calls:
            return

        handle = ToolExecutionHandle(
            execution_id=f"exec_{uuid4().hex}",
            tool_use_ids=[call.call_id for call in tool_calls if call.call_id],
            session_id=context.session_id,
            task_id=context.task_id,
            started_at=datetime.now(UTC).isoformat(),
        )
        summary = ToolExecutionSummary(handle=handle)
        self._summaries[handle.execution_id] = summary

        safe_calls: list[tuple[ToolCall, dict[str, object]]] = []
        for tool_call in tool_calls:
            tool = self._registry.resolve_tool(tool_call.tool_name)
            arguments = self._validate_arguments(tool, tool_call.arguments)
            normalized_call = ToolCall(
                tool_call.tool_name,
                cast(JsonObject, arguments),
                tool_call.call_id,
            )
            outcome = self._evaluate_policy(tool, normalized_call, context)
            decision = outcome.decision
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
                        description=(
                            outcome.reason
                            or f"Permission required for tool {tool_call.tool_name}"
                        ),
                        input=cast(JsonObject, arguments),
                        request_id=tool_call.call_id,
                        action_ref=tool_call.call_id,
                    )
                )

            if decision is PermissionDecision.DENY:
                raise ToolPermissionDeniedError(
                    tool_name=tool_call.tool_name,
                    reason=outcome.reason or "Permission denied by tool policy",
                )

            started = self._tool_started_event(
                context.session_id,
                normalized_call,
            )
            self._record_runtime_event(summary, started)
            yield started

            if context.cancellation_check is not None and context.cancellation_check():
                cancelled = self._tool_cancelled_event(
                    context.session_id,
                    tool_call.tool_name,
                    tool_call.call_id,
                    ToolExecutionAbortReason.USER_INTERRUPTED.value,
                )
                self._record_runtime_event(summary, cancelled)
                yield cancelled
                continue

            if tool_is_concurrency_safe(tool, arguments):
                safe_calls.append((normalized_call, arguments))
                continue

            for event in self._execute_tool_call(
                tool_call=normalized_call,
                context=context,
                summary=summary,
            ):
                yield event

        if safe_calls:
            with ThreadPoolExecutor(max_workers=len(safe_calls)) as pool:
                futures = [
                    pool.submit(
                        list,
                        self._execute_tool_call(
                            tool_call=tool_call,
                            context=context,
                            summary=summary,
                        ),
                    )
                    for tool_call, _ in safe_calls
                ]
                for future in futures:
                    for event in future.result():
                        yield event

    def get_summary(self, execution_handle: ToolExecutionHandle) -> ToolExecutionSummary:
        return self._summaries[execution_handle.execution_id]

    def execute(
        self,
        tool_calls: list[ToolCall],
        context: ToolExecutionContext,
    ) -> list[ToolResult]:
        results: list[ToolResult] = []
        for event in self.execute_stream(tool_calls, context):
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

    def run_tool_stream(
        self,
        tool_calls: list[ToolCall],
        context: ToolExecutionContext,
    ) -> Iterator[RuntimeEvent]:
        yield from self.execute_stream(tool_calls, context)

    def run_tools(
        self,
        tool_calls: list[ToolCall],
        context: ToolExecutionContext,
    ) -> list[ToolResult]:
        return self.execute(tool_calls, context)

    def _execute_tool_call(
        self,
        tool_call: ToolCall,
        context: ToolExecutionContext,
        summary: ToolExecutionSummary,
    ) -> Iterator[RuntimeEvent]:
        tool = self._registry.resolve_tool(tool_call.tool_name)
        stream_call = getattr(tool, "stream_call", None)
        span: SpanHandle | None = None
        started_at = perf_counter()
        if self._observability is not None:
            span = self._observability.start_span(
                "tool",
                {
                    "tool_name": tool_call.tool_name,
                    "concurrency_safe": tool_is_concurrency_safe(tool, tool_call.arguments),
                },
                session_id=context.session_id,
            )
        try:
            if callable(stream_call):
                for event in self._stream_tool_call(tool_call, context, summary):
                    yield event
                self._finish_span(
                    span,
                    started_at,
                    context.session_id,
                    tool_call.tool_name,
                    "completed",
                )
                return

            result = tool_call_helper(tool, tool_call, context)
            mapped = tool_map_result(tool, result, tool_call.call_id)
            event = self._tool_result_event(context.session_id, mapped, tool_call.call_id)
            self._record_runtime_event(summary, event)
            yield event
            self._finish_span(
                span,
                started_at,
                context.session_id,
                tool_call.tool_name,
                "completed",
            )
        except RequiresActionError:
            self._finish_span(
                span,
                started_at,
                context.session_id,
                tool_call.tool_name,
                "requires_action",
            )
            raise
        except ToolCancelledError as exc:
            event = self._tool_cancelled_event(
                context.session_id,
                tool_call.tool_name,
                tool_call.call_id,
                str(exc),
            )
            self._record_runtime_event(summary, event)
            yield event
            self._finish_span(
                span,
                started_at,
                context.session_id,
                tool_call.tool_name,
                "cancelled",
                str(exc),
            )
        except Exception as exc:
            event = self._tool_failed_event(
                context.session_id,
                tool_call.tool_name,
                tool_call.call_id,
                str(exc),
            )
            self._record_runtime_event(summary, event)
            yield event
            self._finish_span(
                span,
                started_at,
                context.session_id,
                tool_call.tool_name,
                "error",
                str(exc),
            )

    def _evaluate_policy(
        self,
        tool: ToolDefinition,
        tool_call: ToolCall,
        context: ToolExecutionContext,
    ) -> ToolPolicyOutcome:
        if self._policy_engine is not None:
            outcome = self._policy_engine.evaluate(tool, tool_call, context)
            if outcome.decision is PermissionDecision.PASSTHROUGH:
                return ToolPolicyOutcome(
                    decision=PermissionDecision.ALLOW,
                    policy_source="passthrough",
                )
            return outcome
        from openagent.tools.compat import tool_check_permissions

        decision = tool_check_permissions(tool, tool_call.arguments, context)
        return ToolPolicyOutcome(decision=decision, policy_source="tool.check_permissions")

    def _stream_tool_call(
        self,
        tool_call: ToolCall,
        context: ToolExecutionContext,
        summary: ToolExecutionSummary,
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
                event = self._tool_progress_event(
                    context.session_id,
                    item.progress,
                    tool_call.call_id,
                )
                self._record_runtime_event(summary, event)
                yield event
            if item.context_modifier is not None:
                summary.context_modifiers.append(item.context_modifier)
            if item.result is not None:
                mapped = tool_map_result(tool, item.result, tool_call.call_id)
                event = self._tool_result_event(context.session_id, mapped, tool_call.call_id)
                self._record_runtime_event(summary, event)
                yield event

    def _validate_arguments(
        self,
        tool: ToolDefinition,
        arguments: JsonObject,
    ) -> dict[str, object]:
        if not isinstance(arguments, dict):
            raise ToolExecutionFailedError(
                tool_name=tool.name,
                reason="validation_failed: tool arguments must be an object",
            )
        return tool_validate_input(tool, dict(arguments))

    def _record_runtime_event(self, summary: ToolExecutionSummary, event: RuntimeEvent) -> None:
        tool_use_id = (
            str(event.payload.get("tool_use_id"))
            if event.payload.get("tool_use_id") is not None
            else None
        )
        summary.events.append(
            ToolExecutionEvent(
                execution_id=summary.handle.execution_id,
                tool_use_id=tool_use_id,
                type=_event_type_for_runtime_event(event.event_type),
                timestamp=event.timestamp,
                payload=dict(event.payload),
            )
        )
        if event.event_type is RuntimeEventType.TOOL_RESULT:
            payload = dict(event.payload)
            payload.pop("tool_use_id", None)
            summary.results.append(ToolResult.from_dict(payload))
        elif event.event_type is RuntimeEventType.TOOL_FAILED:
            summary.errors.append(str(event.payload.get("reason", "tool_failed")))
        elif event.event_type is RuntimeEventType.TOOL_CANCELLED:
            summary.errors.append(str(event.payload.get("reason", "cancelled")))

    def _finish_span(
        self,
        span: SpanHandle | None,
        started_at: float,
        session_id: str,
        tool_name: str,
        status: str,
        error: str | None = None,
    ) -> None:
        if self._observability is None or span is None:
            return
        duration_ms = (perf_counter() - started_at) * 1000
        self._observability.emit_runtime_metric(
            RuntimeMetric(
                name="tool.duration_ms",
                value=duration_ms,
                unit="ms",
                session_id=session_id,
                attributes={"tool_name": tool_name},
            )
        )
        attributes: JsonObject = {"tool_name": tool_name}
        if error is not None:
            attributes["error"] = error
        self._observability.end_span(
            span,
            attributes,
            status=status,
            duration_ms=duration_ms,
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

    def _tool_result_event(
        self,
        session_id: str,
        result: ToolResult,
        call_id: str | None,
    ) -> RuntimeEvent:
        payload = result.to_dict()
        payload["tool_use_id"] = call_id
        persisted = payload.get("persisted_ref")
        payload["persisted_ref"] = persisted_ref_to_string(
            persisted if isinstance(persisted, (PersistedToolResultRef, str)) else None
        )
        return RuntimeEvent(
            event_type=RuntimeEventType.TOOL_RESULT,
            event_id=f"tool_result:{call_id or result.tool_name}",
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
                "reason": reason,
                "tool_use_id": call_id,
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
                "reason": reason,
                "tool_use_id": call_id,
            },
        )


class SimpleStreamingToolExecutor(StreamingToolExecutor):
    """Minimal incremental executor wrapper over `SimpleToolExecutor`."""

    def __init__(
        self,
        executor: SimpleToolExecutor,
        context: ToolExecutionContext,
    ) -> None:
        self._executor = executor
        self._context = context
        self._buffer: list[ToolStreamItem] = []

    def add_tool(self, tool_call: ToolCall, assistant_message_ref: str | None = None) -> None:
        del assistant_message_ref
        for event in self._executor.execute_stream([tool_call], self._context):
            if event.event_type is RuntimeEventType.TOOL_PROGRESS:
                self._buffer.append(
                    ToolStreamItem(
                        progress=ToolProgressUpdate(
                            tool_name=str(event.payload.get("tool_name", tool_call.tool_name)),
                            message=str(event.payload.get("message", "")),
                            progress=_coerce_progress_value(event.payload.get("progress")),
                            metadata=dict(event.payload),
                        )
                    )
                )
            elif event.event_type is RuntimeEventType.TOOL_RESULT:
                payload = dict(event.payload)
                payload.pop("tool_use_id", None)
                self._buffer.append(ToolStreamItem(result=ToolResult.from_dict(payload)))

    def get_completed_results(self) -> list[ToolStreamItem]:
        items = list(self._buffer)
        self._buffer.clear()
        return items

    def get_remaining_results(self) -> list[ToolStreamItem]:
        return self.get_completed_results()

    def discard(self) -> None:
        self._buffer.clear()


def tool_call_helper(
    tool: ToolDefinition,
    tool_call: ToolCall,
    context: ToolExecutionContext,
) -> ToolResult:
    result = invoke_tool_call(tool, tool_call.arguments, context)
    if tool_supports_result_persistence(tool) and result.persisted_ref is not None:
        result.persisted_ref = persisted_ref_to_string(
            PersistedToolResultRef(ref=str(result.persisted_ref))
        )
    return result


def _coerce_progress_value(value: JsonValue | None) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _event_type_for_runtime_event(event_type: RuntimeEventType) -> ToolExecutionEventType:
    mapping = {
        RuntimeEventType.TOOL_STARTED: ToolExecutionEventType.STARTED,
        RuntimeEventType.TOOL_PROGRESS: ToolExecutionEventType.PROGRESS,
        RuntimeEventType.TOOL_RESULT: ToolExecutionEventType.RESULT,
        RuntimeEventType.TOOL_FAILED: ToolExecutionEventType.FAILED,
        RuntimeEventType.TOOL_CANCELLED: ToolExecutionEventType.CANCELLED,
    }
    return mapping[event_type]
