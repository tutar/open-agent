from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, field

from openagent.harness.runtime import (
    ModelProviderExchange,
    ModelStreamEvent,
    ModelTurnRequest,
    ModelTurnResponse,
)
from openagent.local import create_file_runtime
from openagent.object_model import ToolResult
from openagent.observability.otlp import OtlpHttpTransport
from openagent.tools import ToolCall


@dataclass(slots=True)
class ToolThenReplyExchangeModel:
    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        return self.generate_with_exchange(request).response

    def generate_with_exchange(self, request: ModelTurnRequest) -> ModelProviderExchange:
        if request.messages[-1]["role"] == "user":
            return ModelProviderExchange(
                response=ModelTurnResponse(
                    tool_calls=[ToolCall(tool_name="echo", arguments={"text": "hello"})],
                    usage={
                        "input_tokens": 3,
                        "output_tokens": 5,
                        "cache_creation_input_tokens": 2,
                        "cache_read_input_tokens": 1,
                    },
                ),
                raw_response={"kind": "tool_call"},
                reasoning={"summary": "choose echo"},
            )
        return ModelProviderExchange(
            response=ModelTurnResponse(
                assistant_message="done",
                usage={"input_tokens": 4, "output_tokens": 6},
            ),
            raw_response={"kind": "assistant_message"},
            reasoning={"summary": "respond to user"},
        )


@dataclass(slots=True)
class EchoTool:
    name: str = "echo"
    input_schema: dict[str, object] = field(default_factory=lambda: {"type": "object"})

    def description(self) -> str:
        return self.name

    def call(self, arguments: dict[str, object]) -> ToolResult:
        return ToolResult(tool_name=self.name, success=True, content=[str(arguments)])

    def check_permissions(self, arguments: dict[str, object]) -> str:
        del arguments
        return "allow"

    def is_concurrency_safe(self) -> bool:
        return True


@dataclass(slots=True)
class StreamingExchangeModel:
    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        del request
        raise AssertionError("stream_generate should be used")

    def stream_generate(self, request: ModelTurnRequest) -> Iterator[ModelStreamEvent]:
        yield ModelStreamEvent(assistant_delta="hello ")
        yield ModelStreamEvent(
            assistant_delta="world",
            assistant_message="hello world",
            usage={"prompt_tokens": 3, "completion_tokens": 2},
            provider_payload={
                "model": "stream-test",
                "messages": request.messages,
                "stream": True,
                "stream_options": {"include_usage": True},
            },
            raw_provider_events=[
                {"choices": [{"delta": {"content": "hello "}}]},
                {
                    "choices": [{"delta": {"content": "world"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                },
            ],
            reasoning="deliberation",
            transport_metadata={"streaming": True},
        )


def test_local_runtime_exports_metrics_traces_and_openagent_data(tmp_path, monkeypatch) -> None:
    posted: list[tuple[str, dict[str, object]]] = []

    def _capture(self: OtlpHttpTransport, path: str, payload: dict[str, object]) -> None:
        del self
        posted.append((path, payload))

    monkeypatch.setattr(OtlpHttpTransport, "post_json", _capture)
    monkeypatch.setenv("OPENAGENT_OTLP_HTTP_ENDPOINT", "http://example.invalid")
    monkeypatch.setenv("OPENAGENT_OTLP_SERVICE_NAME", "openagent-local-e2e")
    monkeypatch.setenv("OPENAGENT_OBSERVABILITY_STDOUT", "false")

    runtime = create_file_runtime(
        model=ToolThenReplyExchangeModel(),
        session_root=str(tmp_path / ".openagent" / "sessions"),
        tools=[EchoTool()],
        openagent_root=str(tmp_path / ".openagent"),
    )

    runtime.run_turn("hello", "sess-e2e")

    metric_payloads = [payload for path, payload in posted if path == "/v1/metrics"]
    trace_payloads = [payload for path, payload in posted if path == "/v1/traces"]
    log_payloads = [payload for path, payload in posted if path == "/v1/logs"]

    assert metric_payloads
    assert trace_payloads
    assert log_payloads

    metric_names = {
        payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]["name"]
        for payload in metric_payloads
    }
    span_names = {
        payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["name"]
        for payload in trace_payloads
    }
    log_bodies = [
        payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["body"]["stringValue"]
        for payload in log_payloads
    ]
    tool_spans = [
        payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        for payload in trace_payloads
        if payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["name"] == "tool"
    ]
    tool_metrics = [
        payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]["gauge"]["dataPoints"][0]
        for payload in metric_payloads
        if payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]["name"]
        == "openagent_duration_ms"
    ]

    assert "openagent_duration_ms" in metric_names
    assert "openagent_token_usage" in metric_names
    assert "openagent_token_usage_total" in metric_names
    assert {"interaction", "llm_request", "tool"}.issubset(span_names)
    assert tool_spans and "parentSpanId" in tool_spans[0]
    assert len(tool_spans[0]["spanId"]) == 16
    assert any(
        any(
            attribute["key"] == "task_id" and attribute["value"]["stringValue"] == "turn:sess-e2e:1"
            for attribute in point["attributes"]
        )
        for point in tool_metrics
    )
    assert any('"stream": "conversation"' in body for body in log_bodies)
    assert any('"stream": "runtime_event"' in body for body in log_bodies)
    assert any('"stream": "model_io"' in body for body in log_bodies)

    os.environ.pop("OPENAGENT_OTLP_HTTP_ENDPOINT", None)


def test_local_runtime_exports_streaming_usage_and_model_io(tmp_path, monkeypatch) -> None:
    posted: list[tuple[str, dict[str, object]]] = []

    def _capture(self: OtlpHttpTransport, path: str, payload: dict[str, object]) -> None:
        del self
        posted.append((path, payload))

    monkeypatch.setattr(OtlpHttpTransport, "post_json", _capture)
    monkeypatch.setenv("OPENAGENT_OTLP_HTTP_ENDPOINT", "http://example.invalid")
    monkeypatch.setenv("OPENAGENT_OTLP_SERVICE_NAME", "openagent-local-streaming-e2e")
    monkeypatch.setenv("OPENAGENT_OBSERVABILITY_STDOUT", "false")

    openagent_root = tmp_path / ".openagent"
    runtime = create_file_runtime(
        model=StreamingExchangeModel(),
        session_root=str(openagent_root / "sessions"),
        tools=[],
        openagent_root=str(openagent_root),
    )

    runtime.run_turn("hello", "sess-stream-e2e")

    metric_payloads = [payload for path, payload in posted if path == "/v1/metrics"]
    log_payloads = [payload for path, payload in posted if path == "/v1/logs"]

    assert any(
        payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]["name"]
        == "openagent_token_usage"
        for payload in metric_payloads
    )
    assert any(
        payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]["name"]
        == "openagent_token_usage_total"
        for payload in metric_payloads
    )
    index_path = (
        openagent_root / "agent_default" / "agents" / "local-agent" / "model-io" / "index.jsonl"
    )
    rows = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["usage"] == {"prompt_tokens": 3, "completion_tokens": 2}
    assert rows[0]["provider_payload"]["stream_options"] == {"include_usage": True}
    assert any(
        '"stream": "model_io"'
        in payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["body"]["stringValue"]
        for payload in log_payloads
    )

    os.environ.pop("OPENAGENT_OTLP_HTTP_ENDPOINT", None)
