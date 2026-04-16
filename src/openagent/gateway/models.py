"""Serializable gateway envelope and binding models."""

from __future__ import annotations

from dataclasses import dataclass, field

from openagent.object_model import HarnessInstance, JsonObject, SerializableModel


@dataclass(slots=True)
class ChannelIdentity(SerializableModel):
    """Identify a frontend channel instance and conversation."""

    channel_type: str
    user_id: str | None = None
    conversation_id: str | None = None
    device_id: str | None = None


@dataclass(slots=True)
class InboundEnvelope(SerializableModel):
    """Raw input delivered by a channel into the gateway."""

    channel_identity: JsonObject
    input_kind: str
    payload: JsonObject
    delivery_metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class NormalizedInboundMessage(SerializableModel):
    """Gateway-normalized user-facing message content."""

    channel: str
    conversation_id: str
    sender_id: str | None
    message_id: str | None
    content: str
    attachments: list[JsonObject] = field(default_factory=list)
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class SessionBinding(SerializableModel):
    """Persisted one-chat-to-one-session binding."""

    channel_identity: JsonObject
    conversation_id: str
    session_id: str
    agent_id: str | None = None
    adapter_name: str | None = None
    event_types: list[str] = field(default_factory=list)
    checkpoint_event_offset: int = 0
    checkpoint_last_event_id: str | None = None
    restore_marker: str | None = None

    @classmethod
    def from_dict(cls, data: JsonObject) -> SessionBinding:
        """Rebuild a binding from persisted storage."""

        channel_identity = data.get("channel_identity")
        event_types = data.get("event_types")
        raw_checkpoint_offset = data.get("checkpoint_event_offset", 0)
        checkpoint_event_offset = (
            int(raw_checkpoint_offset)
            if isinstance(raw_checkpoint_offset, int | float | str)
            else 0
        )
        return cls(
            channel_identity=dict(channel_identity) if isinstance(channel_identity, dict) else {},
            conversation_id=str(data["conversation_id"]),
            session_id=str(data["session_id"]),
            agent_id=str(data["agent_id"]) if data.get("agent_id") is not None else None,
            adapter_name=(
                str(data["adapter_name"]) if data.get("adapter_name") is not None else None
            ),
            event_types=[str(item) for item in event_types if isinstance(item, str)]
            if isinstance(event_types, list)
            else [],
            checkpoint_event_offset=checkpoint_event_offset,
            checkpoint_last_event_id=(
                str(data["checkpoint_last_event_id"])
                if data.get("checkpoint_last_event_id") is not None
                else None
            ),
            restore_marker=str(data["restore_marker"])
            if data.get("restore_marker") is not None
            else None,
        )


@dataclass(slots=True)
class EgressEnvelope(SerializableModel):
    """Projected event leaving the gateway toward a channel."""

    channel: str
    conversation_id: str
    session_id: str
    event: JsonObject


@dataclass(slots=True)
class LocalSessionHandle(SerializableModel):
    """Minimal in-process session runtime handle."""

    session_id: str
    harness_instance: HarnessInstance | None = None
    done: bool = False
    activities: list[str] = field(default_factory=list)
    current_activity: str | None = None
