"""OpenAI-compatible model adapter implementations."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import cast

from openagent.harness.models import ModelTurnRequest, ModelTurnResponse
from openagent.harness.providers.base import (
    HttpTransport,
    ProviderError,
    UrllibHttpTransport,
)
from openagent.object_model import JsonObject, JsonValue
from openagent.tools import ToolCall


@dataclass(slots=True)
class OpenAIChatCompletionsModelAdapter:
    """Call an OpenAI-compatible `/v1/chat/completions` endpoint."""

    model: str
    base_url: str
    api_key: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_seconds: float = 60.0
    transport: HttpTransport = field(default_factory=UrllibHttpTransport)

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        response = self.transport.post_json(
            self._endpoint_url(),
            self._payload(request),
            self._headers(),
            self.timeout_seconds,
        )
        return self._parse_response(response.body)

    def _endpoint_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/v1/chat/completions"

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _payload(self, request: ModelTurnRequest) -> JsonObject:
        messages_payload = self._messages_payload(request)
        payload: JsonObject = {
            "model": self.model,
            "messages": cast(JsonValue, messages_payload),
        }
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

    def _messages_payload(self, request: ModelTurnRequest) -> list[JsonObject]:
        messages = [self._message_payload(message) for message in request.messages]
        if isinstance(request.short_term_memory, dict):
            summary = str(request.short_term_memory.get("summary", "")).strip()
            if summary:
                messages.insert(
                    0,
                    {
                        "role": "system",
                        "content": f"Session continuity summary: {summary}",
                    },
                )
        for memory in request.memory_context:
            memory_summary = str(memory.get("summary", memory.get("content", "")))
            if memory_summary:
                messages.insert(
                    0,
                    {
                        "role": "system",
                        "content": f"Relevant memory: {memory_summary}",
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
