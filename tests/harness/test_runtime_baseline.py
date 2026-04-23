import time
from collections.abc import Iterator
from dataclasses import dataclass, field

from openagent.harness.runtime import (
    ModelStreamEvent,
    ModelTurnRequest,
    ModelTurnResponse,
    RalphLoop,
    SimpleHarness,
    TurnControl,
)
from openagent.object_model import JsonObject, RuntimeEventType, TerminalStatus, ToolResult
from openagent.session import InMemorySessionStore
from openagent.tools import (
    BashTool,
    PermissionDecision,
    SimpleToolExecutor,
    StaticToolRegistry,
    ToolCall,
    ToolExecutionContext,
    ToolPermissionDeniedError,
    WebSearchTool,
)
from openagent.tools.web import WebSearchBackendError


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


@dataclass(slots=True)
class StreamingScriptedModel:
    chunks: list[ModelStreamEvent]

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        del request
        raise AssertionError("Streaming path should use stream_generate")

    def stream_generate(self, request: ModelTurnRequest) -> Iterator[ModelStreamEvent]:
        del request
        yield from self.chunks


@dataclass(slots=True)
class SleepyModel:
    delay_seconds: float

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        del request
        time.sleep(self.delay_seconds)
        return ModelTurnResponse(assistant_message="too late")


@dataclass(slots=True)
class FlakyModel:
    failures_before_success: int
    attempts: int = 0

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        del request
        self.attempts += 1
        if self.attempts <= self.failures_before_success:
            raise RuntimeError(f"boom-{self.attempts}")
        return ModelTurnResponse(assistant_message="recovered")


@dataclass(slots=True)
class FailingSearchBackend:
    def search(self, query: str) -> list[object]:
        del query
        raise WebSearchBackendError("HTTP 502: upstream unavailable")


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
    assert isinstance(harness.runtime_loop, RalphLoop)
    assert harness.runtime_loop.state.transition == "completed"


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


def test_simple_harness_continues_after_websearch_backend_failure() -> None:
    session_store = InMemorySessionStore()
    tools = StaticToolRegistry([WebSearchTool(backend=FailingSearchBackend())])
    executor = SimpleToolExecutor(tools)
    model = ScriptedModel(
        responses=[
            ModelTurnResponse(
                tool_calls=[ToolCall(tool_name="WebSearch", arguments={"query": "latest news"})]
            ),
            ModelTurnResponse(assistant_message="search backend is unavailable right now"),
        ]
    )
    harness = SimpleHarness(model=model, sessions=session_store, tools=tools, executor=executor)

    events, terminal = harness.run_turn("search", "sess_websearch_failure")

    assert terminal.status is TerminalStatus.COMPLETED
    assert [event.event_type for event in events] == [
        RuntimeEventType.TURN_STARTED,
        RuntimeEventType.TOOL_STARTED,
        RuntimeEventType.TOOL_RESULT,
        RuntimeEventType.ASSISTANT_MESSAGE,
        RuntimeEventType.TURN_COMPLETED,
    ]
    tool_payload = events[2].payload
    assert tool_payload["success"] is False
    assert tool_payload["content"] == ["HTTP 502: upstream unavailable"]


def test_simple_harness_skips_empty_assistant_event_before_tool_failure() -> None:
    session_store = InMemorySessionStore()
    tools = StaticToolRegistry([BashTool(".")])
    executor = SimpleToolExecutor(tools)
    model = ScriptedModel(
        responses=[
            ModelTurnResponse(
                assistant_message="",
                tool_calls=[ToolCall(tool_name="Bash", arguments={})],
            )
        ]
    )
    harness = SimpleHarness(model=model, sessions=session_store, tools=tools, executor=executor)

    events, terminal = harness.run_turn("list files", "sess_tool_fail")

    assert terminal.status is TerminalStatus.FAILED
    assert terminal.reason == "tool_execution_failed"
    assert [event.event_type for event in events] == [
        RuntimeEventType.TURN_STARTED,
        RuntimeEventType.TURN_FAILED,
    ]
    assert [message.role for message in session_store.load_session("sess_tool_fail").messages] == [
        "user"
    ]


