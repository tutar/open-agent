"""Local multi-agent object model."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from openagent.object_model import JsonObject, JsonValue, SerializableModel


class InterAgentChannel(StrEnum):
    TASK_NOTIFICATION = "task_notification"
    DIRECT_VIEW_INPUT = "direct_view_input"
    MAILBOX = "mailbox"


@dataclass(slots=True)
class DelegatedAgentInvocation(SerializableModel):
    prompt: str
    agent_type: str = "delegate"
    description: str | None = None
    run_in_background: bool = False
    parent_session_id: str | None = None
    invoking_request_id: str | None = None
    parent_task_id: str | None = None
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class DelegatedAgentIdentity(SerializableModel):
    agent_id: str
    agent_type: str
    parent_agent_ref: str | None = None
    parent_session_id: str | None = None
    invoking_request_id: str | None = None
    invocation_kind: str = "spawn"
    workspace: str | None = None


@dataclass(slots=True)
class InterAgentMessage(SerializableModel):
    channel: InterAgentChannel | str
    sender: JsonObject
    recipient: JsonObject
    payload: JsonObject = field(default_factory=dict)
    summary: str | None = None
    team: str | None = None
    timestamp: str | None = None


@dataclass(slots=True)
class TaskNotificationEnvelope(SerializableModel):
    recipient_id: str
    task_id: str
    event_type: str
    summary: str
    payload: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class DirectViewInput(SerializableModel):
    recipient_id: str
    content: str
    sender_id: str | None = None
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class ViewedTranscriptEntry(SerializableModel):
    source: str
    kind: str
    payload: JsonValue


@dataclass(slots=True)
class ViewedTranscript(SerializableModel):
    task_id: str
    entries: list[ViewedTranscriptEntry] = field(default_factory=list)
    retained: bool = False
