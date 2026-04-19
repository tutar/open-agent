import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from time import sleep

from openagent import create_feishu_runtime
from openagent.gateway import (
    FEISHU_REACTION_COMPLETED,
    FEISHU_REACTION_IN_PROGRESS,
    ChannelIdentity,
    EgressEnvelope,
    FeishuAppConfig,
    FeishuChannelAdapter,
    FeishuLongConnectionHost,
    FileFeishuInboundDedupeStore,
    InMemoryFeishuInboundDedupeStore,
    OfficialFeishuBotClient,
    create_feishu_gateway,
)
from openagent.gateway.channels.feishu.cards import (
    FeishuReplyCardRecord,
    FileFeishuCardDeliveryStore,
)
from openagent.harness import ModelStreamEvent, ModelTurnRequest, ModelTurnResponse
from openagent.object_model import ToolResult
from openagent.tools import (
    PermissionDecision,
    ToolCall,
    ToolExecutionContext,
    ToolProgressUpdate,
    ToolStreamItem,
)


@dataclass(slots=True)
class StaticModel:
    message: str

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        del request
        return ModelTurnResponse(assistant_message=self.message)


@dataclass(slots=True)
class StreamingAssistantModel:
    chunks: list[ModelStreamEvent]

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        del request
        raise AssertionError("Streaming path should use stream_generate")

    def stream_generate(self, request: ModelTurnRequest) -> Iterator[ModelStreamEvent]:
        del request
        yield from self.chunks


@dataclass(slots=True)
class ToolThenReplyModel:
    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        latest = request.messages[-1]
        if latest.get("role") == "tool":
            return ModelTurnResponse(assistant_message="tool completed after approval")
        return ModelTurnResponse(
            tool_calls=[ToolCall(tool_name="admin", arguments={"text": "rotate"})]
        )


@dataclass(slots=True)
class ToolProgressModel:
    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        latest = request.messages[-1]
        if latest.get("role") == "tool":
            return ModelTurnResponse(assistant_message="done after progress")
        return ModelTurnResponse(
            tool_calls=[ToolCall(tool_name="stream", arguments={"text": "payload"})]
        )


@dataclass(slots=True)
class DemoTool:
    name: str
    permission: PermissionDecision = PermissionDecision.ALLOW
    input_schema: dict[str, str] = field(default_factory=lambda: {"type": "object"})

    def description(self) -> str:
        return self.name

    def call(self, arguments: dict[str, object]) -> ToolResult:
        return ToolResult(
            tool_name=self.name,
            success=True,
            content=[str(arguments.get("text", "ok"))],
        )

    def check_permissions(self, arguments: dict[str, object]) -> str:
        del arguments
        return self.permission.value

    def is_concurrency_safe(self) -> bool:
        return True


@dataclass(slots=True)
class StreamingTool(DemoTool):
    name: str = "stream"

    def stream_call(
        self,
        arguments: dict[str, object],
        context: ToolExecutionContext,
    ) -> Iterator[ToolStreamItem]:
        del context
        yield ToolStreamItem(
            progress=ToolProgressUpdate(
                tool_name=self.name,
                message="working",
                progress=0.5,
            )
        )
        yield ToolStreamItem(
            result=ToolResult(
                tool_name=self.name,
                success=True,
                content=[str(arguments.get("text", "ok"))],
            )
        )