def test_simple_harness_skips_nonempty_assistant_event_when_model_also_requests_tool() -> None:
    echo = FakeTool(name="echo")
    session_store = InMemorySessionStore()
    tools = StaticToolRegistry([echo])
    executor = SimpleToolExecutor(tools)
    model = ScriptedModel(
        responses=[
            ModelTurnResponse(
                assistant_message="Let me check that for you.",
                tool_calls=[ToolCall(tool_name="echo", arguments={"text": "payload"})],
            ),
            ModelTurnResponse(assistant_message="tool completed"),
        ]
    )
    harness = SimpleHarness(model=model, sessions=session_store, tools=tools, executor=executor)

    events, terminal = harness.run_turn("use tool", "sess_tool_preface")

    assert terminal.status is TerminalStatus.COMPLETED
    assert [event.event_type for event in events] == [
        RuntimeEventType.TURN_STARTED,
        RuntimeEventType.TOOL_STARTED,
        RuntimeEventType.TOOL_RESULT,
        RuntimeEventType.ASSISTANT_MESSAGE,
        RuntimeEventType.TURN_COMPLETED,
    ]
    messages = session_store.load_session("sess_tool_preface").messages
    assert [message.role for message in messages] == ["user", "tool", "assistant"]
    assert messages[-1].content == "tool completed"


def test_simple_harness_streaming_model_emits_deltas() -> None:
    session_store = InMemorySessionStore()
    tools = StaticToolRegistry([])
    executor = SimpleToolExecutor(tools)
    model = StreamingScriptedModel(
        chunks=[
            ModelStreamEvent(assistant_delta="hello "),
            ModelStreamEvent(assistant_delta="world"),
        ]
    )
    harness = SimpleHarness(model=model, sessions=session_store, tools=tools, executor=executor)

    events, terminal = harness.run_turn("stream please", "sess_stream")

    assert terminal.status is TerminalStatus.COMPLETED
    assert [event.event_type for event in events] == [
        RuntimeEventType.TURN_STARTED,
        RuntimeEventType.ASSISTANT_DELTA,
        RuntimeEventType.ASSISTANT_DELTA,
        RuntimeEventType.ASSISTANT_MESSAGE,
        RuntimeEventType.TURN_COMPLETED,
    ]
    assert events[3].payload["message"] == "hello world"


def test_simple_harness_cancellation_stops_turn() -> None:
    session_store = InMemorySessionStore()
    tools = StaticToolRegistry([])
    executor = SimpleToolExecutor(tools)
    model = ScriptedModel(responses=[ModelTurnResponse(assistant_message="never emitted")])
    harness = SimpleHarness(model=model, sessions=session_store, tools=tools, executor=executor)

    events, terminal = harness.run_turn(
        "cancel me",
        "sess_cancelled",
        control=TurnControl(cancellation_check=lambda: True),
    )

    assert terminal.status is TerminalStatus.STOPPED
    assert terminal.reason == "cancelled"
    assert [event.event_type for event in events] == [
        RuntimeEventType.TURN_STARTED,
        RuntimeEventType.TURN_FAILED,
    ]


def test_simple_harness_timeout_fails_turn() -> None:
    session_store = InMemorySessionStore()
    tools = StaticToolRegistry([])
    executor = SimpleToolExecutor(tools)
    harness = SimpleHarness(
        model=SleepyModel(delay_seconds=0.05),
        sessions=session_store,
        tools=tools,
        executor=executor,
    )

    events, terminal = harness.run_turn(
        "wait",
        "sess_timeout",
        control=TurnControl(timeout_seconds=0.001),
    )

    assert terminal.status is TerminalStatus.FAILED
    assert terminal.reason == "timeout"
    assert terminal.retryable is True
    assert events[-1].event_type is RuntimeEventType.TURN_FAILED


def test_simple_harness_retries_and_recovers() -> None:
    session_store = InMemorySessionStore()
    tools = StaticToolRegistry([])
    executor = SimpleToolExecutor(tools)
    model = FlakyModel(failures_before_success=1)
    harness = SimpleHarness(model=model, sessions=session_store, tools=tools, executor=executor)

    events, terminal = harness.run_turn(
        "retry",
        "sess_retry_success",
        control=TurnControl(max_retries=1),
    )

    assert model.attempts == 2
    assert terminal.status is TerminalStatus.COMPLETED
    assert events[-1].event_type is RuntimeEventType.TURN_COMPLETED


def test_simple_harness_retry_exhaustion_fails_turn() -> None:
    session_store = InMemorySessionStore()
    tools = StaticToolRegistry([])
    executor = SimpleToolExecutor(tools)
    model = FlakyModel(failures_before_success=3)
    harness = SimpleHarness(model=model, sessions=session_store, tools=tools, executor=executor)

    events, terminal = harness.run_turn(
        "retry until fail",
        "sess_retry_fail",
        control=TurnControl(max_retries=1),
    )

    assert model.attempts == 2
    assert terminal.status is TerminalStatus.FAILED
    assert terminal.reason == "retry_exhausted"
    assert events[-1].event_type is RuntimeEventType.TURN_FAILED


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
