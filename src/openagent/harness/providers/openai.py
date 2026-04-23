"""OpenAI-compatible model adapter implementations."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import cast

from openagent.harness.providers.base import (
    HttpTransport,
    ProviderError,
    UrllibHttpTransport,
)
from openagent.harness.runtime.io import (
    ModelProviderExchange,
    ModelStreamEvent,
    ModelTurnRequest,
    ModelTurnResponse,
)
from openagent.object_model import JsonObject, JsonValue
from openagent.tools import ToolCall


@dataclass(slots=True)
class OpenAIChatCompletionsModelAdapter:
    """Call an OpenAI-compatible `/v1/chat/completions` endpoint."""

    provider_family = "openai"

    model: str
    base_url: str
    api_key: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_seconds: float = 60.0
    transport: HttpTransport = field(default_factory=UrllibHttpTransport)

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        return self.generate_with_exchange(request).response

    def generate_with_exchange(self, request: ModelTurnRequest) -> ModelProviderExchange:
        payload = self._payload(request)
        response = self.transport.post_json(
            self._endpoint_url(),
            payload,
            self._headers(),
            self.timeout_seconds,
        )
        parsed = self._parse_response(response.body)
        return ModelProviderExchange(
            response=parsed,
            provider_payload=payload,
            raw_response=response.body,
            reasoning=self._extract_reasoning(response.body),
            transport_metadata={"status_code": response.status_code},
        )

    def stream_generate(self, request: ModelTurnRequest) -> Iterator[ModelStreamEvent]:
        payload = self._payload(request, stream=True)
        tool_call_parts: dict[int, JsonObject] = {}
        aggregated_message = ""
        usage: JsonObject | None = None
        for event in self._iter_sse_events(
            self.transport.post_json_stream(
                self._endpoint_url(),
                payload,
                self._headers(),
                self.timeout_seconds,
            )
        ):
            choices = event.get("choices")
            if isinstance(choices, list):
                for raw_choice in choices:
                    if not isinstance(raw_choice, dict):
                        continue
                    delta = raw_choice.get("delta")
                    if not isinstance(delta, dict):
                        continue
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        aggregated_message += content
                        yield ModelStreamEvent(assistant_delta=content)
                    raw_tool_calls = delta.get("tool_calls")
                    if isinstance(raw_tool_calls, list):
                        self._accumulate_tool_call_deltas(tool_call_parts, raw_tool_calls)
            raw_usage = event.get("usage")
            if isinstance(raw_usage, dict):
                usage = dict(raw_usage)

        yield ModelStreamEvent(
            assistant_message=aggregated_message or None,
            tool_calls=self._finalize_stream_tool_calls(tool_call_parts),
            usage=usage,
        )

    def _endpoint_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/v1/chat/completions"

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _payload(self, request: ModelTurnRequest, *, stream: bool = False) -> JsonObject:
        messages_payload = self._messages_payload(request)
        payload: JsonObject = {
            "model": self.model,
            "messages": cast(JsonValue, messages_payload),
        }
        if stream:
            payload["stream"] = True
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        if request.tool_definitions:
            tools_payload: list[JsonObject] = [
                {
                    "type": "function",
                    "function": {
                        "name": str(tool["name"]),
                        "description": str(tool.get("description", "")),
                        "parameters": tool.get("input_schema", {}),
                    },
                }
                for tool in request.tool_definitions
            ]
            payload["tools"] = cast(JsonValue, tools_payload)
            payload["tool_choice"] = "auto"
        return payload

    def _iter_sse_events(self, lines: Iterator[str]) -> Iterator[JsonObject]:
        event_lines: list[str] = []
        for raw_line in lines:
            line = raw_line.rstrip("\r\n")
            if not line:
                if event_lines:
                    payload = "\n".join(event_lines)
                    event_lines = []
                    if payload == "[DONE]":
                        break
                    yield self._parse_sse_json(payload)
                continue
            if not line.startswith("data:"):
                continue
            event_lines.append(line.removeprefix("data:").strip())
        if event_lines:
            payload = "\n".join(event_lines)
            if payload != "[DONE]":
                yield self._parse_sse_json(payload)

    def _parse_sse_json(self, payload: str) -> JsonObject:
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ProviderError("Invalid OpenAI streaming JSON payload") from exc
        if not isinstance(parsed, dict):
            raise ProviderError("OpenAI streaming payload must be a JSON object")
        return parsed

    def _accumulate_tool_call_deltas(
        self,
        tool_call_parts: dict[int, JsonObject],
        raw_tool_calls: Sequence[JsonValue],
    ) -> None:
        for raw_item in raw_tool_calls:
            if not isinstance(raw_item, dict):
                continue
            index = raw_item.get("index")
            if not isinstance(index, int):
                continue
            entry = tool_call_parts.setdefault(
                index,
                {"id": None, "name": "", "arguments_text": ""},
            )
            if raw_item.get("id") is not None:
                entry["id"] = str(raw_item["id"])
            function = raw_item.get("function")
            if isinstance(function, dict):
                if function.get("name") is not None:
                    entry["name"] = str(function["name"])
                arguments = function.get("arguments")
                if isinstance(arguments, str):
                    entry["arguments_text"] = str(entry.get("arguments_text", "")) + arguments

    def _finalize_stream_tool_calls(self, tool_call_parts: dict[int, JsonObject]) -> list[ToolCall]:
        tool_calls: list[ToolCall] = []
        for index in sorted(tool_call_parts):
            entry = tool_call_parts[index]
            arguments_text = str(entry.get("arguments_text", "")).strip()
            parsed_arguments: JsonObject = {}
            if arguments_text:
                try:
                    loaded = json.loads(arguments_text)
                except json.JSONDecodeError as exc:
                    raise ProviderError("Invalid OpenAI streaming tool arguments JSON") from exc
                if isinstance(loaded, dict):
                    parsed_arguments = loaded
            tool_calls.append(
                ToolCall(
                    tool_name=str(entry.get("name", "")),
                    arguments=parsed_arguments,
                    call_id=str(entry["id"]) if entry.get("id") is not None else None,
                )
            )
        return tool_calls

    def _messages_payload(self, request: ModelTurnRequest) -> list[JsonObject]:
        system_fragments: list[str] = []
        if request.system_prompt:
            system_fragments.append(request.system_prompt)
        if isinstance(request.short_term_memory, dict):
            summary = str(request.short_term_memory.get("summary", "")).strip()
            if summary:
                system_fragments.append(f"Session continuity summary: {summary}")
        for memory in request.memory_context:
            memory_summary = str(memory.get("summary", memory.get("content", "")))
            if memory_summary:
                system_fragments.append(f"Relevant memory: {memory_summary}")
        messages: list[JsonObject] = []
        for message in request.messages:
            role = str(message.get("role", "user"))
            if role == "system":
                content = str(message.get("content", "")).strip()
                if content:
                    system_fragments.append(content)
                continue
            messages.append(self._message_payload(message))
        if system_fragments:
            messages.insert(
                0,
                {
                    "role": "system",
                    "content": "\n\n".join(
                        fragment.strip() for fragment in system_fragments if fragment.strip()
                    ),
                },
            )
        return messages

    def _message_payload(self, message: JsonObject) -> JsonObject:
        role = str(message.get("role", "user"))
        content = str(message.get("content", ""))
        metadata = message.get("metadata")
        metadata_dict = dict(metadata) if isinstance(metadata, dict) else {}
        if role == "tool":
            payload: JsonObject = {
                "role": "tool",
                "content": content,
            }
            tool_call_id = metadata_dict.get("tool_use_id")
            if tool_call_id is not None:
                payload["tool_call_id"] = str(tool_call_id)
            return payload
        return {"role": role, "content": content}

    def _parse_response(self, body: JsonObject) -> ModelTurnResponse:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError("OpenAI-compatible response did not include choices")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ProviderError("OpenAI-compatible choice must be an object")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ProviderError("OpenAI-compatible choice.message must be an object")
        tool_calls = self._parse_tool_calls(message.get("tool_calls"))
        assistant_message = message.get("content")
        usage = body.get("usage")
        return ModelTurnResponse(
            assistant_message=str(assistant_message) if assistant_message is not None else None,
            tool_calls=tool_calls,
            usage=dict(usage) if isinstance(usage, dict) else None,
        )

    def _parse_tool_calls(self, raw_tool_calls: object) -> list[ToolCall]:
        if not isinstance(raw_tool_calls, list):
            return []
        tool_calls: list[ToolCall] = []
        for raw_item in raw_tool_calls:
            if not isinstance(raw_item, dict):
                continue
            function = raw_item.get("function")
            if not isinstance(function, dict):
                continue
            arguments = function.get("arguments", "{}")
            parsed_arguments: JsonObject = {}
            if isinstance(arguments, str):
                try:
                    loaded = json.loads(arguments)
                except json.JSONDecodeError as exc:
                    raise ProviderError("Invalid OpenAI tool arguments JSON") from exc
                if isinstance(loaded, dict):
                    parsed_arguments = loaded
            elif isinstance(arguments, dict):
                parsed_arguments = dict(arguments)
            tool_calls.append(
                ToolCall(
                    tool_name=str(function.get("name", "")),
                    arguments=parsed_arguments,
                    call_id=str(raw_item.get("id")) if raw_item.get("id") is not None else None,
                )
            )
        return tool_calls

    def _extract_reasoning(self, body: JsonObject) -> JsonValue | None:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        first = choices[0]
        if not isinstance(first, dict):
            return None
        message = first.get("message")
        if not isinstance(message, dict):
            return None
        for key in ("reasoning", "reasoning_content", "thinking"):
            value = message.get(key)
            if value is not None:
                return cast(JsonValue, value)
        content = message.get("content")
        if isinstance(content, list):
            reasoning_blocks = [
                item
                for item in content
                if isinstance(item, dict) and str(item.get("type", "")).startswith("reason")
            ]
            if reasoning_blocks:
                return cast(JsonValue, reasoning_blocks)
        return None
