"""Feishu host driver and official client integration."""

from __future__ import annotations

import fcntl
import importlib
import json
import os
import traceback
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openagent.object_model import JsonObject

from ..channels.feishu import FeishuBotClient, FeishuChannelAdapter
from ..core import Gateway
from ..models import ChannelIdentity, EgressEnvelope


@dataclass(slots=True)
class FeishuHostRunLock:
    """Prevent multiple local hosts from consuming the same Feishu app stream."""

    app_id: str
    lock_root: str
    _handle: Any | None = None

    def acquire(self) -> None:
        """Acquire a non-blocking local process lock for this Feishu app."""

        Path(self.lock_root).mkdir(parents=True, exist_ok=True)
        handle = open(Path(self.lock_root) / f"{self.app_id}.lock", "w", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise RuntimeError(
                "Another local Feishu host is already running for this app_id. "
                "Stop the existing process before starting a second host."
            ) from exc
        handle.write(str(os.getpid()))
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        """Release the local process lock when the host stops."""

        if self._handle is None:
            return
        with suppress(OSError):
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None


@dataclass(slots=True)
class FeishuLongConnectionHost:
    """Glue the Feishu long-connection client to the gateway runtime."""

    gateway: Gateway
    adapter: FeishuChannelAdapter
    client: FeishuBotClient
    run_lock: FeishuHostRunLock | None = None
    management_handler: Callable[[str], list[JsonObject]] | None = None

    def run(self) -> None:
        """Start the underlying Feishu long-connection client."""

        if self.run_lock is not None:
            self.run_lock.acquire()
        print("feishu-host> starting long connection", flush=True)
        try:
            self.client.start(self.handle_event)
        finally:
            if self.run_lock is not None:
                self.run_lock.release()

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
        if inbound.input_kind == "management":
            command = str(inbound.payload.get("command", ""))
            responses = (
                self.management_handler(command)
                if self.management_handler is not None
                else [{"type": "error", "message": "host management is unavailable"}]
            )
            outbound = self._dispatch_management_responses(channel_identity, responses)
            return outbound
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

    def _dispatch_management_responses(
        self,
        channel_identity: ChannelIdentity,
        responses: list[JsonObject],
    ) -> list[JsonObject]:
        conversation_id = channel_identity.conversation_id or "default"
        chat_id, thread_id = self.adapter.parse_conversation_id(conversation_id)
        outbound_messages: list[JsonObject] = []
        for response in responses:
            text = str(response.get("message", "")).strip()
            if not text:
                continue
            projected = {
                "chat_id": chat_id,
                "thread_id": thread_id,
                "text": text,
            }
            print(
                "feishu-host> sending management outbound"
                f" chat={chat_id} text={text}",
                flush=True,
            )
            self.adapter.send(projected)
            outbound_messages.append(projected)
        return outbound_messages


class OfficialFeishuBotClient:
    """Runtime wrapper over the official Feishu Python SDK."""

    def __init__(self, app_id: str, app_secret: str) -> None:
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
        self._app_id = app_id
        self._app_secret = app_secret
        self._client = (
            self._lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .log_level(self._lark.LogLevel.INFO)
            .build()
        )
        self._ws_client: Any | None = None

    def start(self, event_handler: Callable[[JsonObject], None]) -> None:
        """Open the Feishu long connection and dispatch incoming events."""

        def _safe_dispatch(data: Any) -> None:
            try:
                event_handler(self._marshal_event(data))
            except Exception as exc:  # pragma: no cover
                print(f"feishu-host> event handler failed: {exc}", flush=True)
                print(traceback.format_exc(), flush=True)

        dispatcher = (
            self._lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(_safe_dispatch)
            .build()
        )
        self._ws_client = self._lark.ws.Client(
            app_id=self._app_id,
            app_secret=self._app_secret,
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
