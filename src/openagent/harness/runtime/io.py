"""Runtime-local request, response, streaming, and model I/O types."""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from openagent.object_model import (
    JsonObject,
    JsonValue,
    SerializableModel,
)
from openagent.object_model.base import to_json_value
from openagent.tools import ToolCall


@dataclass(slots=True)
class ModelTurnRequest(SerializableModel):
    session_id: str
    messages: list[JsonObject]
    system_prompt: str | None = None
    prompt_sections: list[JsonObject] = field(default_factory=list)
    prompt_blocks: JsonObject | None = None
    initial_user_bootstrap: JsonObject | None = None
    available_tools: list[str] = field(default_factory=list)
    tool_definitions: list[JsonObject] = field(default_factory=list)
    short_term_memory: JsonObject | None = None
    memory_context: list[JsonObject] = field(default_factory=list)


@dataclass(slots=True)
class ModelTurnResponse(SerializableModel):
    assistant_message: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: JsonObject | None = None


@dataclass(slots=True)
class ModelProviderExchange(SerializableModel):
    response: ModelTurnResponse
    provider_payload: JsonObject | None = None
    raw_response: JsonObject | None = None
    reasoning: JsonValue | None = None
    transport_metadata: JsonObject = field(default_factory=dict)
    stream_deltas: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ModelStreamEvent(SerializableModel):
    assistant_delta: str | None = None
    assistant_message: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: JsonObject | None = None


class ModelProviderAdapter(Protocol):
    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        """Produce the next model response for the current turn."""


class ModelProviderExchangeAdapter(ModelProviderAdapter, Protocol):
    def generate_with_exchange(self, request: ModelTurnRequest) -> ModelProviderExchange:
        """Produce the next model response with provider exchange details."""


class ModelProviderStreamingAdapter(ModelProviderAdapter, Protocol):
    def stream_generate(self, request: ModelTurnRequest) -> Iterator[ModelStreamEvent]:
        """Produce streamed model events for the current turn."""


ModelAdapter = ModelProviderAdapter
StreamingModelAdapter = ModelProviderStreamingAdapter


@dataclass(slots=True)
class TurnStreamResult(SerializableModel):
    events: list[JsonObject]
    terminal_state: JsonObject


@dataclass(slots=True)
class ModelIoRecord(SerializableModel):
    capture_id: str
    timestamp: str
    session_id: str
    agent_id: str | None
    harness_instance_id: str | None
    provider_adapter: str
    provider_family: str | None
    model: str | None
    streaming: bool
    retry_index: int
    status: str
    assembled_request: JsonObject
    provider_payload: JsonObject | None = None
    provider_response_raw: JsonObject | None = None
    provider_response_summary: JsonObject | None = None
    parsed_response: JsonObject | None = None
    assistant_message: str | None = None
    tool_calls: list[JsonObject] = field(default_factory=list)
    reasoning: JsonValue | None = None
    usage: JsonObject | None = None
    stream_deltas: list[str] = field(default_factory=list)
    error: str | None = None
    record_path: str | None = None


class ModelIoCapture(Protocol):
    def capture_success(
        self,
        *,
        request: ModelTurnRequest,
        exchange: ModelProviderExchange | None,
        session_id: str,
        agent_id: str | None,
        harness_instance_id: str | None,
        provider_adapter: str,
        provider_family: str | None,
        model: str | None,
        retry_index: int,
        streaming: bool,
    ) -> None: ...

    def capture_error(
        self,
        *,
        request: ModelTurnRequest,
        session_id: str,
        agent_id: str | None,
        harness_instance_id: str | None,
        provider_adapter: str,
        provider_family: str | None,
        model: str | None,
        retry_index: int,
        streaming: bool,
        error: Exception,
    ) -> None: ...


class NoOpModelIoCapture:
    """Discard model I/O capture records."""

    def capture_success(
        self,
        *,
        request: ModelTurnRequest,
        exchange: ModelProviderExchange | None,
        session_id: str,
        agent_id: str | None,
        harness_instance_id: str | None,
        provider_adapter: str,
        provider_family: str | None,
        model: str | None,
        retry_index: int,
        streaming: bool,
    ) -> None:
        del (
            request,
            exchange,
            session_id,
            agent_id,
            harness_instance_id,
            provider_adapter,
            provider_family,
            model,
            retry_index,
            streaming,
        )

    def capture_error(
        self,
        *,
        request: ModelTurnRequest,
        session_id: str,
        agent_id: str | None,
        harness_instance_id: str | None,
        provider_adapter: str,
        provider_family: str | None,
        model: str | None,
        retry_index: int,
        streaming: bool,
        error: Exception,
    ) -> None:
        del (
            request,
            session_id,
            agent_id,
            harness_instance_id,
            provider_adapter,
            provider_family,
            model,
            retry_index,
            streaming,
            error,
        )


