from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from openagent.gateway import (
    ChannelAdapter,
    ChannelIdentity,
    DesktopChannelAdapter,
    FileSessionBindingStore,
    Gateway,
    InboundEnvelope,
    InProcessSessionAdapter,
    TerminalChannelAdapter,
)
from openagent.harness import ModelTurnRequest, ModelTurnResponse
from openagent.local import (
    create_file_runtime,
    create_gateway_for_runtime,
    create_in_memory_runtime,
)
from openagent.object_model import RuntimeEventType, ToolResult
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


@dataclass(slots=True)
class ToolThenReplyModel:
    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        latest = request.messages[-1]
        role = latest.get("role")
        if role == "tool":
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
class FilteringTerminalChannelAdapter:
    channel_type: str = "terminal"

    def accepted_event_types(self) -> list[str]:
        return [
            RuntimeEventType.ASSISTANT_MESSAGE.value,
            RuntimeEventType.TURN_COMPLETED.value,
        ]


def build_terminal_gateway(
    model: object,
    *,
    tools: list[object] | None = None,
    binding_root: str | None = None,
) -> Gateway:
    runtime = create_in_memory_runtime(model=model, tools=tools)
    return create_gateway_for_runtime(
        runtime,
        [TerminalChannelAdapter()],
        binding_root=binding_root,
    )


def build_desktop_gateway(model: object, *, session_root: str) -> Gateway:
    runtime = create_file_runtime(model=model, session_root=session_root)
    return create_gateway_for_runtime(
        runtime,
        [DesktopChannelAdapter()],
        binding_root=str(Path(session_root) / "bindings"),
    )


def test_tui_gateway_processes_user_message() -> None:
    gateway = build_terminal_gateway(StaticModel(message="hello via gateway"))
    channel = ChannelIdentity(
        channel_type="terminal",
        user_id="user_1",
        conversation_id="conv_tui",
    )
    gateway.bind_session(channel, "sess_gateway_tui")

    egress = gateway.process_user_message(
        InboundEnvelope(
            channel_identity=channel.to_dict(),
            input_kind="user_message",
            payload={"content": "hi"},
            delivery_metadata={"message_id": "msg_1"},
        )
    )

    assert egress[0].event["event_type"] == RuntimeEventType.TURN_STARTED.value
    payload = egress[1].event["payload"]
    assert isinstance(payload, dict)
    assert payload["message"] == "hello via gateway"
    assert egress[-1].event["event_type"] == RuntimeEventType.TURN_COMPLETED.value


def test_tui_profile_registers_terminal_channel_defaults() -> None:
    gateway = build_terminal_gateway(StaticModel(message="hello via gateway"))
    channel = ChannelIdentity(
        channel_type="terminal",
        user_id="user_tui_default",
        conversation_id="conv_tui_default",
    )

    binding = gateway.bind_session(channel, "sess_gateway_tui_default")

    assert binding.event_types == TerminalChannelAdapter().accepted_event_types()


def test_desktop_gateway_uses_file_backed_runtime(tmp_path: Path) -> None:
    gateway = build_desktop_gateway(
        StaticModel(message="desktop gateway"),
        session_root=str(tmp_path / "desktop_sessions"),
    )
    channel = ChannelIdentity(
        channel_type="desktop",
        user_id="user_2",
        conversation_id="conv_desktop",
    )
    binding = gateway.bind_session(channel, "sess_gateway_desktop")

    egress = gateway.process_user_message(
        InboundEnvelope(
            channel_identity=channel.to_dict(),
            input_kind="user_message",
            payload={"content": "open"},
            delivery_metadata={"message_id": "msg_2"},
        )
    )

    assert binding.session_id == "sess_gateway_desktop"
    assert egress[0].conversation_id == "conv_desktop"
    assert egress[-1].event["event_type"] == RuntimeEventType.TURN_COMPLETED.value


def test_desktop_profile_registers_desktop_channel_defaults(tmp_path: Path) -> None:
    gateway = build_desktop_gateway(
        StaticModel(message="desktop gateway"),
        session_root=str(tmp_path / "desktop_sessions"),
    )
    channel = ChannelIdentity(
        channel_type="desktop",
        user_id="user_desktop_default",
        conversation_id="conv_desktop_default",
    )

    binding = gateway.bind_session(channel, "sess_gateway_desktop_default")

    assert binding.event_types == DesktopChannelAdapter().accepted_event_types()


def test_gateway_route_control_accepts_known_subtypes() -> None:
    gateway = build_terminal_gateway(StaticModel(message="noop"))

    accepted = gateway.route_control({"subtype": "permission_response"})
    accepted_resume = gateway.route_control({"subtype": "resume"})
    accepted_mode_change = gateway.route_control({"subtype": "mode_change"})
    rejected = gateway.route_control({"subtype": "unknown"})

    assert accepted["accepted"] is True
    assert accepted_resume["accepted"] is True
    assert accepted_mode_change["accepted"] is True
    assert rejected["accepted"] is False


