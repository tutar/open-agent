from __future__ import annotations

import asyncio
import importlib.util
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from openagent.gateway import Gateway
from openagent.gateway.binding_store import FileSessionBindingStore
from openagent.gateway.channels.wecom import (
    InMemoryWeComInboundDedupeStore,
    WeComAiBotClient,
    WeComAppConfig,
    WeComChannelAdapter,
    WeComPrivateChatHost,
    create_wecom_gateway,
)
from openagent.gateway.session_adapter import InProcessSessionAdapter
from openagent.harness.assemblies import create_file_runtime_assembly
from openagent.harness.runtime import ModelTurnRequest, ModelTurnResponse
from openagent.object_model import JsonObject


@dataclass(slots=True)
class StaticModel:
    message: str

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        del request
        return ModelTurnResponse(assistant_message=self.message)


@dataclass(slots=True)
class FakeWeComClient:
    responses: list[dict[str, object]] = field(default_factory=list)
    event_handler: Callable[[dict[str, object]], list[JsonObject]] | None = None

    def start(self, event_handler: Callable[[dict[str, object]], list[JsonObject]]) -> None:
        self.event_handler = event_handler

    def close(self) -> None:
        self.event_handler = None

    def respond(
        self,
        raw_event: dict[str, object],
        conversation_id: str,
        text: str,
        *,
        finish: bool = True,
    ) -> None:
        del raw_event
        self.responses.append({"conversation_id": conversation_id, "text": text, "finish": finish})


@dataclass(slots=True)
class FakeWebSocket:
    sent: list[JsonObject] = field(default_factory=list)

    async def send_json(self, payload: JsonObject) -> None:
        self.sent.append(payload)


def create_test_wecom_gateway(
    tmp_path: Path,
    *,
    message: str,
) -> tuple[Gateway, object]:
    runtime = create_file_runtime_assembly(
        model=StaticModel(message=message),
        session_root=str(tmp_path / "sessions"),
    )
    gateway = Gateway(
        InProcessSessionAdapter(runtime),
        binding_store=FileSessionBindingStore(str(tmp_path / "bindings")),
    )
    gateway.register_channel(WeComChannelAdapter())
    return gateway, runtime


def private_text_event(
    *,
    message_id: str = "wecom_msg_1",
    from_user: str = "userid_1",
    content: str = "hello from wecom",
) -> dict[str, object]:
    return {
        "type": "message",
        "message_type": "text",
        "message_id": message_id,
        "conversation_id": from_user,
        "from_user": from_user,
        "sender_display_name": "Alice",
        "content": content,
        "reply_context": {"msg_id": message_id},
    }


def test_wecom_adapter_normalizes_private_text_message() -> None:
    adapter = WeComChannelAdapter()

    inbound = adapter.normalize_inbound(private_text_event())

    assert inbound is not None
    assert inbound.input_kind == "user_message"
    assert inbound.payload == {"content": "hello from wecom"}
    assert inbound.channel_identity["channel_type"] == "wecom"
    assert inbound.channel_identity["user_id"] == "userid_1"
    assert inbound.channel_identity["conversation_id"] == "wecom:private:userid_1"
    assert inbound.delivery_metadata["message_id"] == "wecom_msg_1"


def test_wecom_adapter_maps_management_commands() -> None:
    adapter = WeComChannelAdapter()

    inbound = adapter.normalize_inbound(private_text_event(content="/channel"))

    assert inbound is not None
    assert inbound.input_kind == "management"
    assert inbound.payload == {"command": "/channel"}


def test_wecom_adapter_ignores_non_text_message() -> None:
    adapter = WeComChannelAdapter()
    event = private_text_event()
    event["message_type"] = "image"

    assert adapter.normalize_inbound(event) is None


def test_wecom_client_sends_subscribe_and_ping_frames() -> None:
    websocket = FakeWebSocket()
    client = WeComAiBotClient(bot_id="bot_1", secret="secret_1")

    asyncio.run(client.subscribe(websocket))
    asyncio.run(client.send_ping(websocket))

    subscribe_frame = websocket.sent[0]
    ping_frame = websocket.sent[1]
    assert subscribe_frame["cmd"] == "aibot_subscribe"
    assert subscribe_frame["body"] == {"bot_id": "bot_1", "secret": "secret_1"}
    assert isinstance(subscribe_frame["headers"], dict)
    assert ping_frame["cmd"] == "ping"
    assert isinstance(ping_frame["headers"], dict)