class FakeFeishuClient:
    def __init__(self) -> None:
        self.sent_messages: list[dict[str, object]] = []
        self.sent_cards: list[dict[str, object]] = []
        self.resolved_cards: list[dict[str, object]] = []
        self.stream_settings: list[dict[str, object]] = []
        self.updated_cards: list[dict[str, object]] = []
        self.added_reactions: list[dict[str, str]] = []
        self.removed_reactions: list[dict[str, str]] = []
        self.started = False
        self.handler: Callable[[dict[str, object]], dict[str, object] | None] | None = None
        self.fail_card_sync_attempts = 0
        self.resolve_card_error: Exception | None = None

    def start(self, event_handler: Callable[[dict[str, object]], dict[str, object] | None]) -> None:
        self.started = True
        self.handler = event_handler

    def close(self) -> None:
        self.started = False

    def send_text(self, chat_id: str, text: str, thread_id: str | None = None) -> None:
        self.sent_messages.append(
            {
                "chat_id": chat_id,
                "thread_id": thread_id,
                "text": text,
            }
        )

    def send_card(self, chat_id: str, card: dict[str, object], thread_id: str | None = None) -> str:
        if self.fail_card_sync_attempts > 0:
            self.fail_card_sync_attempts -= 1
            raise RuntimeError("temporary card create failure")
        message_id = f"card_message_{len(self.sent_cards) + 1}"
        self.sent_cards.append(
            {
                "chat_id": chat_id,
                "thread_id": thread_id,
                "card": card,
                "message_id": message_id,
            }
        )
        return message_id

    def resolve_card_id(self, message_id: str) -> str:
        if self.resolve_card_error is not None:
            raise self.resolve_card_error
        card_id = f"card_id_{message_id}"
        self.resolved_cards.append({"message_id": message_id, "card_id": card_id})
        return card_id

    def enable_card_stream(self, card_id: str, *, uuid: str, sequence: int) -> None:
        if self.fail_card_sync_attempts > 0:
            self.fail_card_sync_attempts -= 1
            raise RuntimeError("temporary card stream settings failure")
        self.stream_settings.append(
            {
                "card_id": card_id,
                "uuid": uuid,
                "sequence": sequence,
                "enabled": True,
            }
        )

    def disable_card_stream(self, card_id: str, *, uuid: str, sequence: int) -> None:
        if self.fail_card_sync_attempts > 0:
            self.fail_card_sync_attempts -= 1
            raise RuntimeError("temporary card stream settings failure")
        self.stream_settings.append(
            {
                "card_id": card_id,
                "uuid": uuid,
                "sequence": sequence,
                "enabled": False,
            }
        )

    def stream_update_card(
        self,
        card_id: str,
        card: dict[str, object],
        *,
        uuid: str,
        sequence: int,
    ) -> None:
        if self.fail_card_sync_attempts > 0:
            self.fail_card_sync_attempts -= 1
            raise RuntimeError("temporary card stream update failure")
        self.updated_cards.append(
            {
                "card_id": card_id,
                "uuid": uuid,
                "sequence": sequence,
                "card": card,
            }
        )

    def update_card(self, message_id: str, card: dict[str, object]) -> None:
        self.updated_cards.append({"message_id": message_id, "card": card, "patched": True})

    def add_reaction(self, message_id: str, reaction_type: str) -> str | None:
        reaction_id = f"reaction:{message_id}:{reaction_type}"
        self.added_reactions.append(
            {
                "message_id": message_id,
                "reaction_type": reaction_type,
                "reaction_id": reaction_id,
            }
        )
        return reaction_id

    def remove_reaction(self, message_id: str, reaction_id: str) -> None:
        self.removed_reactions.append(
            {
                "message_id": message_id,
                "reaction_id": reaction_id,
            }
        )


@dataclass(slots=True)
class FakeClock:
    value: float = 0.0

    def now(self) -> float:
        return self.value


class _FakeReactionCreateResponse:
    def __init__(self, reaction_id: str = "reaction_id") -> None:
        self.code = 0
        self.msg = "ok"
        self.data = type("Data", (), {"reaction_id": reaction_id})()

    def success(self) -> bool:
        return True


class _FakeMessageReactionAPI:
    def __init__(self) -> None:
        self.created_request = None

    def create(self, request: object) -> _FakeReactionCreateResponse:
        self.created_request = request
        return _FakeReactionCreateResponse()


class _FakeImV1:
    def __init__(self) -> None:
        self.message = _FakeMessageAPI()
        self.message_reaction = _FakeMessageReactionAPI()


class _FakeMessageCreateResponse:
    def __init__(self, message_id: str = "om_card_1") -> None:
        self.code = 0
        self.msg = "ok"
        self.data = type("Data", (), {"message_id": message_id})()

    def success(self) -> bool:
        return True


class _FakeMessagePatchResponse:
    code = 0
    msg = "ok"

    def success(self) -> bool:
        return True


class _FakeMessageAPI:
    def __init__(self) -> None:
        self.created_request = None
        self.patched_request = None

    def create(self, request: object) -> _FakeMessageCreateResponse:
        self.created_request = request
        return _FakeMessageCreateResponse()

    def patch(self, request: object) -> _FakeMessagePatchResponse:
        self.patched_request = request
        return _FakeMessagePatchResponse()


class _FakeCardkitCardResponse:
    code = 0
    msg = "ok"

    def __init__(self, *, card_id: str = "card_id_1") -> None:
        self.data = type("Data", (), {"card_id": card_id})()

    def success(self) -> bool:
        return True


class _FakeCardkitCardAPI:
    def __init__(self) -> None:
        self.id_convert_request = None
        self.settings_request = None
        self.update_request = None

    def id_convert(self, request: object) -> _FakeCardkitCardResponse:
        self.id_convert_request = request
        return _FakeCardkitCardResponse(card_id="cardkit_1")

    def settings(self, request: object) -> _FakeCardkitCardResponse:
        self.settings_request = request
        return _FakeCardkitCardResponse()

    def update(self, request: object) -> _FakeCardkitCardResponse:
        self.update_request = request
        return _FakeCardkitCardResponse()


