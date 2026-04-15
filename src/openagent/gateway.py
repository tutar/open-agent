"""Gateway and local session adapter baselines."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from openagent.harness import SimpleHarness
from openagent.object_model import JsonObject, RuntimeEvent, SerializableModel
from openagent.session import SessionCheckpoint


@dataclass(slots=True)
class ChannelIdentity(SerializableModel):
    channel_type: str
    user_id: str | None = None
    conversation_id: str | None = None
    device_id: str | None = None


@dataclass(slots=True)
class InboundEnvelope(SerializableModel):
    channel_identity: JsonObject
    input_kind: str
    payload: JsonObject
    delivery_metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class NormalizedInboundMessage(SerializableModel):
    channel: str
    conversation_id: str
    sender_id: str | None
    message_id: str | None
    content: str
    attachments: list[JsonObject] = field(default_factory=list)
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class SessionBinding(SerializableModel):
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
    channel: str
    conversation_id: str
    session_id: str
    event: JsonObject


@dataclass(slots=True)
class LocalSessionHandle(SerializableModel):
    session_id: str
    done: bool = False
    activities: list[str] = field(default_factory=list)
    current_activity: str | None = None


class ChannelAdapter(Protocol):
    channel_type: str

    def accepted_event_types(self) -> list[str]:
        """Return runtime event types that should be projected to the frontend."""


class SessionBindingStore(Protocol):
    def save_binding(self, binding: SessionBinding) -> None:
        """Persist a gateway session binding."""

    def load_binding(self, channel_type: str, conversation_id: str) -> SessionBinding | None:
        """Load a binding for the given frontend conversation."""


class FileSessionBindingStore:
    """Persist gateway session bindings for local restart-safe frontend recovery."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def save_binding(self, binding: SessionBinding) -> None:
        path = self._binding_path(
            str(binding.channel_identity["channel_type"]),
            binding.conversation_id,
        )
        path.write_text(
            json.dumps(binding.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def load_binding(self, channel_type: str, conversation_id: str) -> SessionBinding | None:
        path = self._binding_path(channel_type, conversation_id)
        if not path.exists():
            return None
        return SessionBinding.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def _binding_path(self, channel_type: str, conversation_id: str) -> Path:
        safe_name = f"{channel_type}__{conversation_id}".replace("/", "_")
        return self._root / f"{safe_name}.json"


class InProcessSessionAdapter:
    """Expose a local harness as a gateway-managed session runtime."""

    def __init__(self, runtime: SimpleHarness) -> None:
        self._runtime = runtime
        self._handles: dict[str, LocalSessionHandle] = {}

    def spawn(self, session_id: str) -> LocalSessionHandle:
        handle = self._handles.setdefault(session_id, LocalSessionHandle(session_id=session_id))
        return handle

    def write_input(self, session_handle: str, input_text: str) -> list[RuntimeEvent]:
        handle = self.spawn(session_handle)
        handle.current_activity = "turn"
        handle.activities.append("turn")
        events, _ = self._runtime.run_turn(input_text, session_handle)
        handle.done = True
        handle.current_activity = None
        return events

    def observe(self, session_handle: str, after: int = 0) -> list[RuntimeEvent]:
        return self._runtime.sessions.read_events(session_handle, after=after)

    def continue_session(self, session_handle: str, approved: bool) -> list[RuntimeEvent]:
        handle = self.spawn(session_handle)
        handle.current_activity = "continuation"
        handle.activities.append("continuation")
        events, _ = self._runtime.continue_turn(session_handle, approved=approved)
        handle.done = True
        handle.current_activity = None
        return events

    def kill(self, session_handle: str) -> None:
        handle = self.spawn(session_handle)
        handle.done = True
        handle.current_activity = "killed"

    def get_checkpoint(self, session_handle: str) -> SessionCheckpoint:
        return self._runtime.sessions.get_checkpoint(session_handle)

    def get_restore_marker(self, session_handle: str) -> str | None:
        session = self._runtime.sessions.load_session(session_handle)
        return getattr(session, "restore_marker", None)


class Gateway:
    """Local channel gateway for terminal/desktop frontends."""

    def __init__(
        self,
        session_adapter: InProcessSessionAdapter,
        binding_store: SessionBindingStore | None = None,
    ) -> None:
        self._session_adapter = session_adapter
        self._binding_store = binding_store
        self._bindings: dict[tuple[str, str], SessionBinding] = {}

    def receive_input(self, inbound_envelope: InboundEnvelope) -> NormalizedInboundMessage:
        channel_identity = inbound_envelope.channel_identity
        conversation_id = str(channel_identity.get("conversation_id", "default"))
        sender_id = channel_identity.get("user_id")
        payload = inbound_envelope.payload
        attachments = payload.get("attachments", [])
        normalized_attachments = (
            [item for item in attachments if isinstance(item, dict)]
            if isinstance(attachments, list)
            else []
        )
        return NormalizedInboundMessage(
            channel=str(channel_identity["channel_type"]),
            conversation_id=conversation_id,
            sender_id=str(sender_id) if sender_id is not None else None,
            message_id=str(inbound_envelope.delivery_metadata.get("message_id"))
            if inbound_envelope.delivery_metadata.get("message_id") is not None
            else None,
            content=str(payload.get("content", "")),
            attachments=normalized_attachments,
            metadata=inbound_envelope.delivery_metadata,
        )

    def bind_session(
        self,
        channel_identity: ChannelIdentity,
        session_identity: str,
        event_types: list[str] | None = None,
        adapter_name: str | None = None,
    ) -> SessionBinding:
        conversation_id = channel_identity.conversation_id or "default"
        existing = self._bindings.get((channel_identity.channel_type, conversation_id))
        if existing is not None and existing.session_id != session_identity:
            raise ValueError("A chat can only be bound to one session")
        binding = SessionBinding(
            channel_identity=channel_identity.to_dict(),
            conversation_id=conversation_id,
            session_id=session_identity,
            adapter_name=adapter_name,
            event_types=list(event_types or []),
        )
        self._bindings[(channel_identity.channel_type, conversation_id)] = binding
        self._session_adapter.spawn(session_identity)
        self._sync_binding_checkpoint(binding)
        if self._binding_store is not None:
            self._binding_store.save_binding(binding)
        return binding

    def route_control(self, control_message: JsonObject) -> JsonObject:
        subtype = str(control_message.get("subtype", "unknown"))
        return {
            "subtype": subtype,
            "accepted": subtype
            in {"interrupt", "resume", "permission_response", "mode_change"},
        }

    def project_egress(
        self,
        runtime_event: RuntimeEvent,
        binding: SessionBinding,
    ) -> EgressEnvelope | None:
        if binding.event_types and runtime_event.event_type.value not in binding.event_types:
            return None
        channel_type = str(binding.channel_identity["channel_type"])
        return EgressEnvelope(
            channel=channel_type,
            conversation_id=binding.conversation_id,
            session_id=binding.session_id,
            event=runtime_event.to_dict(),
        )

    def observe_session(
        self,
        channel_identity: ChannelIdentity,
        after: int = 0,
    ) -> list[EgressEnvelope]:
        conversation_id = channel_identity.conversation_id or "default"
        binding = self._get_binding(channel_identity.channel_type, conversation_id)
        events = self._session_adapter.observe(binding.session_id, after=after)
        return self._project_many(events, binding)

    def process_user_message(self, inbound_envelope: InboundEnvelope) -> list[EgressEnvelope]:
        normalized = self.receive_input(inbound_envelope)
        binding_key = (normalized.channel, normalized.conversation_id)
        binding = self._get_binding(*binding_key)
        events = self._session_adapter.write_input(binding.session_id, normalized.content)
        return self._project_many(events, binding)

    def process_input(self, inbound_envelope: InboundEnvelope) -> list[EgressEnvelope]:
        input_kind = inbound_envelope.input_kind
        if input_kind in {"user_message", "supplement_input"}:
            return self.process_user_message(inbound_envelope)
        if input_kind == "control":
            channel = ChannelIdentity.from_dict(inbound_envelope.channel_identity)
            return self.process_control_message(channel, inbound_envelope.payload)
        return []

    def process_control_message(
        self,
        channel_identity: ChannelIdentity,
        control_message: JsonObject,
    ) -> list[EgressEnvelope]:
        route_result = self.route_control(control_message)
        if not bool(route_result["accepted"]):
            return []

        conversation_id = channel_identity.conversation_id or "default"
        binding = self._get_binding(channel_identity.channel_type, conversation_id)
        subtype = str(control_message["subtype"])

        if subtype == "permission_response":
            approved = bool(control_message.get("approved", False))
            events = self._session_adapter.continue_session(binding.session_id, approved=approved)
            return self._project_many(events, binding)

        if subtype == "interrupt":
            self._session_adapter.kill(binding.session_id)
            return []

        return []

    def _get_binding(self, channel_type: str, conversation_id: str) -> SessionBinding:
        binding = self._bindings.get((channel_type, conversation_id))
        if binding is not None:
            return binding
        if self._binding_store is not None:
            restored = self._binding_store.load_binding(channel_type, conversation_id)
            if restored is not None:
                self._bindings[(channel_type, conversation_id)] = restored
                self._session_adapter.spawn(restored.session_id)
                return restored
        raise KeyError((channel_type, conversation_id))

    def _project_many(
        self,
        events: list[RuntimeEvent],
        binding: SessionBinding,
    ) -> list[EgressEnvelope]:
        self._sync_binding_checkpoint(binding)
        projected: list[EgressEnvelope] = []
        for event in events:
            envelope = self.project_egress(event, binding)
            if envelope is not None:
                projected.append(envelope)
        self._persist_binding(binding)
        return projected

    def _sync_binding_checkpoint(self, binding: SessionBinding) -> None:
        checkpoint = self._session_adapter.get_checkpoint(binding.session_id)
        binding.checkpoint_event_offset = checkpoint.event_offset
        binding.checkpoint_last_event_id = checkpoint.last_event_id
        binding.restore_marker = self._session_adapter.get_restore_marker(binding.session_id)

    def _persist_binding(self, binding: SessionBinding) -> None:
        if self._binding_store is not None:
            self._binding_store.save_binding(binding)
