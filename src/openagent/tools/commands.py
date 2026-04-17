"""Shared command model used by skills, MCP prompts, and review commands."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from openagent.object_model import JsonObject, JsonValue, SerializableModel


class CommandKind(StrEnum):
    PROMPT = "prompt"
    LOCAL = "local"
    LOCAL_UI = "local_ui"
    REVIEW = "review"


class CommandVisibility(StrEnum):
    USER = "user"
    MODEL = "model"
    BOTH = "both"


class ReviewCommandKind(StrEnum):
    REFLECTION = "reflection"
    CRITIQUE = "critique"
    REVIEW = "review"
    VERIFICATION = "verification"


@dataclass(slots=True)
class Command(SerializableModel):
    id: str
    name: str
    kind: CommandKind
    description: str
    visibility: CommandVisibility
    source: str
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class ReviewContext(SerializableModel):
    target_session: str
    original_task: str
    target_agent: str | None = None
    changed_artifacts: list[str] = field(default_factory=list)
    evidence_scope: list[str] = field(default_factory=list)
    review_policy: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class ReviewResult(SerializableModel):
    kind: ReviewCommandKind
    verdict: str
    evidence: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    output_ref: str | None = None


@dataclass(slots=True)
class ReviewCommand(Command):
    review_kind: ReviewCommandKind = ReviewCommandKind.REVIEW
    required_inputs: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    execution_mode: str = "orchestration"


class StaticCommandRegistry:
    """In-memory command registry with explicit invoke handlers."""

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}
        self._handlers: dict[str, Callable[[JsonObject], JsonValue]] = {}

    def register(
        self,
        command: Command,
        handler: Callable[[JsonObject], JsonValue],
    ) -> None:
        self._commands[command.id] = command
        self._handlers[command.id] = handler

    def list_commands(self) -> list[Command]:
        return list(self._commands.values())

    def resolve_command(self, command_id: str) -> Command:
        return self._commands[command_id]

    def invoke_command(self, command_id: str, args: JsonObject) -> JsonValue:
        return self._handlers[command_id](args)
