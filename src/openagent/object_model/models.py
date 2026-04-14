"""Canonical object model placeholders aligned with the agent-sdk-spec."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

from openagent.object_model.base import JsonObject, JsonValue, SerializableModel
from openagent.object_model.enums import RuntimeEventType, TerminalStatus


@dataclass(slots=True)
class RuntimeEvent(SerializableModel):
    # Keep the core event envelope flat so it matches cross-language replay artifacts.
    event_type: RuntimeEventType
    event_id: str
    timestamp: str
    session_id: str
    payload: JsonObject
    agent_id: str | None = None
    task_id: str | None = None

    @classmethod
    def from_dict(cls, data: JsonObject) -> RuntimeEvent:
        event_type = cast(str, data["event_type"])
        event_payload = data["payload"]
        return cls(
            event_type=RuntimeEventType(event_type),
            event_id=str(data["event_id"]),
            timestamp=str(data["timestamp"]),
            session_id=str(data["session_id"]),
            payload=dict(event_payload) if isinstance(event_payload, dict) else {},
            agent_id=str(data["agent_id"]) if data.get("agent_id") is not None else None,
            task_id=str(data["task_id"]) if data.get("task_id") is not None else None,
        )


@dataclass(slots=True)
class TerminalState(SerializableModel):
    status: TerminalStatus
    reason: str
    retryable: bool | None = None
    summary: str | None = None

    @classmethod
    def from_dict(cls, data: JsonObject) -> TerminalState:
        status = cast(str, data["status"])
        return cls(
            status=TerminalStatus(status),
            reason=str(data["reason"]),
            retryable=bool(data["retryable"]) if data.get("retryable") is not None else None,
            summary=str(data["summary"]) if data.get("summary") is not None else None,
        )


@dataclass(slots=True)
class RequiresAction(SerializableModel):
    action_type: str
    session_id: str
    description: str
    agent_id: str | None = None
    task_id: str | None = None
    tool_name: str | None = None
    input: JsonObject | None = None
    request_id: str | None = None


@dataclass(slots=True)
class ToolResult(SerializableModel):
    tool_name: str
    success: bool
    content: list[JsonValue]
    structured_content: JsonObject | None = None
    metadata: JsonObject | None = None
    persisted_ref: str | None = None
    truncated: bool | None = None


@dataclass(slots=True)
class CapabilityView(SerializableModel):
    # These stay as simple name lists for now; origin metadata lands in a later phase.
    tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    mcp_servers: list[str] = field(default_factory=list)
    hands: list[str] = field(default_factory=list)
    policies: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TaskRecord(SerializableModel):
    task_id: str
    type: str
    status: TerminalStatus | str
    description: str
    start_time: str
    session_id: str | None = None
    agent_id: str | None = None
    output_ref: str | None = None
    end_time: str | None = None
    metadata: JsonObject | None = None


@dataclass(slots=True)
class SchemaEnvelope(SerializableModel):
    schema_name: str
    schema_version: str
    payload: JsonObject