class _FakeCardkitV1:
    def __init__(self) -> None:
        self.card = _FakeCardkitCardAPI()


class _FakeSdkClient:
    def __init__(self) -> None:
        self.im = type("Im", (), {"v1": _FakeImV1()})()
        self.cardkit = type("Cardkit", (), {"v1": _FakeCardkitV1()})()


def make_text_event(
    text: str,
    *,
    chat_id: str = "oc_chat_1",
    chat_type: str = "p2p",
    open_id: str = "ou_user_1",
    message_id: str = "om_message_1",
    mentions: list[dict[str, object]] | None = None,
    root_id: str | None = None,
) -> dict[str, object]:
    message: dict[str, object] = {
        "chat_id": chat_id,
        "chat_type": chat_type,
        "message_id": message_id,
        "message_type": "text",
        "content": json.dumps({"text": text}),
    }
    if mentions is not None:
        message["mentions"] = mentions
    if root_id is not None:
        message["root_id"] = root_id
    return {
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": open_id}},
            "message": message,
        },
    }


def test_feishu_adapter_normalizes_private_text_message() -> None:
    adapter = FeishuChannelAdapter()

    inbound = adapter.normalize_inbound(make_text_event("hello from feishu"))

    assert inbound is not None
    assert inbound.input_kind == "user_message"
    assert inbound.payload["content"] == "hello from feishu"
    assert inbound.channel_identity["channel_type"] == "feishu"
    assert inbound.channel_identity["conversation_id"] == "feishu:chat:oc_chat_1"


def test_feishu_adapter_ignores_group_message_without_mention() -> None:
    adapter = FeishuChannelAdapter(mention_required_in_group=True)

    inbound = adapter.normalize_inbound(make_text_event("hello group", chat_type="group"))

    assert inbound is None


def test_feishu_adapter_strips_mentions_and_uses_thread_binding() -> None:
    adapter = FeishuChannelAdapter(mention_required_in_group=True)

    inbound = adapter.normalize_inbound(
        make_text_event(
            "@OpenAgent hello there",
            chat_type="group",
            mentions=[{"name": "OpenAgent", "key": "ou_bot"}],
            root_id="om_root_1",
        )
    )

    assert inbound is not None
    assert inbound.payload["content"] == "hello there"
    assert inbound.channel_identity["conversation_id"] == "feishu:chat:oc_chat_1:thread:om_root_1"


def test_feishu_adapter_only_maps_channel_management_commands_from_text() -> None:
    adapter = FeishuChannelAdapter()

    approve = adapter.normalize_inbound(make_text_event("/approve"))
    resume = adapter.normalize_inbound(make_text_event("/resume", message_id="om_4"))

    assert approve is not None and approve.input_kind == "user_message"
    assert approve.payload == {"content": "/approve"}
    assert resume is not None and resume.input_kind == "user_message"
    assert resume.payload == {"content": "/resume"}


def test_feishu_adapter_maps_channel_management_commands() -> None:
    adapter = FeishuChannelAdapter()

    channel = adapter.normalize_inbound(make_text_event("/channel"))
    config = adapter.normalize_inbound(
        make_text_event("/channel-config feishu app_id cli_app", message_id="om_cfg")
    )

    assert channel is not None
    assert channel.input_kind == "management"
    assert channel.payload == {"command": "/channel"}
    assert config is not None
    assert config.input_kind == "management"
    assert config.payload == {"command": "/channel-config feishu app_id cli_app"}


def test_feishu_adapter_drops_empty_assistant_messages() -> None:
    adapter = FeishuChannelAdapter()

    projected = adapter.project_outbound(
        EgressEnvelope(
            channel="feishu",
            conversation_id="feishu:chat:oc_chat_1",
            event={"event_type": "assistant_message", "payload": {"message": ""}},
            session_id="sess_1",
        )
    )

    assert projected is None


def test_feishu_adapter_maps_card_actions_to_control_payloads() -> None:
    adapter = FeishuChannelAdapter()

    approve = adapter.parse_card_action({"subtype": "permission_response", "approved": True})
    reject = adapter.parse_card_action({"subtype": "permission_response", "approved": False})

    assert approve == ("control", {"subtype": "permission_response", "approved": True})
    assert reject == ("control", {"subtype": "permission_response", "approved": False})
    assert adapter.parse_card_action({"subtype": "interrupt"}) is None
    assert adapter.parse_card_action({"subtype": "resume"}) is None


