"""Local task models used by the harness task subsystem."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from openagent.object_model import JsonObject, JsonValue, SerializableModel


class LocalTaskKind(StrEnum):
    GENERIC = "generic"
    BACKGROUND = "background"
    VERIFIER = "verifier"


class VerificationVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    PARTIAL = "partial"


@dataclass(slots=True)
class BackgroundTaskHandle(SerializableModel):
    task_id: str
    task_kind: LocalTaskKind
    description: str
    detached: bool = False
    session_id: str | None = None
    agent_id: str | None = None
    status: str | None = None
    output_ref: str | None = None
    checkpoints: list[JsonObject] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: JsonObject) -> BackgroundTaskHandle:
        checkpoints = data.get("checkpoints")
        status = data.get("status")
        output_ref = data.get("output_ref")
        return cls(
            task_id=str(data["task_id"]),
            task_kind=LocalTaskKind(str(data["task_kind"])),
            description=str(data["description"]),
            detached=bool(data.get("detached", False)),
            session_id=str(data["session_id"]) if data.get("session_id") is not None else None,
            agent_id=str(data["agent_id"]) if data.get("agent_id") is not None else None,
            status=str(status) if isinstance(status, str) else None,
            output_ref=str(output_ref) if isinstance(output_ref, str) else None,
            checkpoints=[item for item in checkpoints if isinstance(item, dict)]
            if isinstance(checkpoints, list)
            else [],
        )


@dataclass(slots=True)
class VerifierTaskHandle(SerializableModel):
    task_id: str
    description: str
    verification_kind: str = "verification"
    session_id: str | None = None
    agent_id: str | None = None
    status: str | None = None
    output_ref: str | None = None

    @classmethod
    def from_dict(cls, data: JsonObject) -> VerifierTaskHandle:
        status = data.get("status")
        output_ref = data.get("output_ref")
        return cls(
            task_id=str(data["task_id"]),
            description=str(data["description"]),
            verification_kind=str(data.get("verification_kind", "verification")),
            session_id=str(data["session_id"]) if data.get("session_id") is not None else None,
            agent_id=str(data["agent_id"]) if data.get("agent_id") is not None else None,
            status=str(status) if isinstance(status, str) else None,
            output_ref=str(output_ref) if isinstance(output_ref, str) else None,
        )


@dataclass(slots=True)
class BackgroundTaskContext:
    task_id: str
    _checkpoint: Callable[[str, JsonObject], None]
    _emit_progress: Callable[[str, JsonObject], None]
    _append_output: Callable[[str, JsonValue], int]

    def checkpoint(self, payload: JsonObject) -> None:
        self._checkpoint(self.task_id, payload)

    def progress(self, payload: JsonObject) -> None:
        self._emit_progress(self.task_id, payload)

    def output(self, payload: JsonValue) -> int:
        return self._append_output(self.task_id, payload)


@dataclass(slots=True)
class TaskOutputSlice(SerializableModel):
    task_id: str
    output_ref: str | None
    cursor: int
    items: list[JsonValue] = field(default_factory=list)
    done: bool = False


@dataclass(slots=True)
class TaskEventSlice(SerializableModel):
    task_id: str
    cursor: int
    events: list[JsonObject] = field(default_factory=list)
    done: bool = False


@dataclass(slots=True)
class TaskSelector(SerializableModel):
    status: str | None = None
    type: str | None = None
    session_id: str | None = None
    agent_id: str | None = None


@dataclass(slots=True)
class TaskRetentionPolicy(SerializableModel):
    grace_period_seconds: float = 30.0
    evict_terminal_without_observers: bool = True
    evict_output_with_state: bool = False


@dataclass(slots=True)
class VerificationRequest(SerializableModel):
    target_session: str
    original_task: str
    prompt: str
    changed_artifacts: list[str] = field(default_factory=list)
    evidence_scope: list[str] = field(default_factory=list)
    review_policy: JsonObject = field(default_factory=dict)
    source_command_id: str | None = None


@dataclass(slots=True)
class VerificationResult(SerializableModel):
    verdict: VerificationVerdict
    summary: str
    evidence: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    task_id: str | None = None
    output_ref: str | None = None
    metadata: JsonObject | None = None

