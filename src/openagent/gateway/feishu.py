"""Feishu channel adapter and long-connection host integration."""

from __future__ import annotations

import importlib
import json
import os
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from openagent.context_governance import ContextGovernance
from openagent.harness import ModelProviderAdapter, SimpleHarness
from openagent.harness.providers import load_model_from_env
from openagent.object_model import JsonObject
from openagent.observability import AgentObservability
from openagent.session import FileSessionStore
from openagent.tools import SimpleToolExecutor, StaticToolRegistry, ToolDefinition

from .adapters import _default_local_event_types
from .binding_store import FileSessionBindingStore
from .core import Gateway
from .models import ChannelIdentity, EgressEnvelope, InboundEnvelope
from .session_adapter import InProcessSessionAdapter


@dataclass(slots=True)
class FeishuAppConfig:
    """Configuration for the Feishu long-connection host."""

    app_id: str
    app_secret: str
    session_root: str
    binding_root: str
    mention_required_in_group: bool = True

    @classmethod
    def from_env(cls) -> FeishuAppConfig:
        """Load Feishu host configuration from the process environment."""

        app_id = os.getenv("OPENAGENT_FEISHU_APP_ID")
        app_secret = os.getenv("OPENAGENT_FEISHU_APP_SECRET")
        if not app_id:
            raise RuntimeError("OPENAGENT_FEISHU_APP_ID is required")
        if not app_secret:
            raise RuntimeError("OPENAGENT_FEISHU_APP_SECRET is required")

        session_root = os.getenv(
            "OPENAGENT_SESSION_ROOT",
            str(Path(".openagent") / "feishu" / "sessions"),
        )
        binding_root = os.getenv(
            "OPENAGENT_BINDING_ROOT",
            str(Path(session_root) / "bindings"),
        )
        mention_required = os.getenv("OPENAGENT_FEISHU_GROUP_AT_ONLY", "true").lower() != "false"
        return cls(
            app_id=app_id,
            app_secret=app_secret,
            session_root=session_root,
            binding_root=binding_root,
            mention_required_in_group=mention_required,
        )


class FeishuBotClient(Protocol):
    """Minimal Feishu client surface used by the host."""

    def start(self, event_handler: Callable[[JsonObject], None]) -> None:
        """Start receiving Feishu events and forward them to the host."""

    def close(self) -> None:
        """Stop the Feishu connection."""

    def send_text(self, chat_id: str, text: str, thread_id: str | None = None) -> None:
        """Send a text reply to a Feishu chat or thread."""