def test_official_feishu_client_wraps_reaction_type_as_emoji() -> None:
    client = OfficialFeishuBotClient("app", "secret")
    fake_sdk_client = _FakeSdkClient()
    client._client = fake_sdk_client  # type: ignore[assignment]

    reaction_id = client.add_reaction("om_message_1", FEISHU_REACTION_IN_PROGRESS)

    assert reaction_id == "reaction_id"
    request = fake_sdk_client.im.v1.message_reaction.created_request
    assert request is not None
    body = request.request_body
    assert body is not None
    assert body.reaction_type is not None
    assert body.reaction_type.emoji_type == FEISHU_REACTION_IN_PROGRESS


def test_official_feishu_client_sends_and_streams_interactive_cards() -> None:
    client = OfficialFeishuBotClient("app", "secret")
    fake_sdk_client = _FakeSdkClient()
    client._client = fake_sdk_client  # type: ignore[assignment]

    message_id = client.send_card("oc_chat_1", {"elements": []}, thread_id="om_root_1")
    card_id = client.resolve_card_id(message_id)
    client.enable_card_stream(card_id, uuid="uuid_1", sequence=1)
    client.stream_update_card(
        card_id,
        {"elements": []},
        uuid="uuid_1",
        sequence=2,
    )
    client.disable_card_stream(card_id, uuid="uuid_1", sequence=3)

    assert message_id == "om_card_1"
    created_request = fake_sdk_client.im.v1.message.created_request
    assert created_request is not None
    assert created_request.request_body.msg_type == "interactive"
    assert json.loads(created_request.request_body.content) == {"elements": []}
    id_convert_request = fake_sdk_client.cardkit.v1.card.id_convert_request
    assert id_convert_request is not None
    assert id_convert_request.request_body.message_id == "om_card_1"
    settings_request = fake_sdk_client.cardkit.v1.card.settings_request
    assert settings_request is not None
    assert settings_request.card_id == "cardkit_1"
    update_request = fake_sdk_client.cardkit.v1.card.update_request
    assert update_request is not None
    assert update_request.card_id == "cardkit_1"
    assert update_request.request_body.card.type == "card_json"


def test_feishu_host_lazy_binds_and_replies(tmp_path: Path) -> None:
    client = FakeFeishuClient()
    config = FeishuAppConfig(
        app_id="app",
        app_secret="secret",
        session_root=str(tmp_path / "sessions"),
        binding_root=str(tmp_path / "bindings"),
    )
    gateway, _ = create_feishu_gateway(config=config, model=StaticModel(message="hello via feishu"))
    adapter = gateway.get_channel_adapter("feishu")
    assert isinstance(adapter, FeishuChannelAdapter)
    adapter.client = client
    host = FeishuLongConnectionHost(
        gateway=gateway,
        adapter=adapter,
        client=client,
        dedupe_store=InMemoryFeishuInboundDedupeStore(),
        card_delivery_store=FileFeishuCardDeliveryStore(str(tmp_path / "cards.json")),
    )

    outbound = host.handle_event(make_text_event("hello"))

    assert outbound[-1]["delivery"] == "card"
    assert client.sent_cards[0]["chat_id"] == "oc_chat_1"
    latest_card = client.updated_cards[-1]["card"]
    assert latest_card["header"]["title"]["content"] == "OpenAgent · Completed"
    assert "hello via feishu" in latest_card["elements"][0]["content"]
    assert client.resolved_cards == [
        {
            "message_id": "card_message_1",
            "card_id": "card_id_card_message_1",
        }
    ]
    assert client.stream_settings[0]["enabled"] is True
    assert client.stream_settings[-1]["enabled"] is False
    binding = gateway.get_binding("feishu", "feishu:chat:oc_chat_1")
    assert binding.session_id == "feishu-session:feishu:chat:oc_chat_1"
    assert client.added_reactions == [
        {
            "message_id": "om_message_1",
            "reaction_type": FEISHU_REACTION_IN_PROGRESS,
            "reaction_id": f"reaction:om_message_1:{FEISHU_REACTION_IN_PROGRESS}",
        },
        {
            "message_id": "om_message_1",
            "reaction_type": FEISHU_REACTION_COMPLETED,
            "reaction_id": f"reaction:om_message_1:{FEISHU_REACTION_COMPLETED}",
        },
    ]
    assert client.removed_reactions == [
        {
            "message_id": "om_message_1",
            "reaction_id": f"reaction:om_message_1:{FEISHU_REACTION_IN_PROGRESS}",
        }
    ]


