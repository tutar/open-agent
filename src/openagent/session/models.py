"""Session data structures for the TUI-first baseline."""

from __future__ import annotations

from dataclasses import dataclass, field

from openagent.object_model import JsonValue, RuntimeEvent, SerializableModel
from openagent.session.enums import SessionStatus
from openagent.tools.models import ToolCall


@dataclass(slots=True)
class SessionMessage(SerializableModel):
    role: str
    content: str


@dataclass(slots=True)
class SessionRecord(SerializableModel):
    session_id: str
    status: SessionStatus = SessionStatus.IDLE
    messages: list[SessionMessage] = field(default_factory=list)
    events: list[RuntimeEvent] = field(default_factory=list)
    pending_tool_calls: list[ToolCall] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, JsonValue]) -> SessionRecord:
        raw_messages_value = data.get("messages", [])
        raw_events_value = data.get("events", [])
        raw_pending_value = data.get("pending_tool_calls", [])
        raw_messages = raw_messages_value if isinstance(raw_messages_value, list) else []
        raw_events = raw_events_value if isinstance(raw_events_value, list) else []
        raw_pending = raw_pending_value if isinstance(raw_pending_value, list) else []
        return cls(
            session_id=str(data["session_id"]),
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
        )
