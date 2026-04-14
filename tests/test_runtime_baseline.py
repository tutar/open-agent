from dataclasses import dataclass, field

from openagent.harness import ModelTurnRequest, ModelTurnResponse, SimpleHarness
from openagent.object_model import JsonObject, RuntimeEventType, TerminalStatus, ToolResult
from openagent.session import InMemorySessionStore
from openagent.tools import (
    PermissionDecision,
    SimpleToolExecutor,
    StaticToolRegistry,
    ToolCall,
    ToolExecutionContext,
    ToolPermissionDeniedError,
)


@dataclass(slots=True)
class FakeTool:
    name: str
    permission: PermissionDecision = PermissionDecision.ALLOW
    concurrency_safe: bool = True
    input_schema: JsonObject = field(default_factory=lambda: {"type": "object"})
    seen_arguments: list[dict[str, object]] = field(default_factory=list)

    def description(self) -> str:
        return f"Fake tool {self.name}"

    def call(self, arguments: dict[str, object]) -> ToolResult:
        self.seen_arguments.append(arguments)
        text = arguments.get("text", "ok")
        return ToolResult(tool_name=self.name, success=True, content=[str(text)])

    def check_permissions(self, arguments: dict[str, object]) -> str:
        del arguments
        return self.permission.value

    def is_concurrency_safe(self) -> bool:
        return self.concurrency_safe


@dataclass(slots=True)
class ScriptedModel:
    responses: list[ModelTurnResponse]

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        del request
        return self.responses.pop(0)


def test_simple_harness_basic_turn() -> None:
    session_store = InMemorySessionStore()
    tools = StaticToolRegistry([])
    executor = SimpleToolExecutor(tools)
    model = ScriptedModel(responses=[ModelTurnResponse(assistant_message="hello from model")])
    harness = SimpleHarness(model=model, sessions=session_store, tools=tools, executor=executor)

    events, terminal = harness.run_turn("hi", "sess_basic")

    assert terminal.status is TerminalStatus.COMPLETED
    assert [event.event_type for event in events] == [
        RuntimeEventType.TURN_STARTED,
        RuntimeEventType.ASSISTANT_MESSAGE,
        RuntimeEventType.TURN_COMPLETED,
    ]
    assert session_store.load_session("sess_basic").messages[-1].content == "hello from model"


def test_simple_harness_tool_roundtrip() -> None:
    echo = FakeTool(name="echo")
    session_store = InMemorySessionStore()
    tools = StaticToolRegistry([echo])
    executor = SimpleToolExecutor(tools)
    model = ScriptedModel(
        responses=[
            ModelTurnResponse(
                tool_calls=[ToolCall(tool_name="echo", arguments={"text": "payload"})]
            ),
            ModelTurnResponse(assistant_message="tool completed"),
        ]
    )
    harness = SimpleHarness(model=model, sessions=session_store, tools=tools, executor=executor)

    events, terminal = harness.run_turn("use tool", "sess_tool")

    assert terminal.status is TerminalStatus.COMPLETED
    assert [event.event_type for event in events] == [
        RuntimeEventType.TURN_STARTED,
        RuntimeEventType.TOOL_STARTED,
        RuntimeEventType.TOOL_RESULT,
        RuntimeEventType.ASSISTANT_MESSAGE,
        RuntimeEventType.TURN_COMPLETED,
    ]
    assert echo.seen_arguments == [{"text": "payload"}]


def test_simple_harness_requires_action_blocks_turn() -> None:
    privileged = FakeTool(name="admin", permission=PermissionDecision.ASK)
    session_store = InMemorySessionStore()
    tools = StaticToolRegistry([privileged])
    executor = SimpleToolExecutor(tools)
    model = ScriptedModel(
        responses=[
            ModelTurnResponse(tool_calls=[ToolCall(tool_name="admin", arguments={"op": "restart"})])
        ]
    )
    harness = SimpleHarness(model=model, sessions=session_store, tools=tools, executor=executor)

    events, terminal = harness.run_turn("restart service", "sess_ask")

    assert terminal.status is TerminalStatus.BLOCKED
    assert events[-1].event_type is RuntimeEventType.REQUIRES_ACTION


def test_tool_permission_denied_raises_failed_terminal_state() -> None:
    denied = FakeTool(name="rm", permission=PermissionDecision.DENY)
    session_store = InMemorySessionStore()
    tools = StaticToolRegistry([denied])
    executor = SimpleToolExecutor(tools)
    model = ScriptedModel(
        responses=[
            ModelTurnResponse(tool_calls=[ToolCall(tool_name="rm", arguments={"path": "/tmp/x"})])
        ]
    )
    harness = SimpleHarness(model=model, sessions=session_store, tools=tools, executor=executor)

    events, terminal = harness.run_turn("delete file", "sess_deny")

    assert [event.event_type for event in events] == [
        RuntimeEventType.TURN_STARTED,
        RuntimeEventType.TOOL_STARTED,
        RuntimeEventType.TURN_FAILED,
    ]
    assert terminal.status is TerminalStatus.FAILED
    assert terminal.reason == "tool_permission_denied"


def test_route_tool_call_returns_single_result() -> None:
    echo = FakeTool(name="echo")
    registry = StaticToolRegistry([echo])
    harness = SimpleHarness(
        model=ScriptedModel(responses=[]),
        sessions=InMemorySessionStore(),
        tools=registry,
        executor=SimpleToolExecutor(registry),
    )

    result = harness.route_tool_call(ToolCall(tool_name="echo", arguments={"text": "single"}))

    assert result.content == ["single"]


def test_tool_executor_rejects_denied_tool() -> None:
    denied = FakeTool(name="denied", permission=PermissionDecision.DENY)
    executor = SimpleToolExecutor(StaticToolRegistry([denied]))

    try:
        executor.run_tools(
            [ToolCall(tool_name="denied", arguments={})],
            ToolExecutionContext(session_id="sess_x"),
        )
    except ToolPermissionDeniedError as exc:
        assert "Permission denied" in str(exc)
    else:
        raise AssertionError("Expected ToolPermissionDeniedError")
