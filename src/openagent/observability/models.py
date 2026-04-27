"""Vendor-neutral observability models for agent runtime instrumentation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from openagent.object_model import JsonObject, SerializableModel


def now_iso() -> str:
    """Return a stable UTC timestamp for observability records."""

    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class RuntimeMetric(SerializableModel):
    """A runtime metric emitted by the harness or orchestrator."""

    name: str
    value: float
    timestamp: str = field(default_factory=now_iso)
    unit: str | None = None
    instrument_kind: str = "gauge"
    session_id: str | None = None
    task_id: str | None = None
    agent_id: str | None = None
    attributes: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class SessionStateSignal(SerializableModel):
    """A lightweight session lifecycle signal for external consumers."""

    session_id: str
    state: str
    timestamp: str = field(default_factory=now_iso)
    reason: str | None = None
    attributes: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class ProgressUpdate(SerializableModel):
    """Progress projection for turns, tasks, tools, and background agents."""

    scope: str
    timestamp: str = field(default_factory=now_iso)
    session_id: str | None = None
    task_id: str | None = None
    summary: str | None = None
    last_activity: str | None = None
    duration_ms: float | None = None
    tool_use_count: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    attributes: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class SpanHandle(SerializableModel):
    """A started span handle used to close trace spans later."""

    trace_id: str
    span_id: str
    span_type: str
    start_time: str
    parent_span_id: str | None = None
    session_id: str | None = None
    task_id: str | None = None
    attributes: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class TraceSpan(SerializableModel):
    """A completed or in-flight trace span."""

    trace_id: str
    span_id: str
    span_type: str
    start_time: str
    end_time: str | None = None
    status: str = "running"
    parent_span_id: str | None = None
    session_id: str | None = None
    task_id: str | None = None
    duration_ms: float | None = None
    ttft_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    attributes: JsonObject = field(default_factory=dict)
    result: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class ExternalObservabilityEvent(SerializableModel):
    """A sink-facing observability event."""

    kind: str
    payload: JsonObject
    timestamp: str = field(default_factory=now_iso)
