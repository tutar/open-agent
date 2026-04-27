from __future__ import annotations

from dataclasses import dataclass

from openagent.harness.runtime import ModelIoRecord
from openagent.object_model import RuntimeEvent, RuntimeEventType
from openagent.observability import (
    AgentObservability,
    DataProjectionSink,
    OtelObservabilitySink,
    OtlpHttpConfig,
    RuntimeMetric,
    SessionStateSignal,
)
from openagent.observability.otlp import OtlpHttpTransport


@dataclass(slots=True)
class _PostedPayload:
    path: str
    payload: dict[str, object]


def test_otlp_observability_sink_emits_metric_trace_and_log(monkeypatch) -> None:
    posted: list[_PostedPayload] = []

    def _capture(self: OtlpHttpTransport, path: str, payload: dict[str, object]) -> None:
        del self
        posted.append(_PostedPayload(path=path, payload=payload))

    monkeypatch.setattr(OtlpHttpTransport, "post_json", _capture)
    sink = OtelObservabilitySink(OtlpHttpConfig(endpoint="http://example.invalid"))
    observability = AgentObservability([sink])

    observability.emit_runtime_metric(
        RuntimeMetric(
            name="openagent_duration_ms",
            value=12.5,
            session_id="sess-1",
            task_id="turn:sess-1:1",
            attributes={"scope": "turn", "metric_kind": "total_duration_ms"},
        )
    )
    span = observability.start_span(
        "interaction",
        {"scope": "turn"},
        session_id="sess-1",
        task_id="turn:sess-1:1",
    )
    observability.end_span(span, {"reason": "assistant_message"}, duration_ms=12.5)
    observability.emit_session_state(
        SessionStateSignal(
            session_id="sess-1",
            state="idle",
            reason="assistant_message",
        )
    )

    paths = [item.path for item in posted]
    assert "/v1/metrics" in paths
    assert "/v1/traces" in paths
    assert "/v1/logs" in paths

    metric_payload = next(item.payload for item in posted if item.path == "/v1/metrics")
    trace_payload = next(item.payload for item in posted if item.path == "/v1/traces")
    log_payload = next(item.payload for item in posted if item.path == "/v1/logs")

    metric_name = metric_payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]["name"]
    trace_span = trace_payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    trace_name = trace_span["name"]
    log_body = (
        log_payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["body"]["stringValue"]
    )

    assert metric_name == "openagent_duration_ms"
    assert trace_name == "interaction"
    assert len(trace_span["spanId"]) == 16
    assert len(trace_span["traceId"]) == 32
    assert '"kind": "session_state"' in log_body


def test_otlp_observability_sink_accumulates_counter_metrics(monkeypatch) -> None:
    posted: list[_PostedPayload] = []

    def _capture(self: OtlpHttpTransport, path: str, payload: dict[str, object]) -> None:
        del self
        posted.append(_PostedPayload(path=path, payload=payload))

    monkeypatch.setattr(OtlpHttpTransport, "post_json", _capture)
    sink = OtelObservabilitySink(OtlpHttpConfig(endpoint="http://example.invalid"))
    observability = AgentObservability([sink])

    observability.emit_runtime_metric(
        RuntimeMetric(
            name="openagent_token_usage_total",
            value=3.0,
            instrument_kind="counter",
            session_id="sess-1",
            attributes={"scope": "llm_request", "token_type": "input_tokens"},
        )
    )
    observability.emit_runtime_metric(
        RuntimeMetric(
            name="openagent_token_usage_total",
            value=5.0,
            instrument_kind="counter",
            session_id="sess-1",
            attributes={"scope": "llm_request", "token_type": "input_tokens"},
        )
    )

    metric_payloads = [item.payload for item in posted if item.path == "/v1/metrics"]
    first_metric = metric_payloads[0]["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]
    second_metric = metric_payloads[1]["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]

    assert "sum" in first_metric
    assert first_metric["sum"]["isMonotonic"] is True
    assert first_metric["sum"]["dataPoints"][0]["asDouble"] == 3.0
    assert second_metric["sum"]["dataPoints"][0]["asDouble"] == 8.0


def test_data_projection_sink_emits_conversation_runtime_event_and_model_io(monkeypatch) -> None:
    posted: list[_PostedPayload] = []

    def _capture(self: OtlpHttpTransport, path: str, payload: dict[str, object]) -> None:
        del self
        posted.append(_PostedPayload(path=path, payload=payload))

    monkeypatch.setattr(OtlpHttpTransport, "post_json", _capture)
    sink = DataProjectionSink(OtlpHttpConfig(endpoint="http://example.invalid"))
    sink.emit_transcript_entry(
        {
            "session_id": "sess-1",
            "turn_id": "turn:sess-1:1",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "role": "assistant",
            "content": "done",
            "metadata": {},
        }
    )
    sink.emit_runtime_event(
        RuntimeEvent(
            event_type=RuntimeEventType.TOOL_STARTED,
            event_id="tool_started:1",
            timestamp="2026-01-01T00:00:01+00:00",
            session_id="sess-1",
            task_id="turn:sess-1:1",
            payload={"tool_name": "Bash", "tool_use_id": "call-1"},
        )
    )
    sink.emit_model_io_record(
        ModelIoRecord(
            capture_id="cap-1",
            timestamp="2026-01-01T00:00:02+00:00",
            session_id="sess-1",
            agent_id="local-agent",
            harness_instance_id="runtime-1",
            provider_adapter="StubAdapter",
            provider_family="stub",
            model="stub-model",
            streaming=False,
            retry_index=0,
            status="completed",
            assembled_request={"messages": []},
            reasoning={"summary": "reasoning"},
            usage={"input_tokens": 3, "output_tokens": 5},
        )
    )

    bodies = [
        item.payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["body"]["stringValue"]
        for item in posted
        if item.path == "/v1/logs"
    ]

    assert any('"stream": "conversation"' in body for body in bodies)
    assert any('"stream": "runtime_event"' in body for body in bodies)
    assert any('"stream": "model_io"' in body for body in bodies)