@dataclass(slots=True)
class FeishuChannelAdapter:
    """Project Feishu events into gateway envelopes and back into chat messages."""

    client: FeishuBotClient | None = None
    mention_required_in_group: bool = True
    channel_type: str = "feishu"
    _progress_seen: dict[tuple[str, str], bool] = field(default_factory=dict)

    def accepted_event_types(self) -> list[str]:
        """Expose the local frontend event surface to Feishu."""

        return _default_local_event_types()

    def normalize_inbound(self, raw_event: JsonObject) -> InboundEnvelope | None:
        """Convert a Feishu message event into a gateway input envelope."""

        event_type = str(raw_event.get("event_type", ""))
        if event_type != "im.message.receive_v1":
            header = raw_event.get("header")
            if not isinstance(header, dict):
                return None
            if str(header.get("event_type", "")) != "im.message.receive_v1":
                return None

        event = raw_event.get("event")
        if not isinstance(event, dict):
            return None
        message = event.get("message")
        sender = event.get("sender")
        if not isinstance(message, dict) or not isinstance(sender, dict):
            return None
        if str(message.get("message_type", "")) != "text":
            return None

        sender_id = sender.get("sender_id")
        open_id = None
        if isinstance(sender_id, dict) and sender_id.get("open_id") is not None:
            open_id = str(sender_id["open_id"])

        chat_id = str(message.get("chat_id", ""))
        message_id = str(message.get("message_id", ""))
        chat_type = str(message.get("chat_type", ""))
        thread_id = self._extract_thread_root(message)
        raw_text = self._extract_text_content(message.get("content"))
        mentions = self._extract_mentions(message)

        if chat_type != "p2p" and self.mention_required_in_group and not mentions:
            return None

        text = self._strip_mentions(raw_text, mentions).strip()
        if not text:
            return None

        channel_identity = ChannelIdentity(
            channel_type=self.channel_type,
            user_id=open_id,
            conversation_id=self._conversation_id(chat_id, thread_id),
        )
        input_kind, payload = self._parse_input(text)
        return InboundEnvelope(
            channel_identity=channel_identity.to_dict(),
            input_kind=input_kind,
            payload=payload,
            delivery_metadata={
                "message_id": message_id,
                "chat_id": chat_id,
                "chat_type": chat_type,
                "thread_id": thread_id,
            },
        )

    def project_outbound(self, egress_event: EgressEnvelope) -> JsonObject | None:
        """Project a gateway egress event into a Feishu text message."""

        event = egress_event.event
        event_type = str(event.get("event_type", ""))
        payload = event.get("payload")
        normalized_payload = payload if isinstance(payload, dict) else {}
        chat_id, thread_id = self.parse_conversation_id(egress_event.conversation_id)
        text = self._event_text(event_type, normalized_payload, egress_event.session_id)
        if text is None:
            return None
        return {
            "chat_id": chat_id,
            "thread_id": thread_id,
            "text": text,
        }

    def send(self, projected_message: JsonObject) -> None:
        """Send a projected message through the configured Feishu client."""

        if self.client is None:
            raise RuntimeError("Feishu client is not configured")
        self.client.send_text(
            chat_id=str(projected_message["chat_id"]),
            text=str(projected_message["text"]),
            thread_id=str(projected_message["thread_id"])
            if projected_message.get("thread_id") is not None
            else None,
        )

    def parse_conversation_id(self, conversation_id: str) -> tuple[str, str | None]:
        """Recover Feishu chat/thread information from a gateway conversation id."""

        parts = conversation_id.split(":")
        if len(parts) >= 3 and parts[0] == "feishu" and parts[1] == "chat":
            chat_id = parts[2]
            if len(parts) >= 5 and parts[3] == "thread":
                return chat_id, parts[4]
            return chat_id, None
        raise ValueError(f"Invalid Feishu conversation id: {conversation_id}")

    def _event_text(
        self,
        event_type: str,
        payload: JsonObject,
        session_id: str,
    ) -> str | None:
        if event_type == "assistant_message":
            message = payload.get("message")
            return str(message) if message is not None else None
        if event_type == "requires_action":
            tool_name = str(payload.get("tool_name", "unknown"))
            return f"Tool approval required for {tool_name}. Reply /approve or /reject."
        if event_type == "tool_started":
            tool_name = str(payload.get("tool_name", "unknown"))
            return f"Running tool: {tool_name}"
        if event_type == "tool_progress":
            tool_name = str(payload.get("tool_name", "unknown"))
            key = (session_id, tool_name)
            if self._progress_seen.get(key):
                return None
            self._progress_seen[key] = True
            return f"Tool {tool_name} is working..."
        if event_type == "tool_result":
            tool_name = str(payload.get("tool_name", "unknown"))
            self._clear_tool_progress(session_id, tool_name)
            content = payload.get("content")
            if isinstance(content, list) and content:
                return f"Tool {tool_name} completed: {content[0]}"
            return f"Tool {tool_name} completed."
        if event_type == "tool_failed":
            tool_name = str(payload.get("tool_name", "unknown"))
            self._clear_tool_progress(session_id, tool_name)
            reason = payload.get("error") or payload.get("message") or "unknown error"
            return f"Tool {tool_name} failed: {reason}"
        if event_type == "tool_cancelled":
            tool_name = str(payload.get("tool_name", "unknown"))
            self._clear_tool_progress(session_id, tool_name)
            return f"Tool {tool_name} was cancelled."
        if event_type == "turn_failed":
            self._clear_session_progress(session_id)
            reason = payload.get("reason") or payload.get("message") or payload
            return f"Turn failed: {reason}"
        if event_type == "turn_completed":
            self._clear_session_progress(session_id)
            return None
        return None

    def _parse_input(self, text: str) -> tuple[str, JsonObject]:
        command = text.strip()
        if command == "/approve":
            return "control", {"subtype": "permission_response", "approved": True}
        if command == "/reject":
            return "control", {"subtype": "permission_response", "approved": False}
        if command == "/interrupt":
            return "control", {"subtype": "interrupt"}
        if command == "/resume":
            return "control", {"subtype": "resume", "after": 0}
        return "user_message", {"content": text}

    def _conversation_id(self, chat_id: str, thread_id: str | None) -> str:
        if thread_id:
            return f"feishu:chat:{chat_id}:thread:{thread_id}"
        return f"feishu:chat:{chat_id}"

    def _extract_text_content(self, content: object) -> str:
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                return content
            if isinstance(parsed, dict) and parsed.get("text") is not None:
                return str(parsed["text"])
        return ""

    def _extract_mentions(self, message: JsonObject) -> list[JsonObject]:
        mentions = message.get("mentions")
        if isinstance(mentions, list):
            return [item for item in mentions if isinstance(item, dict)]
        return []

    def _extract_thread_root(self, message: JsonObject) -> str | None:
        if message.get("root_id") is not None:
            return str(message["root_id"])
        if message.get("parent_id") is not None:
            return str(message["parent_id"])
        return None

    def _strip_mentions(self, text: str, mentions: list[JsonObject]) -> str:
        normalized = text
        for mention in mentions:
            name = mention.get("name")
            key = mention.get("key")
            if name is not None:
                normalized = normalized.replace(f"@{name}", "")
            if key is not None:
                normalized = normalized.replace(f"<at user_id=\"{key}\"></at>", "")
                normalized = normalized.replace(f"<at user_id={key}></at>", "")
        return " ".join(normalized.split())

    def _clear_tool_progress(self, session_id: str, tool_name: str) -> None:
        self._progress_seen.pop((session_id, tool_name), None)

    def _clear_session_progress(self, session_id: str) -> None:
        stale_keys = [key for key in self._progress_seen if key[0] == session_id]
        for key in stale_keys:
            self._progress_seen.pop(key, None)


