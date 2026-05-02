"""Instructor-backed provider adapter implementations."""

from __future__ import annotations

import json
import os
import instructor
from instructor.core.client import Instructor
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from functools import cache
from typing import TYPE_CHECKING, Any, Literal, Union, cast
from urllib.parse import urlparse

from openagent.harness.providers.errors import ProviderConfigurationError, ProviderError
from openagent.harness.providers.tool_results import (
    openai_tool_result_json_string,
    tool_result_content_to_anthropic,
)
from openagent.harness.runtime.io import (
    ModelProviderExchange,
    ModelStreamEvent,
    ModelTurnRequest,
    ModelTurnResponse,
)
from openagent.object_model import JsonObject, JsonValue
from openagent.tools import ToolCall

if TYPE_CHECKING:
    from pydantic import BaseModel


def _import_pydantic() -> tuple[Any, Any, Any, Any]:
    try:
        from pydantic import BaseModel, Field, TypeAdapter, create_model
    except ImportError as exc:
        raise ProviderConfigurationError(
            "The pydantic package is required for structured provider integration. "
            "Install dependencies with `pip install instructor pydantic openai anthropic`."
        ) from exc
    return BaseModel, Field, TypeAdapter, create_model


def _serialize_completion(value: object) -> JsonObject | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return cast(JsonObject, value)
    for method_name in ("model_dump", "to_dict", "dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                dumped = method(mode="json") if method_name == "model_dump" else method()
            except TypeError:
                dumped = method()
            if isinstance(dumped, dict):
                return cast(JsonObject, dumped)
    return None


def _coerce_json_object(value: object) -> JsonObject:
    if isinstance(value, dict):
        return cast(JsonObject, dict(value))
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(exclude_none=True)
        if isinstance(dumped, dict):
            return cast(JsonObject, dumped)
    return {}


def _usage_from_completion(raw_completion: JsonObject | None) -> JsonObject | None:
    if not isinstance(raw_completion, dict):
        return None
    usage = raw_completion.get("usage")
    return dict(usage) if isinstance(usage, dict) else None


def _assistant_text_from_completion(raw_completion: JsonObject | None) -> str | None:
    if not isinstance(raw_completion, dict):
        return None
    content = raw_completion.get("content")
    if isinstance(content, list):
        text_chunks = [
            str(block.get("text", "")).strip()
            for block in content
            if isinstance(block, dict) and str(block.get("type", "")) == "text"
        ]
        combined = "\n".join(chunk for chunk in text_chunks if chunk)
        return combined or None
    choices = raw_completion.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        return None
    content_value = message.get("content")
    if isinstance(content_value, str):
        text = content_value.strip()
        return text or None
    return None


def _response_model_name(tool_name: str) -> str:
    cleaned = "".join(char if char.isalnum() else "_" for char in tool_name).strip("_")
    return cleaned or "Tool"


@cache
def _action_model_bases() -> tuple[type["BaseModel"], type["BaseModel"]]:
    BaseModel, _, _, _ = _import_pydantic()

    class RespondActionModel(BaseModel):
        def to_assistant_message(self, *, strip: bool = True) -> str | None:
            message = str(getattr(self, "message", "") or "")
            if strip:
                message = message.strip()
            return message or None

    class ToolActionModel(BaseModel):
        @classmethod
        def action_tool_name(cls) -> str:
            tool_name = getattr(cls, "tool_name", None)
            if not isinstance(tool_name, str) or not tool_name.strip():
                raise ProviderError("Instructor returned a tool action without a mapped tool name")
            return tool_name

        def to_tool_call(self) -> ToolCall:
            arguments = _coerce_json_object(self)
            arguments.pop("call_id", None)
            call_id = getattr(self, "call_id", None)
            return ToolCall(
                tool_name=self.action_tool_name(),
                arguments=arguments,
                call_id=str(call_id) if call_id else None,
            )

    return RespondActionModel, ToolActionModel


@dataclass(slots=True)
class ActionModels:
    response_model: object
    respond_model: type["BaseModel"]
    tool_models: dict[str, type["BaseModel"]] = field(default_factory=dict)
    parser: "StructuredActionParser | None" = None


@dataclass(slots=True)
class ToolSchemaCompiler:
    def python_type_for_schema(
        self,
        schema: Mapping[str, object],
        *,
        model_prefix: str,
    ) -> Any:
        BaseModel, Field, _, create_model = _import_pydantic()
        schema_type = schema.get("type")
        enum_values = schema.get("enum")
        if isinstance(enum_values, list) and enum_values:
            return Literal[tuple(enum_values)]
        if schema_type == "string":
            return str
        if schema_type == "integer":
            return int
        if schema_type == "number":
            return float
        if schema_type == "boolean":
            return bool
        if schema_type == "array":
            items = schema.get("items")
            if isinstance(items, dict):
                return list[
                    self.python_type_for_schema(
                        items,
                        model_prefix=f"{model_prefix}Item",
                    )
                ]
            return list[Any]
        if schema_type == "object" or isinstance(schema.get("properties"), dict):
            properties = schema.get("properties")
            if not isinstance(properties, dict):
                return dict[str, Any]
            required = {
                str(item)
                for item in schema.get("required", [])
                if isinstance(item, str)
            }
            fields: dict[str, tuple[Any, object]] = {}
            for field_name, raw_field_schema in properties.items():
                if not isinstance(field_name, str) or not isinstance(raw_field_schema, dict):
                    continue
                field_type = self.python_type_for_schema(
                    raw_field_schema,
                    model_prefix=f"{model_prefix}{_response_model_name(field_name)}",
                )
                description = raw_field_schema.get("description")
                default: object = ...
                annotated_type = field_type
                if field_name not in required:
                    annotated_type = field_type | None
                    default = None
                fields[field_name] = (
                    annotated_type,
                    Field(
                        default=default,
                        description=str(description) if isinstance(description, str) else None,
                    ),
                )
            return create_model(f"{model_prefix}Model", __base__=BaseModel, **fields)
        return Any


@dataclass(slots=True)
class StructuredActionParser:
    def action_kind(self, value: object) -> str | None:
        respond_base, tool_base = _action_model_bases()
        if isinstance(value, respond_base):
            return "respond"
        if isinstance(value, tool_base):
            return "tool"
        if hasattr(value, "to_assistant_message"):
            return "respond"
        if hasattr(value, "to_tool_call"):
            return "tool"
        return cast(str | None, getattr(value.__class__, "__openagent_action_kind__", None))

    def iter_actions(self, structured: object) -> list[object]:
        if isinstance(structured, list):
            return [item for item in structured if item is not None]
        tasks = getattr(structured, "tasks", None)
        if isinstance(tasks, list):
            return [item for item in tasks if item is not None]
        return [structured]

    def assistant_message(self, value: object, *, strip: bool = True) -> str | None:
        method = getattr(value, "to_assistant_message", None)
        if callable(method):
            return cast(str | None, method(strip=strip))
        message = str(getattr(value, "message", "") or "")
        if strip:
            message = message.strip()
        return message or None

    def tool_call(self, value: object) -> ToolCall:
        method = getattr(value, "to_tool_call", None)
        if callable(method):
            return cast(ToolCall, method())
        tool_name = getattr(value.__class__, "__openagent_tool_name__", None)
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ProviderError("Instructor returned a tool action without a mapped tool name")
        arguments = _coerce_json_object(value)
        arguments.pop("call_id", None)
        call_id = getattr(value, "call_id", None)
        return ToolCall(
            tool_name=tool_name,
            arguments=arguments,
            call_id=str(call_id) if call_id else None,
        )

    def response_from_action(
        self,
        structured: object,
        completion: object,
    ) -> ModelTurnResponse:
        usage = _usage_from_completion(_serialize_completion(completion))
        actions = self.iter_actions(structured)
        respond_actions = [action for action in actions if self.action_kind(action) == "respond"]
        tool_actions = [action for action in actions if self.action_kind(action) == "tool"]
        if respond_actions and tool_actions:
            raise ProviderError("Instructor returned both respond and tool actions in a single turn")
        if respond_actions:
            return ModelTurnResponse(
                assistant_message=self.assistant_message(respond_actions[-1], strip=True),
                usage=usage,
            )
        if tool_actions:
            return ModelTurnResponse(
                tool_calls=[self.tool_call(tool_actions[-1])],
                usage=usage,
            )
        raise ProviderError("Instructor returned an action that OpenAgent could not classify")


@dataclass(slots=True)
class ActionModelRegistry:
    compiler: ToolSchemaCompiler = field(default_factory=ToolSchemaCompiler)
    parser: StructuredActionParser = field(default_factory=StructuredActionParser)

    def build(self, tool_definitions: Iterable[JsonObject]) -> ActionModels:
        _, Field, _, create_model = _import_pydantic()
        respond_base, tool_base = _action_model_bases()
        respond_model = create_model(
            "Respond",
            __base__=respond_base,
            message=(
                str,
                Field(
                    ...,
                    description="Final assistant message shown to the user when no tool call is needed.",
                ),
            ),
        )
        tool_models: dict[str, type[BaseModel]] = {}
        for raw_tool in tool_definitions:
            tool_name = str(raw_tool.get("name", "")).strip()
            if not tool_name:
                continue
            input_schema = raw_tool.get("input_schema")
            arguments_schema = input_schema if isinstance(input_schema, dict) else {"type": "object"}
            arguments_model = self.compiler.python_type_for_schema(
                cast(Mapping[str, object], arguments_schema),
                model_prefix=f"{_response_model_name(tool_name)}Arguments",
            )
            fields: dict[str, tuple[Any, object]] = {}
            if isinstance(arguments_model, type) and hasattr(arguments_model, "model_fields"):
                for field_name, model_field in arguments_model.model_fields.items():
                    annotation = model_field.annotation or Any
                    default: object = ...
                    if not model_field.is_required():
                        default = model_field.default
                    fields[field_name] = (
                        annotation,
                        Field(default=default, description=model_field.description),
                    )
            tool_model = create_model(
                f"{_response_model_name(tool_name)}Action",
                __base__=tool_base,
                **fields,
            )
            setattr(tool_model, "tool_name", tool_name)
            tool_models[tool_name] = tool_model
        action_types: list[type[BaseModel]] = [respond_model, *tool_models.values()]
        union_model: object
        if len(action_types) == 1:
            union_model = action_types[0]
        else:
            union_model = Union[tuple(action_types)]  # type: ignore[arg-type]
        from instructor.dsl.iterable import IterableModel

        return ActionModels(
            response_model=IterableModel(union_model),
            respond_model=respond_model,
            tool_models=tool_models,
            parser=self.parser,
        )


def build_action_models(tool_definitions: Iterable[JsonObject]) -> ActionModels:
    return ActionModelRegistry().build(tool_definitions)


def build_action_response_model(tool_definitions: Iterable[JsonObject]) -> object:
    return build_action_models(tool_definitions).response_model


def _recover_completion_from_exception(exc: Exception) -> JsonObject | None:
    failed_attempts = getattr(exc, "failed_attempts", None)
    if not isinstance(failed_attempts, list) or not failed_attempts:
        return None
    for attempt in reversed(failed_attempts):
        completion = getattr(attempt, "completion", None)
        raw_completion = _serialize_completion(completion)
        if raw_completion is not None:
            return raw_completion
    return None


@dataclass(slots=True)
class InstructorModelAdapter:
    """Provider adapter that uses Instructor for structured turn extraction."""

    model: str
    base_url: str
    api_key: str | None = None
    provider: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_seconds: float = 60.0
    instructor_client: object | None = None
    _cached_client: object | None = field(default=None, init=False, repr=False)

    @property
    def provider_family(self) -> str:
        return self._resolved_provider()

    @property
    def tool_result_prompt_hint(self) -> str | None:
        return "openai_tool_json_string" if self.provider_family == "openai" else None

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        return self.generate_with_exchange(request).response

    def generate_with_exchange(self, request: ModelTurnRequest) -> ModelProviderExchange:
        payload = self._payload(request, stream=False)
        action_models = build_action_models(request.tool_definitions)
        parser = action_models.parser or StructuredActionParser()
        client = self._client()
        try:
            structured, completion = client.create_with_completion(
                response_model=action_models.response_model,
                max_retries=2,
                **payload,
            )
            parsed_response = parser.response_from_action(structured, completion)
            raw_completion = _serialize_completion(completion)
        except Exception as exc:
            raw_completion = _recover_completion_from_exception(exc)
            assistant_message = _assistant_text_from_completion(raw_completion)
            if assistant_message is None:
                raise
            parsed_response = ModelTurnResponse(
                assistant_message=assistant_message,
                usage=_usage_from_completion(raw_completion),
            )
        return ModelProviderExchange(
            response=parsed_response,
            provider_payload=payload,
            raw_response=raw_completion,
            reasoning=self._extract_reasoning(raw_completion),
            transport_metadata={"via_instructor": True},
        )

    def stream_generate(self, request: ModelTurnRequest) -> Iterator[ModelStreamEvent]:
        payload = self._payload(request, stream=True)
        action_models = build_action_models(request.tool_definitions)
        parser = action_models.parser or StructuredActionParser()
        client = self._client()
        create_partial = getattr(client, "create_partial", None)
        if not callable(create_partial):
            raise ProviderConfigurationError("Instructor client does not expose create_partial")
        partials = create_partial(
            response_model=action_models.response_model,
            max_retries=2,
            **payload,
        )
        previous_message = ""
        final_message: str | None = None
        final_tool_calls: list[ToolCall] = []
        for partial in partials:
            actions = parser.iter_actions(partial)
            if not actions:
                continue
            respond_actions = [action for action in actions if parser.action_kind(action) == "respond"]
            tool_actions = [action for action in actions if parser.action_kind(action) == "tool"]
            if respond_actions:
                current_message = parser.assistant_message(respond_actions[-1], strip=False) or ""
                if current_message and current_message.startswith(previous_message):
                    delta = current_message[len(previous_message) :]
                    if delta:
                        yield ModelStreamEvent(assistant_delta=delta)
                elif current_message and current_message != previous_message:
                    yield ModelStreamEvent(assistant_delta=current_message)
                if current_message:
                    previous_message = current_message
                    final_message = current_message
                continue
            if tool_actions:
                final_tool_calls = [parser.tool_call(tool_actions[-1])]
        if final_message is None and not final_tool_calls:
            fallback_exchange = self.generate_with_exchange(request)
            yield ModelStreamEvent(
                assistant_message=fallback_exchange.response.assistant_message,
                tool_calls=fallback_exchange.response.tool_calls,
                usage=fallback_exchange.response.usage,
                provider_payload=fallback_exchange.provider_payload,
                raw_provider_events=[],
                reasoning=fallback_exchange.reasoning,
                transport_metadata={
                    "streaming": True,
                    "via_instructor": True,
                    "stream_fallback": "generate_with_exchange",
                },
            )
            return
        yield ModelStreamEvent(
            assistant_message=final_message,
            tool_calls=final_tool_calls,
            provider_payload=payload,
            raw_provider_events=[],
            reasoning=None,
            transport_metadata={"streaming": True, "via_instructor": True},
        )

    def _resolved_provider(self) -> str:
        if self.provider is not None:
            return self.provider
        configured = os.getenv("OPENAGENT_PROVIDER", "").strip().lower()
        if configured in {"openai", "anthropic"}:
            return configured
        return "openai"

    def _client(self) -> Instructor:
        if self.instructor_client is not None:
            return self.instructor_client
        if self._cached_client is not None:
            return self._cached_client
        provider_model = f"{self._resolved_provider()}/{self.model}"
        kwargs: dict[str, object] = {
            "base_url": self.base_url,
            "api_key": self._effective_api_key(),
            "timeout": self.timeout_seconds,
        }
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        self._cached_client = instructor.from_provider(
            model=provider_model,
            async_client=False,
            mode=instructor.Mode.TOOLS,
            cache=None,
            **kwargs,
        )
        return self._cached_client

    def _effective_api_key(self) -> str | None:
        if self.api_key:
            return self.api_key
        if self.provider_family != "openai":
            return None
        hostname = (urlparse(self.base_url).hostname or "").strip().lower()
        if hostname in {"127.0.0.1", "localhost"}:
            # Local OpenAI-compatible endpoints often ignore auth, but the OpenAI SDK
            # still requires a non-empty api_key during client construction.
            return "openagent-local"
        return None

    def _payload(self, request: ModelTurnRequest, *, stream: bool) -> JsonObject:
        if self.provider_family == "anthropic":
            return self._anthropic_payload(request, stream=stream)
        return self._openai_payload(request, stream=stream)

    def _openai_payload(self, request: ModelTurnRequest, *, stream: bool) -> JsonObject:
        messages_payload = self._openai_messages_payload(request)
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
        return payload

    def _openai_messages_payload(self, request: ModelTurnRequest) -> list[JsonObject]:
        messages: list[JsonObject] = []
        system_fragments: list[str] = []
        if request.system_prompt:
            system_fragments.append(request.system_prompt)
        if isinstance(request.short_term_memory, dict):
            summary = str(request.short_term_memory.get("summary", "")).strip()
            if summary:
                system_fragments.append(f"Session continuity summary: {summary}")
        memory_blocks = [
            str(memory.get("summary", memory.get("content", "")))
            for memory in request.memory_context
            if str(memory.get("summary", memory.get("content", "")))
        ]
        if memory_blocks:
            system_fragments.extend([f"Relevant memory: {item}" for item in memory_blocks])
        for message in request.messages:
            role = str(message.get("role", "user"))
            content = message.get("content", "")
            metadata = message.get("metadata")
            metadata_dict = dict(metadata) if isinstance(metadata, dict) else {}
            if role == "system":
                system_fragments.append(str(content))
                continue
            if role == "tool":
                payload: JsonObject = {
                    "role": "tool",
                    "content": openai_tool_result_json_string(
                        content=cast(JsonValue, content),
                        metadata=metadata_dict,
                    ),
                }
                tool_call_id = metadata_dict.get("tool_use_id")
                if tool_call_id is not None:
                    payload["tool_call_id"] = str(tool_call_id)
                messages.append(payload)
                continue
            messages.append({"role": role, "content": str(content)})
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
        if not any(str(message.get("role", "")) == "user" for message in messages):
            raise ProviderError("model request is missing a user message after context compaction")
        return messages

    def _anthropic_payload(self, request: ModelTurnRequest, *, stream: bool) -> JsonObject:
        del stream
        system_messages: list[str] = []
        messages: list[JsonObject] = []
        if request.system_prompt:
            system_messages.append(request.system_prompt)
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
            content = message.get("content", "")
            metadata = message.get("metadata")
            metadata_dict = dict(metadata) if isinstance(metadata, dict) else {}
            if role == "system":
                system_messages.append(str(content))
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
                                    "content": tool_result_content_to_anthropic(
                                        cast(JsonValue, content)
                                    ),
                                }
                            ],
                        }
                    )
                    continue
            messages.append({"role": role, "content": str(content)})
        payload: JsonObject = {
            "model": self.model,
            "messages": cast(JsonValue, messages),
        }
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        if system_messages:
            payload["system"] = "\n\n".join(system_messages)
        return payload

    def _extract_reasoning(self, raw_completion: JsonObject | None) -> JsonValue | None:
        if not isinstance(raw_completion, dict):
            return None
        if self.provider_family == "anthropic":
            content = raw_completion.get("content")
            if not isinstance(content, list):
                return None
            reasoning_blocks = [
                dict(block)
                for block in content
                if isinstance(block, dict) and str(block.get("type", "")) not in {"text", "tool_use"}
            ]
            return cast(JsonValue, reasoning_blocks) if reasoning_blocks else None
        choices = raw_completion.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            return None
        for key in ("reasoning", "reasoning_content", "thinking"):
            value = message.get(key)
            if value is not None:
                return cast(JsonValue, value)
        return None

__all__ = [
    "InstructorModelAdapter",
    "build_action_models",
    "build_action_response_model",
]
