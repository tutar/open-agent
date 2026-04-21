"""WeChat private-chat channel adapter implementation."""

from __future__ import annotations

from dataclasses import dataclass

from openagent.object_model import JsonObject

from ...models import ChannelIdentity, EgressEnvelope, InboundEnvelope
from ..tui.terminal import _default_terminal_event_types

WechatRawEvent = dict[str, object]


@dataclass(slots=True)
class WechatChannelAdapter:
    """Project WeChat private-chat events into gateway envelopes and back."""

    channel_type: str = "wechat"

    def accepted_event_types(self) -> list[str]:
        return _default_terminal_event_types()

    def normalize_inbound(self, raw_event: WechatRawEvent) -> InboundEnvelope | None:
        """Convert an SDK-normalized WeChat message into a gateway envelope."""

        if str(raw_event.get("type", "")) != "message":
            return None
        if str(raw_event.get("message_type", "")) != "text":
            return None
        content = str(raw_event.get("content", "")).strip()
        if not content:
            return None
        conversation_key = str(raw_event.get("conversation_id", "")).strip()
        if not conversation_key:
            return None
        user_id = str(raw_event.get("from_user", "")).strip() or None
        channel_identity = ChannelIdentity(
            channel_type=self.channel_type,
            user_id=user_id,
            conversation_id=self._conversation_id(conversation_key),
        )
        input_kind, payload = self._parse_input(content)
        return InboundEnvelope(
            channel_identity=channel_identity.to_dict(),
            input_kind=input_kind,
            payload=payload,
            delivery_metadata={
                "message_id": str(raw_event.get("message_id", "")),
                "conversation_id": conversation_key,
                "sender_display_name": str(raw_event.get("sender_display_name", "")),
            },
        )

    def project_outbound(self, egress_event: EgressEnvelope) -> JsonObject | None:
        """Project assistant messages into SDK reply payloads."""

        event_type = str(egress_event.event.get("event_type", ""))
        payload = egress_event.event.get("payload")
        normalized_payload = payload if isinstance(payload, dict) else {}
        if event_type != "assistant_message":
            return None
        message = normalized_payload.get("message")
        if message is None:
            return None
        text = str(message).strip()
        if not text:
            return None
        return {
            "conversation_id": self.parse_conversation_id(egress_event.conversation_id),
            "text": text,
        }

    def parse_conversation_id(self, conversation_id: str) -> str:
        prefix = "wechat:private:"
        if conversation_id.startswith(prefix):
            return conversation_id[len(prefix) :]
        raise ValueError(f"Invalid WeChat conversation id: {conversation_id}")

    def _conversation_id(self, conversation_key: str) -> str:
        return f"wechat:private:{conversation_key}"

    def _parse_input(self, text: str) -> tuple[str, JsonObject]:
        command = text.strip()
        if (
            command == "/channel"
            or command.startswith("/channel ")
            or command.startswith("/channel-config ")
        ):
            return "management", {"command": command}
        return "user_message", {"content": text}
