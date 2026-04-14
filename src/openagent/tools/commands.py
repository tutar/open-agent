"""Shared command model used by skills and MCP prompts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from openagent.object_model import JsonObject, JsonValue, SerializableModel


class CommandKind(StrEnum):
    PROMPT = "prompt"
    LOCAL = "local"
    LOCAL_UI = "local_ui"


class CommandVisibility(StrEnum):
    USER = "user"
    MODEL = "model"
    BOTH = "both"


@dataclass(slots=True)
class Command(SerializableModel):
    id: str
    name: str
    kind: CommandKind
    description: str
    visibility: CommandVisibility
    source: str
    metadata: JsonObject = field(default_factory=dict)


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