def test_feishu_host_supports_card_action_approval(tmp_path: Path) -> None:
    client = FakeFeishuClient()
    config = FeishuAppConfig(
        app_id="app",
        app_secret="secret",
        session_root=str(tmp_path / "sessions"),
        binding_root=str(tmp_path / "bindings"),
    )
    gateway, _ = create_feishu_gateway(
        config=config,
        model=ToolThenReplyModel(),
        tools=[DemoTool(name="admin", permission=PermissionDecision.ASK)],
    )
    adapter = gateway.get_channel_adapter("feishu")
    assert isinstance(adapter, FeishuChannelAdapter)
    adapter.client = client
    host = FeishuLongConnectionHost(
        gateway=gateway,
        adapter=adapter,
        client=client,
        dedupe_store=InMemoryFeishuInboundDedupeStore(),
        card_delivery_store=FileFeishuCardDeliveryStore(str(tmp_path / "cards.json")),
    )

    host.handle_event(make_text_event("admin rotate"))
    approval_card = client.updated_cards[-1]["card"]
    assert approval_card["header"]["title"]["content"] == "OpenAgent · Needs Approval"
    actions = approval_card["elements"][-1]["actions"]
    assert actions[0]["text"]["content"] == "Approve"
    assert [item["text"]["content"] for item in actions] == ["Approve", "Reject"]

    reply_message_id = str(client.sent_cards[0]["message_id"])
    result = host.handle_card_action(
        type(
            "Card",
            (),
            {
                "open_message_id": reply_message_id,
                "open_chat_id": "oc_chat_1",
                "open_id": "ou_user_1",
                "action": type(
                    "Action",
                    (),
                    {"value": {"subtype": "permission_response", "approved": True}},
                )(),
            },
        )()
    )

    assert result == {"toast": {"type": "info", "content": "Action received."}}
    final_card = client.updated_cards[-1]["card"]
    assert final_card["header"]["title"]["content"] == "OpenAgent · Completed"
    assert "tool completed after approval" in final_card["elements"][0]["content"]


def test_feishu_host_routes_management_commands_without_session_binding(tmp_path: Path) -> None:
    client = FakeFeishuClient()
    config = FeishuAppConfig(
        app_id="app",
        app_secret="secret",
        session_root=str(tmp_path / "sessions"),
        binding_root=str(tmp_path / "bindings"),
    )
    gateway, _ = create_feishu_gateway(config=config, model=StaticModel(message="unused"))
    adapter = gateway.get_channel_adapter("feishu")
    assert isinstance(adapter, FeishuChannelAdapter)
    adapter.client = client
    host = FeishuLongConnectionHost(
        gateway=gateway,
        adapter=adapter,
        client=client,
        management_handler=lambda command: [{"type": "status", "message": f"handled {command}"}],
        dedupe_store=InMemoryFeishuInboundDedupeStore(),
    )

    outbound = host.handle_event(make_text_event("/channel"))

    assert outbound == [{"chat_id": "oc_chat_1", "thread_id": None, "text": "handled /channel"}]


def test_feishu_adapter_coalesces_tool_progress_notifications(tmp_path: Path) -> None:
    client = FakeFeishuClient()
    config = FeishuAppConfig(
        app_id="app",
        app_secret="secret",
        session_root=str(tmp_path / "sessions"),
        binding_root=str(tmp_path / "bindings"),
    )
    gateway, _ = create_feishu_gateway(
        config=config,
        model=ToolProgressModel(),
        tools=[StreamingTool(name="stream")],
    )
    adapter = gateway.get_channel_adapter("feishu")
    assert isinstance(adapter, FeishuChannelAdapter)
    adapter.client = client
    host = FeishuLongConnectionHost(
        gateway=gateway,
        adapter=adapter,
        client=client,
        dedupe_store=InMemoryFeishuInboundDedupeStore(),
        card_delivery_store=FileFeishuCardDeliveryStore(str(tmp_path / "cards.json")),
    )

    outbound = host.handle_event(make_text_event("run stream"))
    progress_messages = [item for item in outbound if item.get("delivery") == "card"]

    assert len(progress_messages) >= 1
    assert any(
        "Tool stream is working..." in item["card"]["elements"][0]["content"]
        for item in client.updated_cards
    )


def test_feishu_gateway_registers_single_adapter_instance(tmp_path: Path) -> None:
    config = FeishuAppConfig(
        app_id="app",
        app_secret="secret",
        session_root=str(tmp_path / "sessions"),
        binding_root=str(tmp_path / "bindings"),
    )

    gateway, _ = create_feishu_gateway(config=config, model=StaticModel(message="hello"))

    adapter = gateway.get_channel_adapter("feishu")
    assert isinstance(adapter, FeishuChannelAdapter)


