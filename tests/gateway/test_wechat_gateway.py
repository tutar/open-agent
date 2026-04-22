from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openagent.gateway import Gateway
from openagent.gateway.binding_store import FileSessionBindingStore
from openagent.gateway.channels.wechat import (
    InMemoryWechatInboundDedupeStore,
    WechatAppConfig,
    WechatChannelAdapter,
    WechatPrivateChatHost,
    WechatSdkClient,
    create_wechat_gateway,
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
class FakeWechatClient:
    replies: list[dict[str, str]] = field(default_factory=list)
    event_handler: Callable[[dict[str, object]], list[JsonObject]] | None = None

    def start(self, event_handler: Callable[[dict[str, object]], list[JsonObject]]) -> None:
        self.event_handler = event_handler

    def close(self) -> None:
        self.event_handler = None

    def reply(self, raw_event: dict[str, object], conversation_id: str, text: str) -> None:
        del raw_event
        self.replies.append({"conversation_id": conversation_id, "text": text})


@dataclass(slots=True)
class FakeSdkMessage:
    type: str = "text"
    text: str = "hello"
    user_id: str = "wx_user_1"
    raw: dict[str, object] = field(default_factory=lambda: {"msg_id": "wx_msg_1"})


@dataclass(slots=True)
class FakeSdkBot:
    handlers: list[Callable[[Any], Any]] = field(default_factory=list)
    replies: list[tuple[object, str]] = field(default_factory=list)
    started: bool = False
    logged_in: bool = False

    def on_message(self, handler: Callable[[Any], Any]) -> Callable[[Any], Any]:
        self.handlers.append(handler)
        return handler

    async def login(self) -> None:
        self.logged_in = True

    async def start(self) -> None:
        self.started = True

    async def reply(self, msg: object, text: str) -> None:
        self.replies.append((msg, text))


def create_test_wechat_gateway(
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
    gateway.register_channel(WechatChannelAdapter())
    return gateway, runtime


def test_wechat_adapter_normalizes_private_text_message() -> None:
    adapter = WechatChannelAdapter()

    inbound = adapter.normalize_inbound(
        {
            "type": "message",
            "message_type": "text",
            "message_id": "wx_msg_1",
            "conversation_id": "wx_user_1",
            "from_user": "wx_user_1",
            "sender_display_name": "Alice",
            "content": "hello from wechat",
        }
    )

    assert inbound is not None
    assert inbound.input_kind == "user_message"
    assert inbound.payload == {"content": "hello from wechat"}
    assert inbound.channel_identity["channel_type"] == "wechat"
    assert inbound.channel_identity["user_id"] == "wx_user_1"
    assert inbound.channel_identity["conversation_id"] == "wechat:private:wx_user_1"
    assert inbound.delivery_metadata["message_id"] == "wx_msg_1"


def test_wechat_adapter_maps_management_commands() -> None:
    adapter = WechatChannelAdapter()

    inbound = adapter.normalize_inbound(
        {
            "type": "message",
            "message_type": "text",
            "message_id": "wx_msg_2",
            "conversation_id": "wx_user_1",
            "from_user": "wx_user_1",
            "content": "/channel",
        }
    )

    assert inbound is not None
    assert inbound.input_kind == "management"
    assert inbound.payload == {"command": "/channel"}


def test_wechat_sdk_client_registers_handler() -> None:
    sdk_bot = FakeSdkBot()
    client = WechatSdkClient(sdk_bot=sdk_bot)

    client.start(lambda event: [{"conversation_id": str(event["conversation_id"]), "text": "ok"}])

    assert len(sdk_bot.handlers) == 1


def test_wechat_sdk_client_replies_with_original_message_handle() -> None:
    sdk_bot = FakeSdkBot()
    client = WechatSdkClient(sdk_bot=sdk_bot)
    message = FakeSdkMessage()
    raw_event = client.event_from_message(message)

    client.reply(raw_event, "wx_user_1", "hello back")

    assert sdk_bot.replies == [(message, "hello back")]


def test_wechat_private_host_lazy_binds_and_replies(tmp_path: Path) -> None:
    client = FakeWechatClient()
    gateway, _ = create_test_wechat_gateway(tmp_path, message="hello via wechat")
    adapter = gateway.get_channel_adapter("wechat")
    assert isinstance(adapter, WechatChannelAdapter)
    host = WechatPrivateChatHost(
        gateway=gateway,
        adapter=adapter,
        client=client,
        dedupe_store=InMemoryWechatInboundDedupeStore(),
        allowed_senders={"wx_user_1"},
    )

    outbound = host.handle_event(
        {
            "type": "message",
            "message_type": "text",
            "message_id": "wx_msg_1",
            "conversation_id": "wx_user_1",
            "from_user": "wx_user_1",
            "content": "hello",
        }
    )

    assert outbound == [{"conversation_id": "wx_user_1", "text": "hello via wechat"}]
    assert client.replies == [{"conversation_id": "wx_user_1", "text": "hello via wechat"}]
    binding = gateway.get_binding("wechat", "wechat:private:wx_user_1")
    assert binding.session_id == "wechat-session:wechat:private:wx_user_1"


def test_wechat_private_host_ignores_unallowed_sender(tmp_path: Path) -> None:
    client = FakeWechatClient()
    gateway, _ = create_test_wechat_gateway(tmp_path, message="unused")
    adapter = gateway.get_channel_adapter("wechat")
    assert isinstance(adapter, WechatChannelAdapter)
    host = WechatPrivateChatHost(
        gateway=gateway,
        adapter=adapter,
        client=client,
        allowed_senders={"wx_user_1"},
    )

    outbound = host.handle_event(
        {
            "type": "message",
            "message_type": "text",
            "message_id": "wx_msg_2",
            "conversation_id": "wx_user_2",
            "from_user": "wx_user_2",
            "content": "hello",
        }
    )

    assert outbound == []
    assert client.replies == []


def test_wechat_gateway_registers_adapter(tmp_path: Path) -> None:
    config = WechatAppConfig(
        session_root=str(tmp_path / "sessions"),
        binding_root=str(tmp_path / "bindings"),
        allowed_senders=("wx_user_1",),
    )

    gateway, runtime = create_wechat_gateway(config=config, model=StaticModel(message="hello"))

    assert runtime is not None
    assert isinstance(gateway.get_channel_adapter("wechat"), WechatChannelAdapter)
