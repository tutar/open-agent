"""WeChat private-chat host coordination."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from openagent.object_model import JsonObject

from ...core import Gateway
from ...models import ChannelIdentity, EgressEnvelope
from .adapter import WechatChannelAdapter, WechatRawEvent
from .client import WechatBotClient
from .dedupe import FileWechatInboundDedupeStore, InMemoryWechatInboundDedupeStore


@dataclass(slots=True)
class WechatPrivateChatHost:
    """Glue `wechatbot-sdk` message handling to the gateway runtime."""

    gateway: Gateway
    adapter: WechatChannelAdapter
    client: WechatBotClient
    management_handler: Callable[[str], list[JsonObject]] | None = None
    dedupe_store: InMemoryWechatInboundDedupeStore | FileWechatInboundDedupeStore | None = None
    allowed_senders: set[str] | None = None

    def run(self) -> None:
        print("wechat-host> starting wechatbot-sdk client", flush=True)
        self.client.start(self.handle_event)

    def close(self) -> None:
        self.client.close()

    def handle_event(self, raw_event: WechatRawEvent) -> list[JsonObject]:
        """Handle one SDK-normalized WeChat event."""

        message_id = self._extract_message_id(raw_event)
        if (
            message_id is not None
            and self.dedupe_store is not None
            and self.dedupe_store.check_and_mark(message_id)
        ):
            return []
        if not self._sender_allowed(raw_event):
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
        return self._dispatch_egress(raw_event, self.gateway.process_input(inbound))

    def _ensure_binding(self, channel_identity: ChannelIdentity) -> str:
        conversation_id = channel_identity.conversation_id or "default"
        try:
            binding = self.gateway.get_binding(channel_identity.channel_type, conversation_id)
            return binding.session_id
        except KeyError:
            pass
        session_id = f"wechat-session:{conversation_id}"
        self.gateway.bind_session(channel_identity, session_id, adapter_name="wechat")
        return session_id

    def _dispatch_egress(
        self,
        raw_event: WechatRawEvent,
        egress_events: list[EgressEnvelope],
    ) -> list[JsonObject]:
        outbound_messages: list[JsonObject] = []
        for event in egress_events:
            projected = self.adapter.project_outbound(event)
            if projected is not None:
                self.client.reply(
                    raw_event,
                    conversation_id=str(projected["conversation_id"]),
                    text=str(projected["text"]),
                )
                outbound_messages.append(projected)
        return outbound_messages

    def _dispatch_management_responses(
        self,
        raw_event: WechatRawEvent,
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
            self.client.reply(
                raw_event,
                conversation_id=str(projected["conversation_id"]),
                text=str(projected["text"]),
            )
            outbound.append(projected)
        return outbound

    def _extract_message_id(self, raw_event: WechatRawEvent) -> str | None:
        value = raw_event.get("message_id")
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _sender_allowed(self, raw_event: WechatRawEvent) -> bool:
        if not self.allowed_senders:
            return True
        sender = str(raw_event.get("from_user", "")).strip()
        return sender in self.allowed_senders
