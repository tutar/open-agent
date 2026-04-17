"""Anthropic-compatible model adapter implementations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

from openagent.harness.models import (
    ModelProviderExchange,
    ModelTurnRequest,
    ModelTurnResponse,
)
from openagent.harness.providers.base import (
    HttpTransport,
    ProviderError,
    UrllibHttpTransport,
)
from openagent.object_model import JsonObject, JsonValue
from openagent.tools import ToolCall


@dataclass(slots=True)
class AnthropicMessagesModelAdapter:
    """Call an Anthropic-compatible `/v1/messages` endpoint."""

    provider_family = "anthropic"

    model: str
    base_url: str
    api_key: str | None = None
    max_tokens: int = 1024
    anthropic_version: str = "2023-06-01"
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

    def _endpoint_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/v1/messages"

    def _headers(self) -> dict[str, str]:
        headers = {"anthropic-version": self.anthropic_version}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    def _payload(self, request: ModelTurnRequest) -> JsonObject:
        system_messages: list[str] = []
        messages: list[JsonObject] = []
        if isinstance(request.short_term_memory, dict):
            summary = str(request.short_term_memory.get("summary", "")).strip()
            if summary:
                system_messages.append(f"Session continuity summary: {summary}")
        memory_blocks = [
            str(memory.get("summary", memory.get("content", "")))
            for memory in request.memory_context
            if str(memory.get("summary", memory.get("content", "")))
        ]
        if memory_blocks:
            system_messages.extend([f"Relevant memory: {item}" for item in memory_blocks])
        for message in request.messages:
            role = str(message.get("role", "user"))
            content = str(message.get("content", ""))
            metadata = message.get("metadata")
            metadata_dict = dict(metadata) if isinstance(metadata, dict) else {}
            if role == "system":
                system_messages.append(content)
                continue
            if role == "tool":
                tool_use_id = metadata_dict.get("tool_use_id")
                if tool_use_id is not None:
                    messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": str(tool_use_id),
                                    "content": content,
                                }
                            ],
                        }
                    )
                    continue
            messages.append({"role": role, "content": content})
        payload: JsonObject = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": cast(JsonValue, messages),
        }
        if system_messages:
            payload["system"] = "\n\n".join(system_messages)
        if request.tool_definitions:
            tools_payload: list[JsonObject] = [
                {
                    "name": str(tool["name"]),
                    "description": str(tool.get("description", "")),
                    "input_schema": tool.get("input_schema", {}),
                }
                for tool in request.tool_definitions
            ]
            payload["tools"] = cast(JsonValue, tools_payload)
        return payload

    def _parse_response(self, body: JsonObject) -> ModelTurnResponse:
        content = body.get("content")
        if not isinstance(content, list):
            raise ProviderError("Anthropic-compatible response did not include content blocks")
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type", ""))
            if block_type == "text":
                text_parts.append(str(block.get("text", "")))
                continue
            if block_type == "tool_use":
                raw_input = block.get("input")
                parsed_input = dict(raw_input) if isinstance(raw_input, dict) else {}
                tool_calls.append(
                    ToolCall(
                        tool_name=str(block.get("name", "")),
                        arguments=parsed_input,
                        call_id=str(block.get("id")) if block.get("id") is not None else None,
                    )
                )
        assistant_message = "".join(text_parts) or None
        usage = body.get("usage")
        return ModelTurnResponse(
            assistant_message=assistant_message,
            tool_calls=tool_calls,
            usage=dict(usage) if isinstance(usage, dict) else None,
        )

    def _extract_reasoning(self, body: JsonObject) -> JsonValue | None:
        content = body.get("content")
        if not isinstance(content, list):
            return None
        reasoning_blocks = [
            dict(block)
            for block in content
            if isinstance(block, dict) and str(block.get("type", "")) not in {"text", "tool_use"}
        ]
        if not reasoning_blocks:
            return None
        return cast(JsonValue, reasoning_blocks)
