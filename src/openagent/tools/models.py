"""Tool execution models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from openagent.object_model import JsonObject, SerializableModel


class PermissionDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass(slots=True)
class ToolCall(SerializableModel):
    tool_name: str
    arguments: JsonObject = field(default_factory=dict)
    call_id: str | None = None


@dataclass(slots=True)
class ToolExecutionContext(SerializableModel):
    session_id: str
    approved_tool_names: list[str] = field(default_factory=list)
