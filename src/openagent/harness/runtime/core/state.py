"""Turn-local runtime state models."""

from __future__ import annotations

from dataclasses import dataclass, field

from openagent.object_model import JsonObject, SerializableModel


@dataclass(slots=True)
class TurnState(SerializableModel):
    messages: list[JsonObject] = field(default_factory=list)
    turn_count: int = 0
    transition: str = "idle"
    requires_action: bool = False
    task_id: str | None = None
    api_duration_ms: float = 0.0