def test_wecom_client_normalizes_official_text_callback_frame() -> None:
    handled: list[dict[str, object]] = []
    client = WeComAiBotClient(bot_id="bot_1", secret="secret_1")

    def handle_event(event: dict[str, object]) -> list[JsonObject]:
        handled.append(event)
        return []

    client.set_event_handler(handle_event)

    client.handle_frame(
        {
            "cmd": "aibot_msg_callback",
            "headers": {"req_id": "req_1"},
            "body": {
                "msgid": "wecom_msg_1",
                "chatid": "chat_1",
                "chattype": "single",
                "from": {"userid": "userid_1", "name": "Alice"},
                "msgtype": "text",
                "text": {"content": "hello"},
            },
        }
    )

    assert handled == [
        {
            "type": "message",
            "message_type": "text",
            "message_id": "wecom_msg_1",
            "conversation_id": "chat_1",
            "from_user": "userid_1",
            "sender_display_name": "Alice",
            "content": "hello",
            "reply_context": {"req_id": "req_1", "msgid": "wecom_msg_1", "chatid": "chat_1"},
            "raw": {
                "cmd": "aibot_msg_callback",
                "headers": {"req_id": "req_1"},
                "body": {
                    "msgid": "wecom_msg_1",
                    "chatid": "chat_1",
                    "chattype": "single",
                    "from": {"userid": "userid_1", "name": "Alice"},
                    "msgtype": "text",
                    "text": {"content": "hello"},
                },
            },
        }
    ]


def test_wecom_client_ignores_success_ack_frame() -> None:
    handled: list[dict[str, object]] = []
    client = WeComAiBotClient(bot_id="bot_1", secret="secret_1")

    def handle_event(event: dict[str, object]) -> list[JsonObject]:
        handled.append(event)
        return []

    client.set_event_handler(handle_event)

    client.handle_frame({"errcode": 0, "errmsg": "ok", "headers": {"req_id": "req_1"}})

    assert handled == []


def test_wecom_client_respond_sends_official_response_frame() -> None:
    websocket = FakeWebSocket()
    client = WeComAiBotClient(bot_id="bot_1", secret="secret_1")
    client.set_websocket(websocket)

    client.respond(
        {
            "reply_context": {
                "req_id": "req_1",
                "msgid": "wecom_msg_1",
                "chatid": "chat_1",
                "stream_id": "stream_1",
            }
        },
        "chat_1",
        "hello back",
    )

    assert websocket.sent == [
        {
            "cmd": "aibot_respond_msg",
            "headers": {"req_id": "req_1"},
            "body": {
                "msgtype": "stream",
                "stream": {
                    "id": "stream_1",
                    "finish": True,
                    "content": "hello back",
                },
            },
        }
    ]


def test_wecom_client_respond_can_keep_stream_open() -> None:
    websocket = FakeWebSocket()
    client = WeComAiBotClient(bot_id="bot_1", secret="secret_1")
    client.set_websocket(websocket)

    client.respond(
        {
            "reply_context": {
                "req_id": "req_1",
                "msgid": "wecom_msg_1",
                "chatid": "chat_1",
                "stream_id": "stream_1",
            }
        },
        "chat_1",
        "working",
        finish=False,
    )

    assert websocket.sent == [
        {
            "cmd": "aibot_respond_msg",
            "headers": {"req_id": "req_1"},
            "body": {
                "msgtype": "stream",
                "stream": {
                    "id": "stream_1",
                    "finish": False,
                    "content": "working",
                },
            },
        }
    ]


def test_wecom_client_dispatches_handler_off_websocket_loop() -> None:
    websocket = FakeWebSocket()
    client = WeComAiBotClient(bot_id="bot_1", secret="secret_1")
    started = threading.Event()
    release = threading.Event()

    def handle_event(event: dict[str, object]) -> list[JsonObject]:
        started.set()
        client.respond(event, "chat_1", "处理中，请稍候...", finish=False)
        release.wait(timeout=1)
        return []

    client.set_event_handler(handle_event)

    async def exercise() -> None:
        client.set_websocket(websocket)
        start = time.monotonic()
        client.handle_frame(
            {
                "cmd": "aibot_msg_callback",
                "headers": {"req_id": "req_1"},
                "body": {
                    "msgid": "wecom_msg_1",
                    "chatid": "chat_1",
                    "chattype": "single",
                    "from": {"userid": "userid_1"},
                    "msgtype": "text",
                    "text": {"content": "hello"},
                },
            }
        )

        assert time.monotonic() - start < 0.2
        assert started.wait(timeout=0.5)
        for _ in range(20):
            if websocket.sent:
                break
            await asyncio.sleep(0.01)
        release.set()

    asyncio.run(exercise())

    response_body = websocket.sent[0]["body"]
    assert isinstance(response_body, dict)
    response_stream = response_body["stream"]
    assert response_stream == {
        "id": "stream_wecom_msg_1",
        "finish": False,
        "content": "处理中，请稍候...",
    }


