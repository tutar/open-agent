"""Sandbox request, result, and capability models."""

from __future__ import annotations

from dataclasses import dataclass, field

from openagent.object_model import JsonObject, SerializableModel


@dataclass(slots=True)
class SandboxExecutionRequest(SerializableModel):
    command: list[str]
    env: JsonObject = field(default_factory=dict)
    cwd: str | None = None


@dataclass(slots=True)
class SandboxExecutionResult(SerializableModel):
    exit_code: int
    stdout: str = ""
    stderr: str = ""


@dataclass(slots=True)
class SandboxCapabilityView(SerializableModel):
    supports_network: bool = False
    supports_filesystem_write: bool = False
    allowed_command_prefixes: list[str] = field(default_factory=list)