def test_feishu_host_dedupes_duplicate_user_message(tmp_path: Path) -> None:
    client = FakeFeishuClient()
    config = FeishuAppConfig(
        app_id="app",
        app_secret="secret",
        session_root=str(tmp_path / "sessions"),
        binding_root=str(tmp_path / "bindings"),
    )
    gateway, _ = create_feishu_gateway(config=config, model=StaticModel(message="hello once"))
    adapter = gateway.get_channel_adapter("feishu")
    assert isinstance(adapter, FeishuChannelAdapter)
    adapter.client = client
    host = FeishuLongConnectionHost(
        gateway=gateway,
        adapter=adapter,
        client=client,
        dedupe_store=InMemoryFeishuInboundDedupeStore(),
        card_delivery_store=FileFeishuCardDeliveryStore(str(tmp_path / "cards.json")),
    )

    first = host.handle_event(make_text_event("hello", message_id="om_dup"))
    second = host.handle_event(make_text_event("hello", message_id="om_dup"))

    assert first[-1]["delivery"] == "card"
    assert second == []
    assert [item["reaction_type"] for item in client.added_reactions] == [
        FEISHU_REACTION_IN_PROGRESS,
        FEISHU_REACTION_COMPLETED,
    ]


def test_feishu_host_missing_message_id_skips_dedupe(tmp_path: Path) -> None:
    client = FakeFeishuClient()
    config = FeishuAppConfig(
        app_id="app",
        app_secret="secret",
        session_root=str(tmp_path / "sessions"),
        binding_root=str(tmp_path / "bindings"),
    )
    gateway, _ = create_feishu_gateway(config=config, model=StaticModel(message="hello repeat"))
    adapter = gateway.get_channel_adapter("feishu")
    assert isinstance(adapter, FeishuChannelAdapter)
    adapter.client = client
    host = FeishuLongConnectionHost(
        gateway=gateway,
        adapter=adapter,
        client=client,
        dedupe_store=InMemoryFeishuInboundDedupeStore(),
        card_delivery_store=FileFeishuCardDeliveryStore(str(tmp_path / "cards.json")),
    )
    raw_event = make_text_event("hello", message_id="om_unused")
    message = raw_event["event"]["message"]
    assert isinstance(message, dict)
    message.pop("message_id", None)

    first = host.handle_event(raw_event)
    second = host.handle_event(raw_event)

    assert first == [{"chat_id": "oc_chat_1", "thread_id": None, "text": "hello repeat"}]
    assert second == [{"chat_id": "oc_chat_1", "thread_id": None, "text": "hello repeat"}]


def test_file_backed_feishu_dedupe_store_survives_reopen(tmp_path: Path) -> None:
    storage_path = tmp_path / "dedupe" / "feishu.json"

    first_store = FileFeishuInboundDedupeStore(str(storage_path))
    second_store = FileFeishuInboundDedupeStore(str(storage_path))

    assert first_store.check_and_mark("om_persist") is False
    assert second_store.check_and_mark("om_persist") is True


def test_feishu_runtime_creates_file_backed_runtime(tmp_path: Path) -> None:
    runtime = create_feishu_runtime(
        model=StaticModel(message="profile"),
        session_root=str(tmp_path / "sessions"),
    )
    events, terminal = runtime.run_turn("hello", "sess_feishu")

    assert events[1].payload["message"] == "profile"
    assert terminal.reason == "assistant_message"


def test_feishu_host_retries_pending_card_delivery_and_keeps_reaction_in_progress(
    tmp_path: Path,
) -> None:
    client = FakeFeishuClient()
    client.fail_card_sync_attempts = 4
    config = FeishuAppConfig(
        app_id="app",
        app_secret="secret",
        session_root=str(tmp_path / "sessions"),
        binding_root=str(tmp_path / "bindings"),
        card_state_root=str(tmp_path / "card-state"),
    )
    gateway, _ = create_feishu_gateway(
        config=config,
        model=StaticModel(message="hello after retry"),
    )
    adapter = gateway.get_channel_adapter("feishu")
    assert isinstance(adapter, FeishuChannelAdapter)
    adapter.client = client
    store = FileFeishuCardDeliveryStore(str(tmp_path / "delivery.json"))
    host = FeishuLongConnectionHost(
        gateway=gateway,
        adapter=adapter,
        client=client,
        dedupe_store=InMemoryFeishuInboundDedupeStore(),
        card_delivery_store=store,
        retry_interval_seconds=0.01,
    )

    host.handle_event(make_text_event("hello", message_id="om_retry"))

    assert client.sent_cards == []
    assert [item["reaction_type"] for item in client.added_reactions] == [
        FEISHU_REACTION_IN_PROGRESS
    ]
    sleep(0.02)
    host.retry_pending_cards()

    assert client.sent_cards
    assert client.sent_cards[-1]["card"]["header"]["title"]["content"] == "OpenAgent · Completed"
    assert client.stream_settings == []
    assert [item["reaction_type"] for item in client.added_reactions] == [
        FEISHU_REACTION_IN_PROGRESS,
        FEISHU_REACTION_COMPLETED,
    ]