def test_wecom_client_start_reports_missing_aiohttp(monkeypatch: pytest.MonkeyPatch) -> None:
    client = WeComAiBotClient(bot_id="bot_1", secret="secret_1")
    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str, package: str | None = None) -> object | None:
        if name == "aiohttp":
            return None
        return real_find_spec(name, package)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    with pytest.raises(RuntimeError, match="openagent\\[wecom\\]"):
        client.start(lambda event: [])


def test_wecom_client_normalizes_text_frame_for_handler() -> None:
    handled: list[dict[str, object]] = []
    client = WeComAiBotClient(bot_id="bot_1", secret="secret_1")

    def handle_event(event: dict[str, object]) -> list[JsonObject]:
        handled.append(event)
        return []

    client.set_event_handler(handle_event)

    client.handle_frame(
        {
            "type": "message",
            "message_type": "text",
            "message_id": "wecom_msg_1",
            "from_user": "userid_1",
            "content": {"text": "hello"},
            "reply_context": {"response_code": "ctx_1"},
        }
    )

    assert handled == [
        {
            "type": "message",
            "message_type": "text",
            "message_id": "wecom_msg_1",
            "conversation_id": "userid_1",
            "from_user": "userid_1",
            "sender_display_name": "",
            "content": "hello",
            "reply_context": {"response_code": "ctx_1"},
            "raw": {
                "type": "message",
                "message_type": "text",
                "message_id": "wecom_msg_1",
                "from_user": "userid_1",
                "content": {"text": "hello"},
                "reply_context": {"response_code": "ctx_1"},
            },
        }
    ]


def test_wecom_private_host_lazy_binds_and_replies(tmp_path: Path) -> None:
    client = FakeWeComClient()
    gateway, _ = create_test_wecom_gateway(tmp_path, message="hello via wecom")
    adapter = gateway.get_channel_adapter("wecom")
    assert isinstance(adapter, WeComChannelAdapter)
    host = WeComPrivateChatHost(
        gateway=gateway,
        adapter=adapter,
        client=client,
        dedupe_store=InMemoryWeComInboundDedupeStore(),
        allowed_users={"userid_1"},
    )

    outbound = host.handle_event(private_text_event(content="hello"))

    assert outbound == [{"conversation_id": "userid_1", "text": "hello via wecom"}]
    assert client.responses == [
        {"conversation_id": "userid_1", "text": "处理中，请稍候...", "finish": False},
        {"conversation_id": "userid_1", "text": "hello via wecom", "finish": True},
    ]
    binding = gateway.get_binding("wecom", "wecom:private:userid_1")
    assert binding.session_id == "wecom-session:wecom:private:userid_1"


def test_wecom_private_host_ignores_unallowed_sender(tmp_path: Path) -> None:
    client = FakeWeComClient()
    gateway, _ = create_test_wecom_gateway(tmp_path, message="unused")
    adapter = gateway.get_channel_adapter("wecom")
    assert isinstance(adapter, WeComChannelAdapter)
    host = WeComPrivateChatHost(
        gateway=gateway,
        adapter=adapter,
        client=client,
        allowed_users={"userid_1"},
    )

    outbound = host.handle_event(private_text_event(from_user="userid_2"))

    assert outbound == []
    assert client.responses == []


def test_wecom_private_host_dedupes_message_id(tmp_path: Path) -> None:
    client = FakeWeComClient()
    gateway, _ = create_test_wecom_gateway(tmp_path, message="hello once")
    adapter = gateway.get_channel_adapter("wecom")
    assert isinstance(adapter, WeComChannelAdapter)
    host = WeComPrivateChatHost(
        gateway=gateway,
        adapter=adapter,
        client=client,
        dedupe_store=InMemoryWeComInboundDedupeStore(),
    )

    first = host.handle_event(private_text_event(message_id="same_msg"))
    second = host.handle_event(private_text_event(message_id="same_msg"))

    assert first == [{"conversation_id": "userid_1", "text": "hello once"}]
    assert second == []
    assert client.responses == [
        {"conversation_id": "userid_1", "text": "处理中，请稍候...", "finish": False},
        {"conversation_id": "userid_1", "text": "hello once", "finish": True},
    ]


def test_wecom_gateway_registers_adapter(tmp_path: Path) -> None:
    config = WeComAppConfig(
        bot_id="bot_1",
        secret="secret_1",
        session_root=str(tmp_path / "sessions"),
        binding_root=str(tmp_path / "bindings"),
        allowed_users=("userid_1",),
    )

    gateway, runtime = create_wecom_gateway(config=config, model=StaticModel(message="hello"))

    assert runtime is not None
    assert isinstance(gateway.get_channel_adapter("wecom"), WeComChannelAdapter)