@dataclass(slots=True)
class FileModelIoCapture:
    """Persist every model invocation under `.openagent/data/model-io`."""

    root_dir: str | Path
    max_string_chars: int = 20000
    write_raw_response: bool = True
    write_stream_deltas: bool = True
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _root_dir: Path = field(init=False, repr=False)
    _records_dir: Path = field(init=False, repr=False)
    _index_path: Path = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._root_dir = Path(self.root_dir)
        self._records_dir = self._root_dir / "records"
        self._index_path = self._root_dir / "index.jsonl"
        self._records_dir.mkdir(parents=True, exist_ok=True)
        self._index_path.parent.mkdir(parents=True, exist_ok=True)

    def capture_success(
        self,
        *,
        request: ModelTurnRequest,
        exchange: ModelProviderExchange | None,
        session_id: str,
        agent_id: str | None,
        harness_instance_id: str | None,
        provider_adapter: str,
        provider_family: str | None,
        model: str | None,
        retry_index: int,
        streaming: bool,
    ) -> None:
        response_dict = exchange.response.to_dict() if exchange is not None else None
        tool_call_values = (
            response_dict.get("tool_calls", []) if isinstance(response_dict, dict) else []
        )
        if not isinstance(tool_call_values, list):
            tool_call_values = []
        tool_calls = [item for item in tool_call_values if isinstance(item, dict)]
        usage = response_dict.get("usage") if isinstance(response_dict, dict) else None
        assistant_message: str | None = None
        if isinstance(response_dict, dict) and response_dict.get("assistant_message") is not None:
            assistant_message = str(response_dict.get("assistant_message"))
        record = ModelIoRecord(
            capture_id=uuid.uuid4().hex,
            timestamp=datetime.now(UTC).isoformat(),
            session_id=session_id,
            agent_id=agent_id,
            harness_instance_id=harness_instance_id,
            provider_adapter=provider_adapter,
            provider_family=provider_family,
            model=model,
            streaming=streaming,
            retry_index=retry_index,
            status="completed",
            assembled_request=request.to_dict(),
            provider_payload=exchange.provider_payload if exchange is not None else None,
            provider_response_raw=(
                exchange.raw_response if exchange is not None and self.write_raw_response else None
            ),
            provider_response_summary=self._summarize_response(
                exchange.raw_response if exchange is not None else None
            ),
            parsed_response=response_dict if isinstance(response_dict, dict) else None,
            assistant_message=assistant_message,
            tool_calls=tool_calls,
            reasoning=exchange.reasoning if exchange is not None else None,
            usage=dict(usage) if isinstance(usage, dict) else None,
            stream_deltas=(
                list(exchange.stream_deltas)
                if exchange is not None and self.write_stream_deltas
                else []
            ),
        )
        self._write_record(record)

    def capture_error(
        self,
        *,
        request: ModelTurnRequest,
        session_id: str,
        agent_id: str | None,
        harness_instance_id: str | None,
        provider_adapter: str,
        provider_family: str | None,
        model: str | None,
        retry_index: int,
        streaming: bool,
        error: Exception,
    ) -> None:
        record = ModelIoRecord(
            capture_id=uuid.uuid4().hex,
            timestamp=datetime.now(UTC).isoformat(),
            session_id=session_id,
            agent_id=agent_id,
            harness_instance_id=harness_instance_id,
            provider_adapter=provider_adapter,
            provider_family=provider_family,
            model=model,
            streaming=streaming,
            retry_index=retry_index,
            status="failed",
            assembled_request=request.to_dict(),
            error=f"{type(error).__name__}: {error}",
        )
        self._write_record(record)

    def _write_record(self, record: ModelIoRecord) -> None:
        record_dir = self._records_dir / record.session_id
        record_dir.mkdir(parents=True, exist_ok=True)
        record_path = record_dir / f"{record.capture_id}.json"
        record.record_path = str(record_path)
        payload = json.dumps(record.to_dict(), indent=2, ensure_ascii=False, sort_keys=True)
        with self._lock:
            record_path.write_text(payload, encoding="utf-8")
            with self._index_path.open("a", encoding="utf-8") as index_file:
                index_file.write(json.dumps(record.to_dict(), ensure_ascii=False))
                index_file.write("\n")

    def _summarize_response(self, raw_response: JsonObject | None) -> JsonObject | None:
        if raw_response is None:
            return None
        summary = _truncate_json(raw_response, self.max_string_chars)
        return summary if isinstance(summary, dict) else None


def _truncate_json(value: JsonValue, max_chars: int) -> JsonValue:
    if isinstance(value, str):
        if len(value) <= max_chars:
            return value
        return f"{value[:max_chars]}...<truncated>"
    if isinstance(value, list):
        return [_truncate_json(item, max_chars) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _truncate_json(to_json_value(item), max_chars)
            for key, item in value.items()
        }
    return value