def test_feishu_host_retries_pending_cards_within_same_conversation_only(tmp_path: Path) -> None:
    client = FakeFeishuClient()
    store = FileFeishuCardDeliveryStore(str(tmp_path / "delivery.json"))
    store.upsert(
        FeishuReplyCardRecord(
            request_message_id="om_private",
            session_id="sess-private",
            conversation_id="feishu:chat:oc_private",
            chat_id="oc_private",
            prompt_text="private prompt",
            reply_message_id="om_reply_private",
            latest_card={"elements": []},
            delivery_pending=True,
        )
    )
    store.upsert(
        FeishuReplyCardRecord(
            request_message_id="om_group",
            session_id="sess-group",
            conversation_id="feishu:chat:oc_group",
            chat_id="oc_group",
            prompt_text="group prompt",
            reply_message_id="om_reply_group",
            latest_card={"elements": []},
            status="completed",
            delivery_pending=True,
        )
    )
    host = FeishuLongConnectionHost(
        gateway=create_feishu_gateway(
            config=FeishuAppConfig(
                app_id="app",
                app_secret="secret",
                session_root=str(tmp_path / "sessions"),
                binding_root=str(tmp_path / "bindings"),
            ),
            model=StaticModel(message="unused"),
        )[0],
        adapter=FeishuChannelAdapter(client=client),
        client=client,
        dedupe_store=InMemoryFeishuInboundDedupeStore(),
        card_delivery_store=store,
    )

    host.retry_pending_cards("feishu:chat:oc_group")

    assert client.updated_cards == [
        {
            "message_id": "om_reply_group",
            "card": {"elements": []},
            "patched": True,
        },
    ]


def test_feishu_host_reuses_existing_record_for_same_request_message(tmp_path: Path) -> None:
    client = FakeFeishuClient()
    store = FileFeishuCardDeliveryStore(str(tmp_path / "delivery.json"))
    config = FeishuAppConfig(
        app_id="app",
        app_secret="secret",
        session_root=str(tmp_path / "sessions"),
        binding_root=str(tmp_path / "bindings"),
    )
    gateway, _ = create_feishu_gateway(config=config, model=StaticModel(message="unused"))
    adapter = gateway.get_channel_adapter("feishu")
    assert isinstance(adapter, FeishuChannelAdapter)
    adapter.client = client
    host = FeishuLongConnectionHost(
        gateway=gateway,
        adapter=adapter,
        client=client,
        dedupe_store=InMemoryFeishuInboundDedupeStore(),
        card_delivery_store=store,
    )
    record = FeishuReplyCardRecord(
        request_message_id="om_retry",
        session_id="sess",
        conversation_id="feishu:chat:oc_chat_1",
        chat_id="oc_chat_1",
        prompt_text="retry me",
        reply_message_id="om_old",
        latest_card={"elements": []},
    )
    store.upsert(record)
    reused = host._ensure_turn_card(
        session_id="sess",
        channel_identity=ChannelIdentity(
            channel_type="feishu",
            conversation_id="feishu:chat:oc_chat_1",
        ),
        delivery_metadata={"chat_id": "oc_chat_1"},
        prompt_text="retry me",
        request_message_id="om_retry",
    )

    assert reused is not None
    assert reused.reply_message_id == "om_old"
    assert client.sent_cards == []


def test_feishu_host_falls_back_to_message_patch_when_cardkit_scope_is_missing(
    tmp_path: Path,
) -> None:
    client = FakeFeishuClient()
    client.resolve_card_error = RuntimeError(
        "Feishu resolve_card_id failed: code=99991672 msg=Access denied. "
        "One of the following scopes is required: [cardkit:card:read]."
    )
    config = FeishuAppConfig(
        app_id="app",
        app_secret="secret",
        session_root=str(tmp_path / "sessions"),
        binding_root=str(tmp_path / "bindings"),
    )
    gateway, _ = create_feishu_gateway(config=config, model=StaticModel(message="hello"))
    adapter = gateway.get_channel_adapter("feishu")
    assert isinstance(adapter, FeishuChannelAdapter)
    adapter.client = client
    store = FileFeishuCardDeliveryStore(str(tmp_path / "delivery.json"))
    host = FeishuLongConnectionHost(
        gateway=gateway,
        adapter=adapter,
        client=client,
        dedupe_store=InMemoryFeishuInboundDedupeStore(),
        card_delivery_store=store,
    )

    host.handle_event(make_text_event("hello", message_id="om_scope"))

    persisted = store.get_by_request_message_id("om_scope")
    assert persisted is not None
    assert persisted.cardkit_supported is False
    assert persisted.reply_message_id is not None
    assert client.sent_cards != []
    assert client.stream_settings == []
    assert client.updated_cards
    assert client.updated_cards[-1]["message_id"] == persisted.reply_message_id
    assert client.updated_cards[-1]["patched"] is True