def test_gateway_processes_permission_continuation() -> None:
    gateway = build_terminal_gateway(
        ToolThenReplyModel(),
        tools=[DemoTool(name="admin", permission=PermissionDecision.ASK)],
    )
    channel = ChannelIdentity(
        channel_type="terminal",
        user_id="user_3",
        conversation_id="conv_permission",
    )
    gateway.bind_session(channel, "sess_gateway_permission")

    first_egress = gateway.process_user_message(
        InboundEnvelope(
            channel_identity=channel.to_dict(),
            input_kind="user_message",
            payload={"content": "admin rotate"},
            delivery_metadata={"message_id": "msg_3"},
        )
    )
    second_egress = gateway.process_control_message(
        channel,
        {"subtype": "permission_response", "approved": True},
    )

    assert first_egress[-1].event["event_type"] == RuntimeEventType.REQUIRES_ACTION.value
    assert second_egress[0].event["event_type"] == RuntimeEventType.TOOL_STARTED.value
    assert second_egress[-1].event["event_type"] == RuntimeEventType.TURN_COMPLETED.value


def test_gateway_process_input_accepts_supplement_input() -> None:
    gateway = build_terminal_gateway(StaticModel(message="supplement seen"))
    channel = ChannelIdentity(
        channel_type="terminal",
        user_id="user_sup",
        conversation_id="conv_supplement",
    )
    gateway.bind_session(channel, "sess_gateway_supplement")

    egress = gateway.process_input(
        InboundEnvelope(
            channel_identity=channel.to_dict(),
            input_kind="supplement_input",
            payload={"content": "one more thing"},
        )
    )

    assert egress[0].event["event_type"] == RuntimeEventType.TURN_STARTED.value
    assert egress[-1].event["event_type"] == RuntimeEventType.TURN_COMPLETED.value


def test_gateway_projects_tool_progress_events() -> None:
    gateway = build_terminal_gateway(
        ToolProgressModel(),
        tools=[StreamingTool(name="stream")],
    )
    channel = ChannelIdentity(
        channel_type="terminal",
        user_id="user_progress",
        conversation_id="conv_progress",
    )
    gateway.bind_session(channel, "sess_gateway_progress")

    egress = gateway.process_user_message(
        InboundEnvelope(
            channel_identity=channel.to_dict(),
            input_kind="user_message",
            payload={"content": "run stream"},
        )
    )

    assert RuntimeEventType.TOOL_PROGRESS.value in [item.event["event_type"] for item in egress]


def test_gateway_filters_and_replays_projected_events() -> None:
    gateway = build_terminal_gateway(StaticModel(message="visible reply"))
    channel = ChannelIdentity(
        channel_type="terminal",
        user_id="user_4",
        conversation_id="conv_filtered",
    )
    gateway.bind_session(
        channel,
        "sess_gateway_filtered",
        event_types=[
            RuntimeEventType.ASSISTANT_MESSAGE.value,
            RuntimeEventType.TURN_COMPLETED.value,
        ],
        adapter_name="terminal-tui",
    )

    egress = gateway.process_user_message(
        InboundEnvelope(
            channel_identity=channel.to_dict(),
            input_kind="user_message",
            payload={"content": "hi"},
        )
    )
    replay = gateway.observe_session(channel)

    assert [item.event["event_type"] for item in egress] == [
        RuntimeEventType.ASSISTANT_MESSAGE.value,
        RuntimeEventType.TURN_COMPLETED.value,
    ]
    assert [item.event["event_type"] for item in replay] == [
        RuntimeEventType.ASSISTANT_MESSAGE.value,
        RuntimeEventType.TURN_COMPLETED.value,
    ]
    assert replay[0].session_id == "sess_gateway_filtered"
    binding = gateway.bind_session(
        channel,
        "sess_gateway_filtered",
        event_types=[
            RuntimeEventType.ASSISTANT_MESSAGE.value,
            RuntimeEventType.TURN_COMPLETED.value,
        ],
        adapter_name="terminal-tui",
    )
    assert binding.checkpoint_event_offset == 3


def test_gateway_registers_channel_defaults_for_bindings() -> None:
    runtime = create_in_memory_runtime(model=StaticModel(message="hello"))
    gateway = Gateway(InProcessSessionAdapter(runtime))
    channel_adapter: ChannelAdapter = FilteringTerminalChannelAdapter()
    channel = ChannelIdentity(
        channel_type="terminal",
        user_id="user_default_events",
        conversation_id="conv_default_events",
    )

    gateway.register_channel(channel_adapter)
    binding = gateway.bind_session(channel, "sess_default_events")

    assert binding.event_types == [
        RuntimeEventType.ASSISTANT_MESSAGE.value,
        RuntimeEventType.TURN_COMPLETED.value,
    ]


