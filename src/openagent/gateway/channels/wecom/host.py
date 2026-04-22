"""WeCom private-chat host coordination."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from openagent.object_model import JsonObject

from ...core import Gateway
from ...models import ChannelIdentity, EgressEnvelope
from .adapter import WeComChannelAdapter, WeComRawEvent
from .client import WeComBotClient
from .dedupe import FileWeComInboundDedupeStore, InMemoryWeComInboundDedupeStore

_PROCESSING_MESSAGE = "处理中，请稍候..."


@dataclass(slots=True)
class WeComPrivateChatHost:
    """Glue WeCom AI Bot WebSocket messages to the gateway runtime."""

    gateway: Gateway
    adapter: WeComChannelAdapter
    client: WeComBotClient
    management_handler: Callable[[str], list[JsonObject]] | None = None
    dedupe_store: InMemoryWeComInboundDedupeStore | FileWeComInboundDedupeStore | None = None
    allowed_users: set[str] | None = None

    def run(self) -> None:
        print("wecom-host> starting WeCom AI Bot WebSocket client", flush=True)
        self.client.start(self.handle_event)

    def close(self) -> None:
        self.client.close()

    def handle_event(self, raw_event: WeComRawEvent) -> list[JsonObject]:
        """Handle one normalized WeCom event."""

        message_id = self._extract_message_id(raw_event)
        if (
            message_id is not None
            and self.dedupe_store is not None
            and self.dedupe_store.check_and_mark(message_id)
        ):
            return []
        if not self._user_allowed(raw_event):
            return []
        inbound = self.adapter.normalize_inbound(raw_event)
        if inbound is None:
            return []
        channel_identity = ChannelIdentity.from_dict(inbound.channel_identity)
        if inbound.input_kind == "management":
            command = str(inbound.payload.get("command", ""))
            responses = (
                self.management_handler(command)
                if self.management_handler is not None
                else [{"type": "error", "message": "host management is unavailable"}]
            )
            return self._dispatch_management_responses(
                raw_event,
                channel_identity,
                cast(list[JsonObject], responses),
            )
        self._ensure_binding(channel_identity)
        self._send_processing_notice(raw_event, channel_identity)
        return self._dispatch_egress(raw_event, self.gateway.process_input(inbound))

    def _ensure_binding(self, channel_identity: ChannelIdentity) -> str:
        conversation_id = channel_identity.conversation_id or "default"
        try:
            binding = self.gateway.get_binding(channel_identity.channel_type, conversation_id)
            return binding.session_id
        except KeyError:
            pass
        session_id = f"wecom-session:{conversation_id}"
        self.gateway.bind_session(channel_identity, session_id, adapter_name="wecom")
        return session_id

    def _dispatch_egress(
        self,
        raw_event: WeComRawEvent,
        egress_events: list[EgressEnvelope],
    ) -> list[JsonObject]:
        outbound_messages: list[JsonObject] = []
        for event in egress_events:
            projected = self.adapter.project_outbound(event)
            if projected is not None:
                self.client.respond(
                    raw_event,
                    conversation_id=str(projected["conversation_id"]),
                    text=str(projected["text"]),
                    finish=True,
                )
                outbound_messages.append(projected)
        return outbound_messages

    def _send_processing_notice(
        self,
        raw_event: WeComRawEvent,
        channel_identity: ChannelIdentity,
    ) -> None:
        conversation_id = channel_identity.conversation_id or "default"
        self.client.respond(
            raw_event,
            conversation_id=self.adapter.parse_conversation_id(conversation_id),
            text=_PROCESSING_MESSAGE,
            finish=False,
        )

    def _dispatch_management_responses(
        self,
        raw_event: WeComRawEvent,
        channel_identity: ChannelIdentity,
        responses: list[JsonObject],
    ) -> list[JsonObject]:
        conversation_id = channel_identity.conversation_id or "default"
        outbound: list[JsonObject] = []
        for response in responses:
            projected: JsonObject = {
                "conversation_id": self.adapter.parse_conversation_id(conversation_id),
                "text": str(response.get("message", "")),
            }
            self.client.respond(
                raw_event,
                conversation_id=str(projected["conversation_id"]),
                text=str(projected["text"]),
                finish=True,
            )
            outbound.append(projected)
        return outbound

    def _extract_message_id(self, raw_event: WeComRawEvent) -> str | None:
        value = raw_event.get("message_id")
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _user_allowed(self, raw_event: WeComRawEvent) -> bool:
        if not self.allowed_users:
            return True
        sender = str(raw_event.get("from_user", "")).strip()
        return sender in self.allowed_users