@dataclass(slots=True)
class FeishuLongConnectionHost:
    """Glue the Feishu long-connection client to the gateway runtime."""

    gateway: Gateway
    adapter: FeishuChannelAdapter
    client: FeishuBotClient

    def run(self) -> None:
        """Start the underlying Feishu long-connection client."""

        print("feishu-host> starting long connection", flush=True)
        self.client.start(self.handle_event)

    def close(self) -> None:
        """Close the underlying Feishu long-connection client."""

        self.client.close()

    def handle_event(self, raw_event: JsonObject) -> list[JsonObject]:
        """Handle a single Feishu event and emit projected outbound messages."""

        print(
            "feishu-host> received raw event",
            json.dumps(raw_event, ensure_ascii=False),
            flush=True,
        )
        inbound = self.adapter.normalize_inbound(raw_event)
        if inbound is None:
            print("feishu-host> ignored event after normalization", flush=True)
            return []

        channel_identity = ChannelIdentity.from_dict(inbound.channel_identity)
        print(
            "feishu-host> normalized input"
            f" kind={inbound.input_kind} conversation={channel_identity.conversation_id}",
            flush=True,
        )
        if inbound.input_kind == "control":
            try:
                egress = self.gateway.process_control_message(channel_identity, inbound.payload)
            except KeyError:
                message = self._missing_session_message(channel_identity)
                print(
                    "feishu-host> no bound session for control input; sending hint",
                    flush=True,
                )
                self.adapter.send(message)
                return [message]
            return self._dispatch_egress(egress)

        self._ensure_binding(channel_identity)
        egress = self.gateway.process_input(inbound)
        return self._dispatch_egress(egress)

    def _ensure_binding(self, channel_identity: ChannelIdentity) -> None:
        conversation_id = channel_identity.conversation_id or "default"
        try:
            self.gateway.get_binding(channel_identity.channel_type, conversation_id)
            return
        except KeyError:
            pass

        session_id = f"feishu-session:{conversation_id}"
        self.gateway.bind_session(
            channel_identity,
            session_id,
            adapter_name="feishu",
        )

    def _dispatch_egress(self, egress_events: list[EgressEnvelope]) -> list[JsonObject]:
        outbound_messages: list[JsonObject] = []
        for event in egress_events:
            projected = self.adapter.project_outbound(event)
            if projected is None:
                continue
            print(
                "feishu-host> sending outbound"
                f" event={event.event.get('event_type')} chat={projected['chat_id']}",
                flush=True,
            )
            self.adapter.send(projected)
            outbound_messages.append(projected)
        return outbound_messages

    def _missing_session_message(self, channel_identity: ChannelIdentity) -> JsonObject:
        conversation_id = channel_identity.conversation_id or "default"
        chat_id, thread_id = self.adapter.parse_conversation_id(conversation_id)
        return {
            "chat_id": chat_id,
            "thread_id": thread_id,
            "text": "No active session is bound for this chat yet. Send a normal message first.",
        }