def test_feishu_host_updates_reply_card_for_assistant_deltas(tmp_path: Path) -> None:
    client = FakeFeishuClient()
    config = FeishuAppConfig(
        app_id="app",
        app_secret="secret",
        session_root=str(tmp_path / "sessions"),
        binding_root=str(tmp_path / "bindings"),
    )
    gateway, _ = create_feishu_gateway(
        config=config,
        model=StreamingAssistantModel(
            chunks=[
                ModelStreamEvent(assistant_delta="hello "),
                ModelStreamEvent(assistant_delta="world"),
            ]
        ),
    )
    adapter = gateway.get_channel_adapter("feishu")
    assert isinstance(adapter, FeishuChannelAdapter)
    adapter.client = client
    store = FileFeishuCardDeliveryStore(str(tmp_path / "delivery.json"))
    host = FeishuLongConnectionHost(
        gateway=gateway,
        adapter=adapter,
        client=client,
        dedupe_store=InMemoryFeishuInboundDedupeStore(),
        card_delivery_store=store,
    )

    host.handle_event(make_text_event("stream hello", message_id="om_stream_delta"))

    assert client.sent_cards != []
    assert len(client.updated_cards) >= 2
    final_record = store.get_by_request_message_id("om_stream_delta")
    assert final_record is not None
    assert final_record.assistant_message == "hello world"
    assert "hello world" in json.dumps(final_record.latest_card, ensure_ascii=False)


def test_feishu_host_batches_assistant_deltas_before_flushing_card(tmp_path: Path) -> None:
    client = FakeFeishuClient()
    client.resolve_card_error = RuntimeError(
        "Feishu resolve_card_id failed: code=99991672 msg=Access denied. "
        "One of the following scopes is required: [cardkit:card:read]."
    )
    clock = FakeClock(value=100.0)
    config = FeishuAppConfig(
        app_id="app",
        app_secret="secret",
        session_root=str(tmp_path / "sessions"),
        binding_root=str(tmp_path / "bindings"),
    )
    gateway, _ = create_feishu_gateway(
        config=config,
        model=StreamingAssistantModel(
            chunks=[
                ModelStreamEvent(assistant_delta="hello "),
                ModelStreamEvent(assistant_delta="world"),
            ]
        ),
    )
    adapter = gateway.get_channel_adapter("feishu")
    assert isinstance(adapter, FeishuChannelAdapter)
    adapter.client = client
    store = FileFeishuCardDeliveryStore(str(tmp_path / "delivery.json"))
    host = FeishuLongConnectionHost(
        gateway=gateway,
        adapter=adapter,
        client=client,
        dedupe_store=InMemoryFeishuInboundDedupeStore(),
        card_delivery_store=store,
        current_time=clock.now,
        stream_flush_interval_seconds=0.15,
    )

    host.handle_event(make_text_event("stream hello", message_id="om_stream_batched"))

    patched_cards = [item for item in client.updated_cards if item.get("patched") is True]
    assert len(patched_cards) == 4
    assert patched_cards[-1]["card"]["header"]["title"]["content"] == "OpenAgent · Completed"
    final_record = store.get_by_request_message_id("om_stream_batched")
    assert final_record is not None
    assert final_record.assistant_message == "hello world"


def test_feishu_host_handles_long_connection_card_action_event(tmp_path: Path) -> None:
    client = FakeFeishuClient()
    config = FeishuAppConfig(
        app_id="app",
        app_secret="secret",
        session_root=str(tmp_path / "sessions"),
        binding_root=str(tmp_path / "bindings"),
    )
    gateway, _ = create_feishu_gateway(
        config=config,
        model=ToolThenReplyModel(),
        tools=[DemoTool(name="admin", permission=PermissionDecision.ASK)],
    )
    adapter = gateway.get_channel_adapter("feishu")
    assert isinstance(adapter, FeishuChannelAdapter)
    adapter.client = client
    store = FileFeishuCardDeliveryStore(str(tmp_path / "delivery.json"))
    host = FeishuLongConnectionHost(
        gateway=gateway,
        adapter=adapter,
        client=client,
        dedupe_store=InMemoryFeishuInboundDedupeStore(),
        card_delivery_store=store,
    )

    host.handle_event(make_text_event("admin rotate", message_id="om_action"))
    reply_message_id = str(client.sent_cards[0]["message_id"])
    result = host.handle_event(
        {
            "header": {"event_type": "card.action.trigger"},
            "event": {
                "operator": {"open_id": "ou_user_1"},
                "action": {"value": {"subtype": "permission_response", "approved": True}},
                "context": {
                    "open_message_id": reply_message_id,
                    "open_chat_id": "oc_chat_1",
                },
            },
        }
    )

    assert result == {"toast": {"type": "info", "content": "Action received."}}
