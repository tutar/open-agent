"""Short-term session memory models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from openagent.object_model import SerializableModel


@dataclass(slots=True)
class ShortTermSessionMemory(SerializableModel):
    session_id: str
    summary: str
    current_goal: str | None = None
    progress: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    coverage_boundary: int | None = None
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    stable: bool = True


@dataclass(slots=True)
class ShortTermMemoryUpdateResult(SerializableModel):
    memory: ShortTermSessionMemory | None = None
    scheduled: bool = False
    stable: bool = False
