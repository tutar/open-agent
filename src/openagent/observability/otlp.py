"""OTLP HTTP sink and `.openagent` data projection helpers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import cast
from urllib import error, request

from openagent.object_model import JsonObject, JsonValue, RuntimeEvent, SerializableModel
from openagent.observability.models import ExternalObservabilityEvent


def _iso_to_unix_nano(timestamp: str | None) -> str:
    if timestamp is None:
        return str(int(datetime.now(UTC).timestamp() * 1_000_000_000))
    normalized = timestamp.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return str(int(parsed.timestamp() * 1_000_000_000))


def _stringify(value: JsonValue | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _flatten_attributes(payload: JsonObject) -> list[JsonObject]:
    attributes: list[JsonObject] = []
    for key, value in payload.items():
        if value is None:
            continue
        attr_value: JsonObject
        if isinstance(value, bool):
            attr_value = {"boolValue": value}
        elif isinstance(value, int):
            attr_value = {"intValue": str(value)}
        elif isinstance(value, float):
            attr_value = {"doubleValue": value}
        else:
            attr_value = {"stringValue": _stringify(cast(JsonValue, value))}
        attributes.append({"key": key, "value": attr_value})
    return attributes


@dataclass(slots=True)
class OtlpHttpConfig:
    endpoint: str
    service_name: str = "openagent"
    service_instance_id: str | None = None
    deployment_environment: str | None = None
    timeout_seconds: float = 2.0

    @classmethod
    def from_env(cls) -> OtlpHttpConfig | None:
        endpoint = os.getenv("OPENAGENT_OTLP_HTTP_ENDPOINT", "").strip()
        if not endpoint:
            return None
        return cls(
            endpoint=endpoint.rstrip("/"),
            service_name=os.getenv("OPENAGENT_OTLP_SERVICE_NAME", "openagent").strip()
            or "openagent",
            service_instance_id=os.getenv("OPENAGENT_OTLP_SERVICE_INSTANCE_ID", "").strip()
            or None,
            deployment_environment=os.getenv("OPENAGENT_OTLP_DEPLOYMENT_ENV", "").strip()
            or None,
        )


class OtlpHttpTransport:
    """Minimal OTLP/HTTP JSON transport used by local observability sinks."""

    def __init__(self, config: OtlpHttpConfig) -> None:
        self._config = config

    def post_json(self, path: str, payload: JsonObject) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            f"{self._config.endpoint}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            opener = request.build_opener(request.ProxyHandler({}))
            with opener.open(req, timeout=self._config.timeout_seconds):
                return
        except (error.URLError, TimeoutError):
            # Observability export should not break the runtime.
            return

    def resource_attributes(self, extra: JsonObject | None = None) -> list[JsonObject]:
        attrs: JsonObject = {"service.name": self._config.service_name}
        if self._config.service_instance_id is not None:
            attrs["service.instance.id"] = self._config.service_instance_id
        if self._config.deployment_environment is not None:
            attrs["deployment.environment"] = self._config.deployment_environment
        if extra:
            attrs.update(extra)
        return _flatten_attributes(attrs)


class OtelObservabilitySink:
    """Export runtime metrics, traces, and selected events over OTLP/HTTP JSON."""

    def __init__(self, config: OtlpHttpConfig) -> None:
        self._transport = OtlpHttpTransport(config)
        self._counter_totals: dict[tuple[str, str], float] = {}
        self._counter_lock = Lock()

    def emit(self, event: ExternalObservabilityEvent) -> None:
        if event.kind == "metric":
            self._emit_metric(event.payload)
            return
        if event.kind == "span_ended":
            self._emit_trace(event.payload)
            return
        if event.kind in {"progress", "session_state", "external_event"}:
            self._emit_log(event.kind, event.payload)

    def _emit_metric(self, payload: JsonObject) -> None:
        value = payload.get("value")
        if not isinstance(value, (int, float)):
            return
        instrument_kind = str(payload.get("instrument_kind", "gauge"))
        raw_attributes = payload.get("attributes")
        attributes = dict(raw_attributes) if isinstance(raw_attributes, dict) else {}
        for key in ("session_id", "task_id", "agent_id", "unit", "name"):
            if payload.get(key) is not None:
                attributes[key] = cast(JsonValue, payload.get(key))
        metric_value = float(value)
        metric_body: JsonObject
        data_point: JsonObject = {
            "timeUnixNano": _iso_to_unix_nano(cast(str | None, payload.get("timestamp"))),
            "asDouble": metric_value,
            "attributes": _flatten_attributes(attributes),
        }
        if instrument_kind == "counter":
            metric_value = self._next_counter_total(
                str(payload.get("name", "openagent_metric")),
                attributes,
                metric_value,
            )
            data_point["asDouble"] = metric_value
            metric_body = {
                "sum": {
                    "aggregationTemporality": 2,
                    "isMonotonic": True,
                    "dataPoints": [data_point],
                }
            }
        else:
            metric_body = {
                "gauge": {
                    "dataPoints": [data_point],
                }
            }
        otlp_payload: JsonObject = {
            "resourceMetrics": [
                {
                    "resource": {
                        "attributes": self._transport.resource_attributes(
                            {"openagent.signal_kind": "runtime_metric"}
                        )
                    },
                    "scopeMetrics": [
                        {
                            "scope": {"name": "openagent.observability"},
                            "metrics": [
                                {
                                    "name": str(payload.get("name", "openagent_metric")).replace(
                                        ".", "_"
                                    ),
                                    "unit": str(payload.get("unit")) if payload.get("unit") else "",
                                    **metric_body,
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        self._transport.post_json("/v1/metrics", otlp_payload)

    def _emit_trace(self, payload: JsonObject) -> None:
        raw_attributes = payload.get("attributes")
        attributes = dict(raw_attributes) if isinstance(raw_attributes, dict) else {}
        for key in (
            "span_type",
            "session_id",
            "task_id",
            "status",
            "ttft_ms",
            "input_tokens",
            "output_tokens",
            "cache_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        ):
            if payload.get(key) is not None:
                attributes[key] = cast(JsonValue, payload.get(key))
        if isinstance(payload.get("result"), dict):
            for key, value in cast(JsonObject, payload["result"]).items():
                if value is not None:
                    attributes[f"result.{key}"] = value
        span: JsonObject = {
            "traceId": str(payload.get("trace_id", "")),
            "spanId": str(payload.get("span_id", "")),
            "name": str(payload.get("span_type", "span")),
            "kind": 1,
            "startTimeUnixNano": _iso_to_unix_nano(cast(str | None, payload.get("start_time"))),
            "endTimeUnixNano": _iso_to_unix_nano(cast(str | None, payload.get("end_time"))),
            "attributes": _flatten_attributes(attributes),
        }
        parent_span_id = payload.get("parent_span_id")
        if isinstance(parent_span_id, str) and parent_span_id:
            span["parentSpanId"] = parent_span_id
        otlp_payload: JsonObject = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": self._transport.resource_attributes(
                            {"openagent.signal_kind": "trace"}
                        )
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "openagent.observability"},
                            "spans": [span],
                        }
                    ],
                }
            ]
        }
        self._transport.post_json("/v1/traces", otlp_payload)

    def _emit_log(self, kind: str, payload: JsonObject) -> None:
        body = {"kind": kind, "payload": payload}
        attributes: JsonObject = {"kind": kind}
        for key in ("session_id", "task_id", "agent_id", "scope", "state"):
            if payload.get(key) is not None:
                attributes[key] = cast(JsonValue, payload.get(key))
        otlp_payload: JsonObject = {
            "resourceLogs": [
                {
                    "resource": {
                        "attributes": self._transport.resource_attributes(
                            {"openagent.signal_kind": "log"}
                        )
                    },
                    "scopeLogs": [
                        {
                            "scope": {"name": "openagent.observability"},
                            "logRecords": [
                                {
                                    "timeUnixNano": _iso_to_unix_nano(
                                        cast(str | None, payload.get("timestamp"))
                                    ),
                                    "severityText": "INFO",
                                    "body": {
                                        "stringValue": json.dumps(
                                            body, ensure_ascii=False, sort_keys=True
                                        )
                                    },
                                    "attributes": _flatten_attributes(attributes),
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        self._transport.post_json("/v1/logs", otlp_payload)

    def _next_counter_total(
        self,
        name: str,
        attributes: JsonObject,
        increment: float,
    ) -> float:
        key = (name, json.dumps(attributes, ensure_ascii=False, sort_keys=True))
        with self._counter_lock:
            total = self._counter_totals.get(key, 0.0) + increment
            self._counter_totals[key] = total
            return total


class DataProjectionSink:
    """Project append-only `.openagent` data into OTLP logs."""

    def __init__(self, config: OtlpHttpConfig) -> None:
        self._transport = OtlpHttpTransport(config)

    def emit_transcript_entry(self, entry: JsonObject) -> None:
        self._emit_stream("conversation", entry)

    def emit_runtime_event(self, event: RuntimeEvent) -> None:
        self._emit_stream("runtime_event", event.to_dict())

    def emit_model_io_record(self, record: SerializableModel) -> None:
        self._emit_stream("model_io", record.to_dict())

    def _emit_stream(self, stream: str, payload: JsonObject) -> None:
        attributes: JsonObject = {"stream": stream}
        for key in (
            "session_id",
            "task_id",
            "turn_id",
            "agent_id",
            "event_type",
            "role",
            "model",
            "provider_adapter",
            "status",
            "capture_id",
        ):
            if payload.get(key) is not None:
                attributes[key] = cast(JsonValue, payload.get(key))
        if stream == "runtime_event":
            event_payload = payload.get("payload")
            if isinstance(event_payload, dict):
                tool_name = event_payload.get("tool_name")
                if tool_name is not None:
                    attributes["tool_name"] = cast(JsonValue, tool_name)
        body = {"stream": stream, "payload": payload}
        otlp_payload: JsonObject = {
            "resourceLogs": [
                {
                    "resource": {
                        "attributes": self._transport.resource_attributes(
                            {"openagent.signal_kind": "openagent_data"}
                        )
                    },
                    "scopeLogs": [
                        {
                            "scope": {"name": "openagent.data_projection"},
                            "logRecords": [
                                {
                                    "timeUnixNano": _iso_to_unix_nano(
                                        cast(str | None, payload.get("timestamp"))
                                    ),
                                    "severityText": "INFO",
                                    "body": {
                                        "stringValue": json.dumps(
                                            body, ensure_ascii=False, sort_keys=True
                                        )
                                    },
                                    "attributes": _flatten_attributes(attributes),
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        self._transport.post_json("/v1/logs", otlp_payload)


class NoOpDataProjectionSink:
    """Discard `.openagent` projections when OTLP export is disabled."""

    def emit_transcript_entry(self, entry: JsonObject) -> None:
        del entry

    def emit_runtime_event(self, event: RuntimeEvent) -> None:
        del event

    def emit_model_io_record(self, record: SerializableModel) -> None:
        del record


def create_otlp_observability_sink_from_env() -> OtelObservabilitySink | None:
    config = OtlpHttpConfig.from_env()
    if config is None:
        return None
    return OtelObservabilitySink(config)


def create_data_projection_sink_from_env() -> DataProjectionSink | NoOpDataProjectionSink:
    config = OtlpHttpConfig.from_env()
    if config is None:
        return NoOpDataProjectionSink()
    return DataProjectionSink(config)