class OfficialFeishuBotClient:
    """Runtime wrapper over the official Feishu Python SDK."""

    def __init__(self, config: FeishuAppConfig) -> None:
        try:
            self._lark = importlib.import_module("lark_oapi")
            im_v1 = importlib.import_module("lark_oapi.api.im.v1")
        except ImportError as exc:
            raise RuntimeError(
                "Feishu support requires the optional dependency 'lark-oapi'. "
                "Install it with: pip install 'openagent[feishu]'"
            ) from exc

        self._create_message_request = getattr(im_v1, "CreateMessageRequest")
        self._create_message_request_body = getattr(im_v1, "CreateMessageRequestBody")
        self._config = config
        self._client = (
            self._lark.Client.builder()
            .app_id(config.app_id)
            .app_secret(config.app_secret)
            .log_level(self._lark.LogLevel.INFO)
            .build()
        )
        self._ws_client: Any | None = None

    def start(self, event_handler: Callable[[JsonObject], None]) -> None:
        """Open the Feishu long connection and dispatch incoming events."""

        def _safe_dispatch(data: Any) -> None:
            try:
                event_handler(self._marshal_event(data))
            except Exception as exc:  # pragma: no cover - defensive runtime logging
                print(f"feishu-host> event handler failed: {exc}", flush=True)
                print(traceback.format_exc(), flush=True)

        dispatcher = (
            self._lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(_safe_dispatch)
            .build()
        )
        self._ws_client = self._lark.ws.Client(
            app_id=self._config.app_id,
            app_secret=self._config.app_secret,
            event_handler=dispatcher,
            log_level=self._lark.LogLevel.INFO,
        )
        self._ws_client.start()

    def close(self) -> None:
        """Close the websocket client when possible."""

        if self._ws_client is not None and hasattr(self._ws_client, "close"):
            self._ws_client.close()

    def send_text(self, chat_id: str, text: str, thread_id: str | None = None) -> None:
        """Send a text message to the target chat."""

        print(
            "feishu-host> sdk send_text"
            f" chat={chat_id} thread={thread_id} text={text}",
            flush=True,
        )
        body_builder = (
            self._create_message_request_body.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": text}, ensure_ascii=False))
        )
        if thread_id is not None:
            if hasattr(body_builder, "root_id"):
                body_builder = body_builder.root_id(thread_id)
            if hasattr(body_builder, "reply_in_thread"):
                body_builder = body_builder.reply_in_thread(True)

        request = (
            self._create_message_request.builder()
            .receive_id_type("chat_id")
            .request_body(body_builder.build())
            .build()
        )
        response = self._client.im.v1.message.create(request)
        if not response.success():
            raise RuntimeError(f"Feishu send_text failed: code={response.code} msg={response.msg}")

    def _marshal_event(self, data: Any) -> JsonObject:
        raw = self._lark.JSON.marshal(data)
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(parsed, dict):
            raise RuntimeError("Unexpected Feishu event payload")
        if "header" not in parsed:
            parsed["header"] = {"event_type": "im.message.receive_v1"}
        return parsed


def create_feishu_runtime(
    model: ModelProviderAdapter,
    session_root: str,
    tools: list[ToolDefinition] | None = None,
    observability: AgentObservability | None = None,
) -> SimpleHarness:
    """Create a file-backed runtime suitable for Feishu sessions."""

    registry = StaticToolRegistry(tools or [])
    return SimpleHarness(
        model=model,
        sessions=FileSessionStore(session_root),
        tools=registry,
        executor=SimpleToolExecutor(registry),
        context_governance=ContextGovernance(storage_dir=session_root),
        observability=observability,
    )


def create_feishu_gateway(
    config: FeishuAppConfig,
    model: ModelProviderAdapter,
    tools: list[ToolDefinition] | None = None,
) -> tuple[Gateway, SimpleHarness]:
    """Create the file-backed Feishu gateway/runtime pair."""

    runtime = create_feishu_runtime(model=model, session_root=config.session_root, tools=tools)
    gateway = Gateway(
        InProcessSessionAdapter(runtime),
        binding_store=FileSessionBindingStore(config.binding_root),
    )
    gateway.register_channel(
        FeishuChannelAdapter(mention_required_in_group=config.mention_required_in_group)
    )
    return gateway, runtime


def create_feishu_host_from_env() -> FeishuLongConnectionHost:
    """Build a Feishu long-connection host from environment variables."""

    config = FeishuAppConfig.from_env()
    gateway, _ = create_feishu_gateway(config=config, model=load_model_from_env())
    client = OfficialFeishuBotClient(config)
    adapter = FeishuChannelAdapter(
        client=client,
        mention_required_in_group=config.mention_required_in_group,
    )
    gateway.register_channel(adapter)
    return FeishuLongConnectionHost(gateway=gateway, adapter=adapter, client=client)


def main() -> None:
    """Start the Feishu long-connection gateway host."""

    host = create_feishu_host_from_env()
    host.run()


if __name__ == "__main__":
    main()
