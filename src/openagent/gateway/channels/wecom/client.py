"""aiohttp-based WeCom AI Bot WebSocket client."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from openagent.object_model import JsonObject

from .adapter import WeComRawEvent


class WeComBotClient(Protocol):
    def start(self, event_handler: Callable[[WeComRawEvent], list[JsonObject]]) -> None:
        """Start receiving WeCom private-chat events."""

    def close(self) -> None:
        """Stop the client."""

    def respond(
        self,
        raw_event: WeComRawEvent,
        conversation_id: str,
        text: str,
        *,
        finish: bool = True,
    ) -> None:
        """Respond to a WeCom private-chat message."""


@dataclass(slots=True)
class WeComAiBotClient:
    """Small WeCom AI Bot protocol client built on aiohttp WebSocket primitives."""

    bot_id: str
    secret: str
    ws_url: str = "wss://openws.work.weixin.qq.com"
    ping_interval_seconds: float = 30.0
    reconnect_base_seconds: float = 1.0
    reconnect_max_seconds: float = 30.0
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _event_handler: Callable[[WeComRawEvent], list[JsonObject]] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _websocket: Any | None = field(default=None, init=False, repr=False)
    _websocket_loop: asyncio.AbstractEventLoop | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def start(self, event_handler: Callable[[WeComRawEvent], list[JsonObject]]) -> None:
        self._ensure_dependencies()
        self.set_event_handler(event_handler)
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run_loop,
            name="openagent-wecom-aibot",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()

    def set_event_handler(
        self,
        event_handler: Callable[[WeComRawEvent], list[JsonObject]],
    ) -> None:
        self._event_handler = event_handler

    def set_websocket(self, websocket: object) -> None:
        self._websocket = websocket
        try:
            self._websocket_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._websocket_loop = None

    async def subscribe(self, websocket: object) -> None:
        await cast(Any, websocket).send_json(
            {
                "cmd": "aibot_subscribe",
                "headers": {"req_id": self._new_req_id()},
                "body": {
                    "bot_id": self.bot_id,
                    "secret": self.secret,
                },
            }
        )
        print("wecom-host> sent aibot_subscribe frame", flush=True)

    async def send_ping(self, websocket: object) -> None:
        await cast(Any, websocket).send_json(
            {
                "cmd": "ping",
                "headers": {"req_id": self._new_req_id()},
            }
        )

    def handle_frame(self, frame: JsonObject) -> None:
        if self._is_success_ack(frame):
            print("wecom-host> subscription acknowledged", flush=True)
            return
        handler = self._event_handler
        if handler is None:
            return
        event = self.event_from_frame(frame)
        if event is not None:
            self._dispatch_event_handler(handler, event)

    def respond(
        self,
        raw_event: WeComRawEvent,
        conversation_id: str,
        text: str,
        *,
        finish: bool = True,
    ) -> None:
        websocket = self._websocket
        if websocket is None:
            return
        del conversation_id
        reply_context = raw_event.get("reply_context")
        context = dict(reply_context) if isinstance(reply_context, dict) else {}
        stream_id = str(
            context.get("stream_id") or f"stream_{context.get('msgid') or self._new_req_id()}"
        )
        payload: JsonObject = {
            "cmd": "aibot_respond_msg",
            "headers": {"req_id": str(context.get("req_id") or self._new_req_id())},
            "body": {
                "msgtype": "stream",
                "stream": {
                    "id": stream_id,
                    "finish": finish,
                    "content": text,
                },
            },
        }
        self._run_async(cast(Any, websocket).send_json(payload))

    def event_from_frame(self, frame: JsonObject) -> WeComRawEvent | None:
        cmd = self._frame_cmd(frame)
        if cmd == "aibot_msg_callback":
            return self._event_from_callback_frame(frame)
        body = frame.get("body")
        if isinstance(body, dict) and self._frame_cmd(body) == "aibot_msg_callback":
            nested_frame: JsonObject = {
                "cmd": "aibot_msg_callback",
                "headers": frame.get("headers", {}),
                "body": body.get("body", body),
            }
            return self._event_from_callback_frame(nested_frame)
        message_type = str(
            frame.get("message_type") or frame.get("msgtype") or frame.get("msg_type") or ""
        )
        if not message_type:
            message_type = self._nested_str(frame, "message", "message_type") or "text"
        from_user = str(
            frame.get("from_user")
            or frame.get("from")
            or frame.get("userid")
            or self._nested_str(frame, "sender", "userid")
            or ""
        )
        content = self._extract_text(frame.get("content"))
        message_id = self._message_id(frame, from_user, content)
        reply_context = frame.get("reply_context")
        return {
            "type": "message",
            "message_type": message_type,
            "message_id": message_id,
            "conversation_id": str(frame.get("conversation_id") or from_user),
            "from_user": from_user,
            "sender_display_name": str(frame.get("sender_display_name", "")),
            "content": content,
            "reply_context": dict(reply_context) if isinstance(reply_context, dict) else {},
            "raw": frame,
        }

    def _event_from_callback_frame(self, frame: JsonObject) -> WeComRawEvent | None:
        body = frame.get("body")
        if not isinstance(body, dict):
            return None
        headers = frame.get("headers")
        header_values = headers if isinstance(headers, dict) else {}
        sender = body.get("from")
        sender_values = sender if isinstance(sender, dict) else {}
        from_user = str(
            body.get("from_user")
            or sender_values.get("userid")
            or sender_values.get("user_id")
            or ""
        )
        text = body.get("text")
        text_values = text if isinstance(text, dict) else {}
        content = str(body.get("content") or text_values.get("content") or "")
        msgid = str(body.get("msgid") or body.get("message_id") or "")
        chatid = str(body.get("chatid") or body.get("conversation_id") or from_user)
        return {
            "type": "message",
            "message_type": str(body.get("msgtype") or body.get("message_type") or "text"),
            "message_id": msgid or self._message_id(body, from_user, content),
            "conversation_id": chatid,
            "from_user": from_user,
            "sender_display_name": str(
                sender_values.get("name") or body.get("sender_display_name") or ""
            ),
            "content": content,
            "reply_context": {
                "req_id": str(header_values.get("req_id", "")),
                "msgid": msgid,
                "chatid": chatid,
            },
            "raw": frame,
        }

    def _run_loop(self) -> None:
        asyncio.run(self._connect_forever())

    def _ensure_dependencies(self) -> None:
        if importlib.util.find_spec("aiohttp") is None:
            raise RuntimeError(
                "WeCom support requires the optional dependency 'aiohttp'. "
                "Install it with openagent[wecom] or uv sync --extra wecom, or run with: "
                "uv run --extra wecom openagent-host --channel wecom"
            )

    async def _connect_forever(self) -> None:
        import aiohttp  # type: ignore[import-not-found]

        delay = self.reconnect_base_seconds
        while not self._stop.is_set():
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(self.ws_url) as websocket:
                        self._websocket = websocket
                        self._websocket_loop = asyncio.get_running_loop()
                        print(f"wecom-host> websocket connected url={self.ws_url}", flush=True)
                        await self.subscribe(websocket)
                        delay = self.reconnect_base_seconds
                        await self._receive_until_closed(websocket)
            except Exception as exc:
                if self._stop.is_set():
                    return
                print(f"wecom-host> websocket disconnected: {exc}", flush=True)
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.reconnect_max_seconds)

    async def _receive_until_closed(self, websocket: object) -> None:
        ping_task = asyncio.create_task(self._ping_loop(websocket))
        try:
            async for message in cast(Any, websocket):
                data = getattr(message, "data", None)
                if not isinstance(data, str):
                    continue
                try:
                    frame = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if isinstance(frame, dict):
                    self._log_frame(cast(JsonObject, frame))
                    self.handle_frame(cast(JsonObject, frame))
        finally:
            ping_task.cancel()
            self._websocket = None
            self._websocket_loop = None

    async def _ping_loop(self, websocket: object) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self.ping_interval_seconds)
            await self.send_ping(websocket)

    def _run_async(self, awaitable: Any) -> None:
        target_loop = self._websocket_loop
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            if target_loop is not None and target_loop.is_running():
                future = asyncio.run_coroutine_threadsafe(awaitable, target_loop)
                future.add_done_callback(self._log_threadsafe_send_error)
                return
            try:
                asyncio.run(awaitable)
            except Exception as exc:
                print(f"wecom-host> send failed: {exc}", flush=True)
                raise
            return
        task = loop.create_task(awaitable)
        task.add_done_callback(self._log_async_send_error)

    def _dispatch_event_handler(
        self,
        handler: Callable[[WeComRawEvent], list[JsonObject]],
        event: WeComRawEvent,
    ) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            handler(event)
            return
        thread = threading.Thread(
            target=self._run_event_handler,
            args=(handler, event),
            name="openagent-wecom-handler",
            daemon=True,
        )
        thread.start()

    def _run_event_handler(
        self,
        handler: Callable[[WeComRawEvent], list[JsonObject]],
        event: WeComRawEvent,
    ) -> None:
        try:
            handler(event)
        except Exception as exc:
            print(f"wecom-host> handler failed: {exc}", flush=True)

    def _log_async_send_error(self, task: asyncio.Task[Any]) -> None:
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            print(f"wecom-host> send failed: {exc}", flush=True)

    def _log_threadsafe_send_error(self, future: Any) -> None:
        try:
            exc = future.exception()
        except Exception as callback_exc:
            print(f"wecom-host> send failed: {callback_exc}", flush=True)
            return
        if exc is not None:
            print(f"wecom-host> send failed: {exc}", flush=True)

    def _extract_text(self, content: object) -> str:
        if isinstance(content, dict):
            value = content.get("text") or content.get("content")
            return str(value) if value is not None else ""
        if content is None:
            return ""
        return str(content)

    def _message_id(self, frame: JsonObject, from_user: str, text: str) -> str:
        for key in ("message_id", "msg_id", "msgid", "id"):
            value = frame.get(key)
            if value is not None:
                return str(value)
        timestamp = str(frame.get("timestamp", frame.get("create_time", "")))
        digest = hashlib.sha1(f"{from_user}|{timestamp}|{text}".encode()).hexdigest()
        return f"wecom-{digest}"

    def _nested_str(self, frame: JsonObject, outer: str, inner: str) -> str | None:
        value = frame.get(outer)
        if isinstance(value, dict) and value.get(inner) is not None:
            return str(value[inner])
        return None

    def _log_frame(self, frame: JsonObject) -> None:
        cmd = self._frame_cmd(frame) or "<unknown>"
        print(f"wecom-host> received frame cmd={cmd}", flush=True)
        if cmd == "<unknown>":
            print(
                "wecom-host> unknown frame summary " + self._frame_summary(frame),
                flush=True,
            )
        if os.getenv("OPENAGENT_WECOM_DEBUG", "").lower() in {"1", "true", "yes"}:
            print(
                "wecom-host> frame " + json.dumps(frame, ensure_ascii=False, default=str),
                flush=True,
            )

    def _frame_cmd(self, frame: JsonObject) -> str:
        for key in ("cmd", "command", "type", "event"):
            value = frame.get(key)
            if value is not None:
                return str(value)
        body = frame.get("body")
        if isinstance(body, dict):
            for key in ("cmd", "command", "type", "event"):
                value = body.get(key)
                if value is not None:
                    return str(value)
        return ""

    def _frame_summary(self, frame: JsonObject) -> str:
        body = frame.get("body")
        body_keys = sorted(str(key) for key in body.keys()) if isinstance(body, dict) else []
        summary: JsonObject = {
            "keys": list(sorted(str(key) for key in frame.keys())),
            "body_keys": list(body_keys),
        }
        for key in ("errcode", "errmsg", "code", "message", "msg"):
            value = frame.get(key)
            if value is not None:
                summary[key] = str(value)
        if isinstance(body, dict):
            for key in ("errcode", "errmsg", "code", "message", "msg"):
                value = body.get(key)
                if value is not None:
                    summary[f"body.{key}"] = str(value)
        return json.dumps(summary, ensure_ascii=False)

    def _is_success_ack(self, frame: JsonObject) -> bool:
        errcode = frame.get("errcode")
        errmsg = str(frame.get("errmsg", "")).lower()
        return str(errcode) == "0" and errmsg == "ok"

    def _new_req_id(self) -> str:
        return uuid.uuid4().hex
