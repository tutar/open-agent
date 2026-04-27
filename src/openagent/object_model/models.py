"""Canonical object model placeholders aligned with the agent-spec."""

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
    terminal_state: str = "requires_action"
    resumable: bool = True
    agent_id: str | None = None
    task_id: str | None = None
    tool_name: str | None = None
    input: JsonObject | None = None
    request_id: str | None = None
    action_ref: str | None = None
    policy_decision_id: str | None = None


@dataclass(slots=True)
class HarnessInstance(SerializableModel):
    harness_instance_id: str
    agent_id: str
    gateway_id: str
    status: str
    session_id: str | None = None
    runtime_state_ref: str | None = None
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class SessionHarnessLease(SerializableModel):
    session_id: str
    harness_instance_id: str
    agent_id: str
    acquired_at: str
    lease_state: str = "active"
    resume_token: str | None = None


@dataclass(slots=True)
class ShortTermMemoryRef(SerializableModel):
    session_id: str
    memory_id: str
    scope: str = "session"


@dataclass(slots=True)
class AgentLongTermMemoryRef(SerializableModel):
    agent_id: str
    memory_id: str
    scope: str = "agent"


@dataclass(slots=True)
class PolicyDecision(SerializableModel):
    decision_id: str
    outcome: str
    target: str
    reason: str | None = None
    session_id: str | None = None
    agent_id: str | None = None
    metadata: JsonObject = field(default_factory=dict)


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
    parent_task_id: str | None = None
    output_ref: str | None = None
    output_cursor: int | str | None = None
    end_time: str | None = None
    terminal_state: JsonObject | None = None
    notified: bool = False
    metadata: JsonObject | None = None

    @classmethod
    def from_dict(cls, data: JsonObject) -> TaskRecord:
        raw_status = str(data["status"])
        try:
            status: TerminalStatus | str = TerminalStatus(raw_status)
        except ValueError:
            status = raw_status
        metadata = data.get("metadata")
        terminal_state = data.get("terminal_state")
        raw_output_cursor = data.get("output_cursor")
        output_cursor: int | str | None
        if isinstance(raw_output_cursor, (int, str)):
            output_cursor = raw_output_cursor
        else:
            output_cursor = None
        return cls(
            task_id=str(data["task_id"]),
            type=str(data["type"]),
            status=status,
            description=str(data["description"]),
            start_time=str(data["start_time"]),
            session_id=str(data["session_id"]) if data.get("session_id") is not None else None,
            agent_id=str(data["agent_id"]) if data.get("agent_id") is not None else None,
            parent_task_id=(
                str(data["parent_task_id"]) if data.get("parent_task_id") is not None else None
            ),
            output_ref=str(data["output_ref"]) if data.get("output_ref") is not None else None,
            output_cursor=output_cursor,
            end_time=str(data["end_time"]) if data.get("end_time") is not None else None,
            terminal_state=(
                dict(terminal_state) if isinstance(terminal_state, dict) else None
            ),
            notified=bool(data.get("notified", False)),
            metadata=dict(metadata) if isinstance(metadata, dict) else None,
        )


@dataclass(slots=True)
class TaskEvent(SerializableModel):
    task_id: str
    event_id: str
    timestamp: str
    type: str
    payload: JsonObject
    terminal_state: JsonObject | None = None
    error: JsonObject | None = None

    @classmethod
    def from_dict(cls, data: JsonObject) -> TaskEvent:
        payload = data.get("payload")
        terminal_state = data.get("terminal_state")
        error = data.get("error")
        return cls(
            task_id=str(data["task_id"]),
            event_id=str(data["event_id"]),
            timestamp=str(data["timestamp"]),
            type=str(data["type"]),
            payload=dict(payload) if isinstance(payload, dict) else {},
            terminal_state=dict(terminal_state) if isinstance(terminal_state, dict) else None,
            error=dict(error) if isinstance(error, dict) else None,
        )


@dataclass(slots=True)
class SchemaEnvelope(SerializableModel):
    schema_name: str
    schema_version: str
    payload: JsonObject
