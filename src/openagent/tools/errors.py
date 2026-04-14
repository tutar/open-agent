"""Tool execution exceptions."""

from __future__ import annotations

from dataclasses import dataclass

from openagent.object_model import RequiresAction, SerializableModel


@dataclass(slots=True)
class RequiresActionError(Exception, SerializableModel):
    requires_action: RequiresAction

    def __str__(self) -> str:
        return self.requires_action.description


@dataclass(slots=True)
class ToolPermissionDeniedError(Exception, SerializableModel):
    tool_name: str
    reason: str

    def __str__(self) -> str:
        return f"{self.tool_name}: {self.reason}"
