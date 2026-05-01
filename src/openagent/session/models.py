"""Session data structures for the TUI-first baseline."""

from __future__ import annotations

from dataclasses import dataclass, field

from openagent.object_model import (
    JsonObject,
    JsonValue,
    RuntimeEvent,
    SerializableModel,
    render_tool_result_content,
)
from openagent.session.enums import SessionStatus
from openagent.tools.models import ToolCall


@dataclass(slots=True)
class SessionMessage(SerializableModel):
    role: str
    content: JsonValue
    metadata: JsonObject = field(default_factory=dict)


def session_message_text(message: SessionMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    if message.role == "tool" and isinstance(message.content, list):
        return render_tool_result_content(message.content)
    return str(message.content)


@dataclass(slots=True)
class SessionRecord(SerializableModel):
    session_id: str
    agent_id: str | None = None
    status: SessionStatus = SessionStatus.IDLE
    messages: list[SessionMessage] = field(default_factory=list)
    events: list[RuntimeEvent] = field(default_factory=list)
    pending_tool_calls: list[ToolCall] = field(default_factory=list)
    restore_marker: str | None = None
    short_term_memory: JsonObject | None = None
    metadata: JsonObject = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, JsonValue]) -> SessionRecord:
        raw_messages_value = data.get("messages", [])
        raw_events_value = data.get("events", [])
        raw_pending_value = data.get("pending_tool_calls", [])
        raw_short_term_memory = data.get("short_term_memory")
        raw_metadata = data.get("metadata")
        raw_messages = raw_messages_value if isinstance(raw_messages_value, list) else []
        raw_events = raw_events_value if isinstance(raw_events_value, list) else []
        raw_pending = raw_pending_value if isinstance(raw_pending_value, list) else []
        short_term_memory = (
            dict(raw_short_term_memory) if isinstance(raw_short_term_memory, dict) else None
        )
        return cls(
            session_id=str(data["session_id"]),
            agent_id=str(data["agent_id"]) if data.get("agent_id") is not None else None,
            status=SessionStatus(str(data.get("status", SessionStatus.IDLE.value))),
            messages=[
                SessionMessage.from_dict(message)
                for message in raw_messages
                if isinstance(message, dict)
            ],
            events=[
                RuntimeEvent.from_dict(event) for event in raw_events if isinstance(event, dict)
            ],
            pending_tool_calls=[
                ToolCall.from_dict(tool_call)
                for tool_call in raw_pending
                if isinstance(tool_call, dict)
            ],
            restore_marker=str(data["restore_marker"])
            if data.get("restore_marker") is not None
            else None,
            short_term_memory=short_term_memory,
            metadata=dict(raw_metadata) if isinstance(raw_metadata, dict) else {},
        )


@dataclass(slots=True)
class SessionCursor(SerializableModel):
    session_id: str
    event_offset: int
    last_event_id: str | None = None


@dataclass(slots=True)
class SessionCheckpoint(SerializableModel):
    session_id: str
    event_offset: int
    last_event_id: str | None = None
    cursor: SessionCursor | None = None
    committed_at: str | None = None


@dataclass(slots=True)
class WakeRequest(SerializableModel):
    session_id: str
    cursor: SessionCursor | None = None
    restore_mode: str = "latest"


@dataclass(slots=True)
class ResumeSnapshot(SerializableModel):
    session_id: str
    runtime_state: dict[str, JsonValue]
    transcript_slice: list[dict[str, JsonValue]] = field(default_factory=list)
    working_state: dict[str, JsonValue] = field(default_factory=dict)
    short_term_memory: JsonObject | None = None