def test_gateway_resumes_bound_session_from_explicit_offset() -> None:
    gateway = build_terminal_gateway(StaticModel(message="resume replay"))
    channel = ChannelIdentity(
        channel_type="terminal",
        user_id="user_resume",
        conversation_id="conv_resume",
    )
    gateway.bind_session(channel, "sess_gateway_resume")
    gateway.process_user_message(
        InboundEnvelope(
            channel_identity=channel.to_dict(),
            input_kind="user_message",
            payload={"content": "hi"},
        )
    )

    replay = gateway.resume_bound_session(channel, after=0)

    assert replay[0].event["event_type"] == RuntimeEventType.TURN_STARTED.value
    assert replay[-1].event["event_type"] == RuntimeEventType.TURN_COMPLETED.value


def test_gateway_process_control_resume_replays_events() -> None:
    gateway = build_terminal_gateway(StaticModel(message="resume control"))
    channel = ChannelIdentity(
        channel_type="terminal",
        user_id="user_resume_control",
        conversation_id="conv_resume_control",
    )
    gateway.bind_session(channel, "sess_gateway_resume_control")
    gateway.process_user_message(
        InboundEnvelope(
            channel_identity=channel.to_dict(),
            input_kind="user_message",
            payload={"content": "hi"},
        )
    )

    replay = gateway.process_control_message(channel, {"subtype": "resume", "after": 0})

    assert replay[0].event["event_type"] == RuntimeEventType.TURN_STARTED.value
    assert replay[-1].event["event_type"] == RuntimeEventType.TURN_COMPLETED.value


def test_gateway_restores_persisted_binding_after_restart(tmp_path: Path) -> None:
    binding_root = tmp_path / "bindings"
    first_gateway = build_terminal_gateway(
        StaticModel(message="hello via persisted binding"),
        binding_root=str(binding_root),
    )
    channel = ChannelIdentity(
        channel_type="terminal",
        user_id="user_5",
        conversation_id="conv_persisted",
    )
    first_gateway.bind_session(channel, "sess_gateway_persisted")
    first_gateway.process_user_message(
        InboundEnvelope(
            channel_identity=channel.to_dict(),
            input_kind="user_message",
            payload={"content": "hi"},
        )
    )

    restored_gateway = build_terminal_gateway(
        StaticModel(message="hello via persisted binding"),
        binding_root=str(binding_root),
    )
    egress = restored_gateway.process_user_message(
        InboundEnvelope(
            channel_identity=channel.to_dict(),
            input_kind="user_message",
            payload={"content": "hi"},
        )
    )

    assert egress[0].session_id == "sess_gateway_persisted"
    assert egress[-1].event["event_type"] == RuntimeEventType.TURN_COMPLETED.value
    restored_binding = FileSessionBindingStore(binding_root).load_binding(
        "terminal",
        "conv_persisted",
    )
    assert restored_binding is not None
    assert restored_binding.checkpoint_event_offset >= 3
    assert restored_binding.checkpoint_last_event_id is not None


def test_gateway_get_binding_restores_persisted_binding(tmp_path: Path) -> None:
    binding_root = tmp_path / "bindings"
    first_gateway = build_terminal_gateway(
        StaticModel(message="hello binding"),
        binding_root=str(binding_root),
    )
    channel = ChannelIdentity(
        channel_type="terminal",
        user_id="user_binding_restore",
        conversation_id="conv_binding_restore",
    )
    first_gateway.bind_session(channel, "sess_binding_restore")

    restored_gateway = build_terminal_gateway(
        StaticModel(message="hello binding"),
        binding_root=str(binding_root),
    )
    restored = restored_gateway.get_binding("terminal", "conv_binding_restore")

    assert restored.session_id == "sess_binding_restore"


def test_desktop_gateway_persists_binding_store_by_default(tmp_path: Path) -> None:
    session_root = tmp_path / "desktop_sessions"
    gateway = build_desktop_gateway(
        StaticModel(message="desktop gateway"),
        session_root=str(session_root),
    )
    channel = ChannelIdentity(
        channel_type="desktop",
        user_id="user_6",
        conversation_id="conv_desktop_binding",
    )
    gateway.bind_session(channel, "sess_gateway_desktop_binding")

    store = FileSessionBindingStore(session_root / "bindings")
    restored = store.load_binding("desktop", "conv_desktop_binding")

    assert restored is not None
    assert restored.session_id == "sess_gateway_desktop_binding"


def test_gateway_enforces_one_chat_one_session() -> None:
    runtime = create_in_memory_runtime(model=StaticModel(message="x"))
    gateway = Gateway(InProcessSessionAdapter(runtime))
    channel = ChannelIdentity(
        channel_type="terminal",
        user_id="user_7",
        conversation_id="conv_unique",
    )

    gateway.bind_session(channel, "sess_one")
    try:
        gateway.bind_session(channel, "sess_two")
    except ValueError as exc:
        assert "one session" in str(exc)
    else:
        raise AssertionError("Expected ValueError")
