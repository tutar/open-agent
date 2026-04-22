"""Local task models used by the harness task baseline."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from openagent.object_model import JsonObject, SerializableModel


class LocalTaskKind(StrEnum):
    GENERIC = "generic"
    BACKGROUND = "background"
    VERIFIER = "verifier"


@dataclass(slots=True)
class BackgroundTaskHandle(SerializableModel):
    task_id: str
    task_kind: LocalTaskKind
    description: str
    detached: bool = False
    checkpoints: list[JsonObject] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: JsonObject) -> BackgroundTaskHandle:
        checkpoints = data.get("checkpoints")
        return cls(
            task_id=str(data["task_id"]),
            task_kind=LocalTaskKind(str(data["task_kind"])),
            description=str(data["description"]),
            detached=bool(data.get("detached", False)),
            checkpoints=[item for item in checkpoints if isinstance(item, dict)]
            if isinstance(checkpoints, list)
            else [],
        )


@dataclass(slots=True)
class BackgroundTaskContext:
    task_id: str
    _checkpoint: Callable[[str, JsonObject], None]
    _emit_progress: Callable[[str, JsonObject], None]

    def checkpoint(self, payload: JsonObject) -> None:
        self._checkpoint(self.task_id, payload)

    def progress(self, payload: JsonObject) -> None:
        self._emit_progress(self.task_id, payload)
