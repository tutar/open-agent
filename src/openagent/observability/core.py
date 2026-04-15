"""Core observability facade."""

from __future__ import annotations

import time
import uuid

from openagent.object_model import JsonObject
from openagent.observability.interfaces import ObservabilitySink
from openagent.observability.models import (
    ExternalObservabilityEvent,
    ProgressUpdate,
    RuntimeMetric,
    SessionStateSignal,
    SpanHandle,
    TraceSpan,
)
from openagent.observability.sinks import CompositeObservabilitySink, create_development_sink


class AgentObservability:
    """Vendor-neutral observability event router."""

    def __init__(self, sinks: list[ObservabilitySink] | None = None) -> None:
        if sinks is None:
            self._sink: ObservabilitySink = create_development_sink()
        elif len(sinks) == 1:
            self._sink = sinks[0]
        else:
            self._sink = CompositeObservabilitySink(sinks)

    def emit_runtime_metric(self, metric: RuntimeMetric) -> ExternalObservabilityEvent:
        return self._emit("metric", metric.to_dict())

    def emit_progress(self, update: ProgressUpdate) -> ExternalObservabilityEvent:
        return self._emit("progress", update.to_dict())

    def emit_session_state(self, state: SessionStateSignal) -> ExternalObservabilityEvent:
        return self._emit("session_state", state.to_dict())

    def start_span(
        self,
        span_type: str,
        attributes: JsonObject | None = None,
        *,
        parent: SpanHandle | None = None,
        session_id: str | None = None,
        task_id: str | None = None,
    ) -> SpanHandle:
        handle = SpanHandle(
            trace_id=parent.trace_id if parent is not None else uuid.uuid4().hex,
            span_id=uuid.uuid4().hex,
            span_type=span_type,
            start_time=self._now(),
            parent_span_id=parent.span_id if parent is not None else None,
            session_id=session_id,
            task_id=task_id,
            attributes=dict(attributes or {}),
        )
        self._emit("span_started", handle.to_dict())
        return handle

    def end_span(
        self,
        span_handle: SpanHandle,
        result: JsonObject | None = None,
        *,
        status: str = "completed",
        duration_ms: float | None = None,
        ttft_ms: float | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cache_tokens: int | None = None,
    ) -> TraceSpan:
        started_at = time.perf_counter()
        if duration_ms is None:
            duration_ms = None
        span = TraceSpan(
            trace_id=span_handle.trace_id,
            span_id=span_handle.span_id,
            span_type=span_handle.span_type,
            start_time=span_handle.start_time,
            end_time=self._now(),
            status=status,
            parent_span_id=span_handle.parent_span_id,
            session_id=span_handle.session_id,
            task_id=span_handle.task_id,
            duration_ms=duration_ms,
            ttft_ms=ttft_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_tokens=cache_tokens,
            attributes=dict(span_handle.attributes),
            result=dict(result or {}),
        )
        del started_at
        self._emit("span_ended", span.to_dict())
        return span

    def project_external_event(self, event: object) -> ExternalObservabilityEvent:
        if hasattr(event, "to_dict"):
            payload = event.to_dict()  # type: ignore[assignment]
        elif isinstance(event, dict):
            payload = dict(event)
        else:
            payload = {"repr": repr(event)}
        return self._emit("external_event", payload)

    def _emit(self, kind: str, payload: JsonObject) -> ExternalObservabilityEvent:
        event = ExternalObservabilityEvent(kind=kind, payload=payload)
        self._sink.emit(event)
        return event

    def _now(self) -> str:
        from openagent.observability.models import now_iso

        return now_iso()
