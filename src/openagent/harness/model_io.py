"""Persistent model input/output capture for training and offline analysis."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from openagent.harness.models import ModelProviderExchange, ModelTurnRequest
from openagent.object_model import JsonObject, JsonValue, SerializableModel
from openagent.object_model.base import to_json_value


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
            status="error",
            assembled_request=request.to_dict(),
            error=str(error),
        )
        self._write_record(record)

    def _write_record(self, record: ModelIoRecord) -> None:
        session_dir = self._records_dir / self._safe_path_component(record.session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        timestamp = record.timestamp.replace(":", "-")
        record_path = session_dir / f"{timestamp}-{record.capture_id}.json"
        record.record_path = str(record_path)
        serialized = self._trim_json_value(to_json_value(record))
        assert isinstance(serialized, dict)
        with self._lock:
            record_path.write_text(
                json.dumps(serialized, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            with self._index_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(serialized, ensure_ascii=False) + "\n")

    def _safe_path_component(self, value: str) -> str:
        return value.replace("/", "_").replace(":", "_")

    def _summarize_response(self, raw_response: JsonObject | None) -> JsonObject | None:
        if raw_response is None:
            return None
        summary: JsonObject = {}
        for key in ("id", "object", "model", "type", "stop_reason", "usage"):
            value = raw_response.get(key)
            if value is not None:
                summary[key] = to_json_value(value)
        content = raw_response.get("content")
        if isinstance(content, list):
            summary["content_blocks"] = len(content)
        choices = raw_response.get("choices")
        if isinstance(choices, list):
            summary["choices"] = len(choices)
        return summary or {"present": True}

    def _trim_json_value(self, value: JsonValue) -> JsonValue:
        if isinstance(value, str):
            if len(value) <= self.max_string_chars:
                return value
            return value[: self.max_string_chars] + "...[truncated]"
        if isinstance(value, list):
            return [self._trim_json_value(item) for item in value]
        if isinstance(value, dict):
            return {key: self._trim_json_value(item) for key, item in value.items()}
        return value
