"""Harness-local runtime, response, and adapter models."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Protocol

from openagent.object_model import (
    JsonObject,
    JsonValue,
    RuntimeEvent,
    SerializableModel,
    TerminalState,
)
from openagent.tools import ToolCall


@dataclass(slots=True)
class ModelTurnRequest(SerializableModel):
    session_id: str
    messages: list[JsonObject]
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


# Backward-compatible aliases while the SDK migrates to the clearer provider-aware names.
ModelAdapter = ModelProviderAdapter
StreamingModelAdapter = ModelProviderStreamingAdapter


@dataclass(slots=True)
class TurnState(SerializableModel):
    messages: list[JsonObject] = field(default_factory=list)
    turn_count: int = 0
    transition: str = "idle"
    requires_action: bool = False


@dataclass(slots=True)
class TurnControl:
    timeout_seconds: float | None = None
    max_retries: int = 0
    cancellation_check: Callable[[], bool] | None = None


class AgentRuntime(Protocol):
    def run_turn_stream(
        self,
        input: str,
        session_handle: str,
        control: TurnControl | None = None,
    ) -> Iterator[RuntimeEvent]:
        """Advance the current turn state machine as an event stream."""

    def continue_turn(
        self,
        session_handle: str,
        approved: bool,
    ) -> tuple[list[RuntimeEvent], TerminalState]:
        """Resume a previously blocked turn after a host decision."""


class CancelledTurn(Exception):
    """Raised when cooperative cancellation stops the current turn."""


class TimedOutTurn(Exception):
    """Raised when the configured turn timeout expires."""


class RetryExhaustedTurn(Exception):
    """Raised when model retries are exhausted."""


@dataclass(slots=True)
class TurnStreamResult(SerializableModel):
    events: list[JsonObject]
    terminal_state: JsonObject
