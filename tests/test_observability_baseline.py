import io
from collections.abc import Iterator
from dataclasses import dataclass, field

from openagent.harness import ModelStreamEvent, ModelTurnRequest, ModelTurnResponse, SimpleHarness
from openagent.object_model import ToolResult
from openagent.observability import (
    AgentObservability,
    InMemoryObservabilitySink,
    ProgressUpdate,
    StdoutObservabilitySink,
)
from openagent.orchestration import InMemoryTaskManager, LocalBackgroundAgentOrchestrator
from openagent.session import InMemorySessionStore
from openagent.tools import (
    PermissionDecision,
    SimpleToolExecutor,
    StaticToolRegistry,
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
class StreamingModel:
    chunks: list[ModelStreamEvent]

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        del request
        raise AssertionError("stream_generate should be used")

    def stream_generate(self, request: ModelTurnRequest) -> Iterator[ModelStreamEvent]:
        del request
        yield from self.chunks


@dataclass(slots=True)
class AskTool:
    name: str = "admin"
    input_schema: dict[str, str] = field(default_factory=lambda: {"type": "object"})

    def description(self) -> str:
        return self.name

    def call(self, arguments: dict[str, object]) -> ToolResult:
        return ToolResult(tool_name=self.name, success=True, content=[str(arguments)])

    def check_permissions(self, arguments: dict[str, object]) -> str:
        del arguments
        return PermissionDecision.ASK.value

    def is_concurrency_safe(self) -> bool:
        return True


@dataclass(slots=True)
class StreamingTool:
    name: str = "stream"
    input_schema: dict[str, str] = field(default_factory=lambda: {"type": "object"})

    def description(self) -> str:
        return self.name

    def call(self, arguments: dict[str, object]) -> ToolResult:
        return ToolResult(tool_name=self.name, success=True, content=[str(arguments)])

    def check_permissions(self, arguments: dict[str, object]) -> str:
        del arguments
        return PermissionDecision.ALLOW.value

    def is_concurrency_safe(self) -> bool:
        return True

    def stream_call(
        self,
        arguments: dict[str, object],
        context: ToolExecutionContext,
    ) -> Iterator[ToolStreamItem]:
        del arguments
        del context
        yield ToolStreamItem(
            progress=ToolProgressUpdate(
                tool_name=self.name,
                message="working",
                progress=0.5,
            )
        )
        yield ToolStreamItem(
            result=ToolResult(tool_name=self.name, success=True, content=["done"])
        )


@dataclass(slots=True)
class ToolOnlyModel:
    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        del request
        return ModelTurnResponse(
            tool_calls=[ToolCall(tool_name="admin", arguments={"text": "rotate"})]
        )


@dataclass(slots=True)
class ToolThenReplyModel:
    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        if request.messages[-1]["role"] == "user":
            return ModelTurnResponse(
                tool_calls=[ToolCall(tool_name="stream", arguments={"text": "run"})]
            )
        return ModelTurnResponse(assistant_message="done")


def build_harness(
    model: object,
    tools: list[object],
    sink: InMemoryObservabilitySink,
) -> SimpleHarness:
    registry = StaticToolRegistry(tools)
    executor = SimpleToolExecutor(registry)
    return SimpleHarness(
        model=model,  # type: ignore[arg-type]
        sessions=InMemorySessionStore(),
        tools=registry,
        executor=executor,
        observability=AgentObservability([sink]),
    )


def test_harness_emits_session_states_and_interaction_spans() -> None:
    sink = InMemoryObservabilitySink()
    harness = build_harness(StaticModel(message="hello"), [], sink)

    harness.run_turn("hi", "sess_observe")

    session_states = [event.payload["state"] for event in sink.list_by_kind("session_state")]
    span_kinds = sink.list_by_kind("span_started")

    assert session_states == ["running", "idle"]
    assert span_kinds[0].payload["span_type"] == "interaction"


def test_requires_action_emits_blocked_session_state() -> None:
    sink = InMemoryObservabilitySink()
    harness = build_harness(
        ToolOnlyModel(),
        [AskTool()],
        sink,
    )

    harness.run_turn("admin rotate", "sess_requires_action")

    session_states = [event.payload["state"] for event in sink.list_by_kind("session_state")]

    assert session_states == ["running", "requires_action"]


def test_streaming_model_emits_llm_span_and_metric() -> None:
    sink = InMemoryObservabilitySink()
    harness = build_harness(
        StreamingModel(chunks=[
            ModelStreamEvent(assistant_delta="hello "),
            ModelStreamEvent(assistant_delta="world"),
        ]),
        [],
        sink,
    )

    harness.run_turn("stream", "sess_stream_obs")

    ended_spans = sink.list_by_kind("span_ended")
    metrics = sink.list_by_kind("metric")

    assert any(event.payload["span_type"] == "llm_request" for event in ended_spans)
    assert any(event.payload["name"] == "llm_request.duration_ms" for event in metrics)


def test_tool_progress_and_tool_span_are_emitted() -> None:
    sink = InMemoryObservabilitySink()
    harness = build_harness(ToolThenReplyModel(), [StreamingTool()], sink)

    harness.run_turn("run stream", "sess_tool_obs")

    progress_events = sink.list_by_kind("progress")
    ended_spans = sink.list_by_kind("span_ended")

    assert any(event.payload["scope"] == "tool" for event in progress_events)
    assert any(event.payload["span_type"] == "tool" for event in ended_spans)


def test_background_orchestrator_emits_progress_and_trace() -> None:
    sink = InMemoryObservabilitySink()
    orchestrator = LocalBackgroundAgentOrchestrator(
        InMemoryTaskManager(),
        observability=AgentObservability([sink]),
    )
    handle = orchestrator.start_background_task(
        "crawl",
        lambda context: (context.progress({"summary": "step one"}) or {"output_ref": "done"}),
    )
    orchestrator._futures[handle.task_id].result(timeout=1)

    external_events = sink.list_by_kind("external_event")
    progress_events = sink.list_by_kind("progress")
    ended_spans = sink.list_by_kind("span_ended")

    assert any(event.payload["event_type"] == "task_created" for event in external_events)
    assert any(event.payload["scope"] == "background_agent" for event in progress_events)
    assert any(event.payload["span_type"] == "background_task" for event in ended_spans)


def test_stdout_sink_writes_structured_json_lines() -> None:
    stream = io.StringIO()
    sink = StdoutObservabilitySink(stream=stream)
    observability = AgentObservability([sink])

    observability.emit_progress(
        ProgressUpdate(scope="turn", summary="hello", last_activity="turn_started")
    )

    output = stream.getvalue().strip()

    assert '"kind": "progress"' in output
