"""Harness-local response and adapter models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from openagent.object_model import JsonObject, SerializableModel
from openagent.tools import ToolCall


@dataclass(slots=True)
class ModelTurnRequest(SerializableModel):
    session_id: str
    messages: list[JsonObject]
    available_tools: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ModelTurnResponse(SerializableModel):
    assistant_message: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


class ModelAdapter(Protocol):
    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        """Produce the next model response for the current turn."""
