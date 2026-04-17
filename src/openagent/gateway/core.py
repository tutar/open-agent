"""Gateway core for local multi-channel access into harness sessions."""

from __future__ import annotations

from openagent.object_model import JsonObject, RuntimeEvent
from openagent.observability import AgentObservability

from .control import route_control_message
from .interfaces import ChannelAdapter, SessionAdapter, SessionBindingStore
from .models import (
    ChannelIdentity,
    EgressEnvelope,
    InboundEnvelope,
    NormalizedInboundMessage,
    SessionBinding,
)
from .projector import project_runtime_event


class Gateway:
    """Channel gateway for terminal and chat-style frontends."""

    def __init__(
        self,
        session_adapter: SessionAdapter,
        binding_store: SessionBindingStore | None = None,
        observability: AgentObservability | None = None,
    ) -> None:
        self._session_adapter = session_adapter
        self._binding_store = binding_store
        self._bindings: dict[tuple[str, str], SessionBinding] = {}
        self._channel_adapters: dict[str, ChannelAdapter] = {}
        self._observability = observability

    def register_channel(self, adapter: ChannelAdapter) -> None:
        """Register channel preferences used when creating a binding."""

        self._channel_adapters[adapter.channel_type] = adapter
        if self._observability is not None:
            self._observability.project_external_event(
                {
                    "event": "gateway.channel_registered",
                    "channel_type": adapter.channel_type,
                }
            )

    def get_channel_adapter(self, channel_type: str) -> ChannelAdapter:
        """Return a previously registered channel adapter."""

        return self._channel_adapters[channel_type]

    def receive_input(self, inbound_envelope: InboundEnvelope) -> NormalizedInboundMessage:
        """Normalize channel input into the runtime-facing message shape."""

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
        """Bind one chat to exactly one session."""

        conversation_id = channel_identity.conversation_id or "default"
        existing = self._bindings.get((channel_identity.channel_type, conversation_id))
        if existing is not None and existing.session_id != session_identity:
            raise ValueError("A chat can only be bound to one session")

        if event_types is not None:
            resolved_event_types = list(event_types)
        else:
            resolved_event_types = self._default_event_types(channel_identity.channel_type)
        binding = SessionBinding(
            channel_identity=channel_identity.to_dict(),
            conversation_id=conversation_id,
            session_id=session_identity,
            adapter_name=adapter_name,
            event_types=resolved_event_types,
        )
        self._bindings[(channel_identity.channel_type, conversation_id)] = binding
        self._session_adapter.spawn(session_identity)
        self._sync_binding_checkpoint(binding)
        self._persist_binding(binding)
        if self._observability is not None:
            self._observability.project_external_event(
                {
                    "event": "gateway.session_bound",
                    "channel_type": channel_identity.channel_type,
                    "conversation_id": conversation_id,
                    "session_id": session_identity,
                }
            )
        return binding

    def get_binding(self, channel_type: str, conversation_id: str) -> SessionBinding:
        """Return an in-memory or restored binding."""

        return self._get_binding(channel_type, conversation_id)

    def route_control(self, control_message: JsonObject) -> JsonObject:
        """Expose control routing for tests and channel adapters."""

        return route_control_message(control_message)

    def project_egress(
        self,
        runtime_event: RuntimeEvent,
        binding: SessionBinding,
    ) -> EgressEnvelope | None:
        """Project a runtime event for a bound channel."""

        return project_runtime_event(runtime_event, binding)

    def observe_session(
        self,
        channel_identity: ChannelIdentity,
        after: int = 0,
    ) -> list[EgressEnvelope]:
        """Observe a session from an explicit event offset."""

        conversation_id = channel_identity.conversation_id or "default"
        binding = self._get_binding(channel_identity.channel_type, conversation_id)
        events = self._session_adapter.observe(binding.session_id, after=after)
        return self._project_many(events, binding)

    def resume_bound_session(
        self,
        channel_identity: ChannelIdentity,
        after: int | None = None,
    ) -> list[EgressEnvelope]:
        """Replay the currently bound session for channel resume/reconnect."""

        conversation_id = channel_identity.conversation_id or "default"
        binding = self._get_binding(channel_identity.channel_type, conversation_id)
        event_offset = binding.checkpoint_event_offset if after is None else after
        events = self._session_adapter.observe(binding.session_id, after=event_offset)
        return self._project_many(events, binding)

    def process_user_message(self, inbound_envelope: InboundEnvelope) -> list[EgressEnvelope]:
        """Append a user message into the currently bound session."""

        normalized = self.receive_input(inbound_envelope)
        if self._observability is not None:
            self._observability.project_external_event(
                {
                    "event": "gateway.user_message",
                    "channel": normalized.channel,
                    "conversation_id": normalized.conversation_id,
                    "message_id": normalized.message_id,
                }
            )
        binding = self._get_binding(normalized.channel, normalized.conversation_id)
        events = self._session_adapter.write_input(binding.session_id, normalized.content)
        return self._project_many(events, binding)

    def process_input(self, inbound_envelope: InboundEnvelope) -> list[EgressEnvelope]:
        """Route inbound channel input by input kind."""

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
        """Route non-text channel control messages."""

        route_result = self.route_control(control_message)
        if not bool(route_result["accepted"]):
            return []

        conversation_id = channel_identity.conversation_id or "default"
        binding = self._get_binding(channel_identity.channel_type, conversation_id)
        subtype = str(control_message["subtype"])

        if subtype == "permission_response":
            approved = bool(control_message.get("approved", False))
            if self._observability is not None:
                self._observability.project_external_event(
                    {
                        "event": "gateway.control",
                        "subtype": subtype,
                        "conversation_id": conversation_id,
                        "approved": approved,
                    }
                )
            events = self._session_adapter.continue_session(binding.session_id, approved=approved)
            return self._project_many(events, binding)

        if subtype == "interrupt":
            if self._observability is not None:
                self._observability.project_external_event(
                    {
                        "event": "gateway.control",
                        "subtype": subtype,
                        "conversation_id": conversation_id,
                    }
                )
            self._session_adapter.kill(binding.session_id)
            return []

        if subtype == "resume":
            after = control_message.get("after")
            normalized_after = int(after) if isinstance(after, int | float | str) else None
            if self._observability is not None:
                self._observability.project_external_event(
                    {
                        "event": "gateway.control",
                        "subtype": subtype,
                        "conversation_id": conversation_id,
                        "after": normalized_after,
                    }
                )
            return self.resume_bound_session(channel_identity, after=normalized_after)

        return []

    def _default_event_types(self, channel_type: str) -> list[str]:
        adapter = self._channel_adapters.get(channel_type)
        if adapter is None:
            return []
        return list(adapter.accepted_event_types())

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
                if self._observability is not None:
                    self._observability.project_external_event(
                        {
                            "event": "gateway.egress",
                            "channel": envelope.channel,
                            "conversation_id": envelope.conversation_id,
                            "session_id": envelope.session_id,
                            "event_type": envelope.event["event_type"],
                        }
                    )
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
