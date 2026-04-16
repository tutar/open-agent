import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from openagent import create_feishu_runtime
from openagent.gateway import (
    FeishuAppConfig,
    FeishuChannelAdapter,
    FeishuLongConnectionHost,
    create_feishu_gateway,
)
from openagent.harness import ModelTurnRequest, ModelTurnResponse
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
        self.started = False
        self.handler: Callable[[dict[str, object]], None] | None = None

    def start(self, event_handler: Callable[[dict[str, object]], None]) -> None:
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


def test_feishu_adapter_maps_commands_to_control_payloads() -> None:
    adapter = FeishuChannelAdapter()

    approve = adapter.normalize_inbound(make_text_event("/approve"))
    reject = adapter.normalize_inbound(make_text_event("/reject", message_id="om_2"))
    interrupt = adapter.normalize_inbound(make_text_event("/interrupt", message_id="om_3"))
    resume = adapter.normalize_inbound(make_text_event("/resume", message_id="om_4"))

    assert approve is not None and approve.payload == {
        "subtype": "permission_response",
        "approved": True,
    }
    assert reject is not None and reject.payload == {
        "subtype": "permission_response",
        "approved": False,
    }
    assert interrupt is not None and interrupt.payload == {"subtype": "interrupt"}
    assert resume is not None and resume.payload == {"subtype": "resume", "after": 0}


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
    host = FeishuLongConnectionHost(gateway=gateway, adapter=adapter, client=client)

    outbound = host.handle_event(make_text_event("hello"))

    assert outbound == [{"chat_id": "oc_chat_1", "thread_id": None, "text": "hello via feishu"}]
    binding = gateway.get_binding("feishu", "feishu:chat:oc_chat_1")
    assert binding.session_id == "feishu-session:feishu:chat:oc_chat_1"


def test_feishu_host_supports_command_approval(tmp_path: Path) -> None:
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
    host = FeishuLongConnectionHost(gateway=gateway, adapter=adapter, client=client)

    first = host.handle_event(make_text_event("admin rotate"))
    second = host.handle_event(make_text_event("/approve", message_id="om_approve"))

    assert first[-1]["text"] == "Tool approval required for admin. Reply /approve or /reject."
    assert second[-1]["text"] == "tool completed after approval"


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
    )

    outbound = host.handle_event(make_text_event("/channel"))

    assert outbound == [{"chat_id": "oc_chat_1", "thread_id": None, "text": "handled /channel"}]


def test_feishu_host_resume_replays_session(tmp_path: Path) -> None:
    client = FakeFeishuClient()
    config = FeishuAppConfig(
        app_id="app",
        app_secret="secret",
        session_root=str(tmp_path / "sessions"),
        binding_root=str(tmp_path / "bindings"),
    )
    gateway, _ = create_feishu_gateway(config=config, model=StaticModel(message="hello replay"))
    adapter = gateway.get_channel_adapter("feishu")
    assert isinstance(adapter, FeishuChannelAdapter)
    adapter.client = client
    host = FeishuLongConnectionHost(gateway=gateway, adapter=adapter, client=client)

    host.handle_event(make_text_event("hello"))
    replay = host.handle_event(make_text_event("/resume", message_id="om_resume"))

    assert replay[0]["text"] == "hello replay"


def test_feishu_host_reports_missing_session_for_control(tmp_path: Path) -> None:
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
    host = FeishuLongConnectionHost(gateway=gateway, adapter=adapter, client=client)

    outbound = host.handle_event(make_text_event("/approve"))

    assert outbound == [
        {
            "chat_id": "oc_chat_1",
            "thread_id": None,
            "text": "No active session is bound for this chat yet. Send a normal message first.",
        }
    ]


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
    host = FeishuLongConnectionHost(gateway=gateway, adapter=adapter, client=client)

    outbound = host.handle_event(make_text_event("run stream"))
    progress_messages = [item for item in outbound if item["text"] == "Tool stream is working..."]

    assert len(progress_messages) == 1


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


def test_feishu_runtime_creates_file_backed_runtime(tmp_path: Path) -> None:
    runtime = create_feishu_runtime(
        model=StaticModel(message="profile"),
        session_root=str(tmp_path / "sessions"),
    )
    events, terminal = runtime.run_turn("hello", "sess_feishu")

    assert events[1].payload["message"] == "profile"
    assert terminal.reason == "assistant_message"
