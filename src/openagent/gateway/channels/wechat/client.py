"""wechatbot-sdk client wrapper for the WeChat channel."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

from openagent.object_model import JsonObject

from .adapter import WechatRawEvent


class WechatBotClient(Protocol):
    def start(self, event_handler: Callable[[WechatRawEvent], list[JsonObject]]) -> None:
        """Start receiving WeChat private-chat events."""

    def close(self) -> None:
        """Stop the client."""

    def reply(self, raw_event: WechatRawEvent, conversation_id: str, text: str) -> None:
        """Reply to a WeChat private-chat message."""


@dataclass(slots=True)
class WechatSdkClient:
    """Adapter around `wechatbot-sdk` with a synchronous OpenAgent surface."""

    sdk_bot: Any | None = None
    base_url: str = "https://ilinkai.weixin.qq.com"
    cred_path: str = str(Path(".openagent") / "wechat" / "credentials.json")
    on_qr_url: Callable[[str], None] | None = None
    on_scanned: Callable[[], None] | None = None
    on_expired: Callable[[], None] | None = None
    on_error: Callable[[object], None] | None = None
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _owns_bot: bool = field(default=False, init=False, repr=False)
    _event_handler: Callable[[WechatRawEvent], list[JsonObject]] | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if self.sdk_bot is None:
            self.sdk_bot = self._create_sdk_bot()
            self._owns_bot = True

    def start(self, event_handler: Callable[[WechatRawEvent], list[JsonObject]]) -> None:
        self._event_handler = event_handler

        async def _handle_message(msg: object) -> None:
            handler = self._event_handler
            if handler is None:
                return
            handler(self.event_from_message(msg))

        sdk_bot = cast(Any, self.sdk_bot)
        sdk_bot.on_message(_handle_message)
        if self._owns_bot and self._thread is None:
            self._thread = threading.Thread(
                target=self._run_bot,
                name="openagent-wechat-sdk",
                daemon=True,
            )
            self._thread.start()

    def close(self) -> None:
        self._event_handler = None

    def reply(self, raw_event: WechatRawEvent, conversation_id: str, text: str) -> None:
        del conversation_id
        message_handle = raw_event.get("_message_handle")
        if message_handle is None:
            return
        sdk_bot = cast(Any, self.sdk_bot)
        self._run_async(sdk_bot.reply(message_handle, text))

    def event_from_message(self, msg: object) -> WechatRawEvent:
        raw = getattr(msg, "raw", {})
        raw_dict = dict(raw) if isinstance(raw, dict) else {}
        user_id = str(getattr(msg, "user_id", ""))
        text = str(getattr(msg, "text", ""))
        message_type = str(getattr(msg, "type", "text"))
        return {
            "type": "message",
            "message_type": message_type,
            "message_id": self._message_id(msg, raw_dict, user_id, text),
            "conversation_id": user_id,
            "from_user": user_id,
            "sender_display_name": str(raw_dict.get("nickname", raw_dict.get("display_name", ""))),
            "content": text,
            "raw": raw_dict,
            "_message_handle": msg,
        }

    def _create_sdk_bot(self) -> object:
        try:
            module = importlib.import_module("wechatbot")
        except ImportError as exc:
            raise RuntimeError(
                "WeChat support requires the optional dependency 'wechatbot-sdk'. "
                "Install it with: pip install 'openagent[wechat]'"
            ) from exc
        bot_class = getattr(module, "WeChatBot")
        return bot_class(
            base_url=self.base_url,
            cred_path=self.cred_path,
            on_qr_url=self.on_qr_url or self._default_qr_url,
            on_scanned=self.on_scanned,
            on_expired=self.on_expired,
            on_error=self.on_error,
        )

    def _run_bot(self) -> None:
        async def _runner() -> None:
            sdk_bot = cast(Any, self.sdk_bot)
            await sdk_bot.login()
            await sdk_bot.start()

        asyncio.run(_runner())

    def _run_async(self, awaitable: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(awaitable)
            return
        loop.create_task(awaitable)

    def _message_id(
        self,
        msg: object,
        raw: dict[str, object],
        user_id: str,
        text: str,
    ) -> str:
        for key in ("msg_id", "message_id", "id", "new_msg_id"):
            value = raw.get(key)
            if value is not None:
                return str(value)
        timestamp = str(getattr(msg, "timestamp", raw.get("timestamp", "")))
        digest = hashlib.sha1(f"{user_id}|{timestamp}|{text}".encode()).hexdigest()
        return f"wechat-{digest}"

    def _default_qr_url(self, url: str) -> None:
        print(f"wechat-host> scan QR login URL: {url}", flush=True)
