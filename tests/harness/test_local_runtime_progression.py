from collections.abc import Iterator
from dataclasses import dataclass, field

from openagent.harness.runtime import ModelTurnRequest, ModelTurnResponse
from openagent.object_model import RuntimeEventType, ToolResult
from openagent.tools import (
    PermissionDecision,
    RuleBasedToolPolicyEngine,
    SimpleToolExecutor,
    StaticToolRegistry,
    ToolCall,
    ToolCancelledError,
    ToolExecutionContext,
    ToolPolicyOutcome,
    ToolPolicyRule,
    ToolProgressUpdate,
    ToolStreamItem,
)


@dataclass(slots=True)
class FakeTool:
    name: str
    permission: PermissionDecision = PermissionDecision.ALLOW
    concurrency_safe: bool = True
    input_schema: dict[str, object] = field(default_factory=lambda: {"type": "object"})

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
        return self.concurrency_safe


@dataclass(slots=True)
class ScriptedModel:
    responses: list[ModelTurnResponse]

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        del request
        return self.responses.pop(0)


@dataclass(slots=True)
class StreamingTool:
    name: str = "streaming"
    permission: PermissionDecision = PermissionDecision.ALLOW
    input_schema: dict[str, object] = field(default_factory=lambda: {"type": "object"})

    def description(self) -> str:
        return self.name

    def check_permissions(self, arguments: dict[str, object]) -> str:
        del arguments
        return self.permission.value

    def is_concurrency_safe(self) -> bool:
        return False

    def call(self, arguments: dict[str, object]) -> ToolResult:
        return ToolResult(
            tool_name=self.name,
            success=True,
            content=[str(arguments.get("text", "ok"))],
        )

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
class FailingTool(StreamingTool):
    name: str = "failing"

    def stream_call(
        self,
        arguments: dict[str, object],
        context: ToolExecutionContext,
    ) -> Iterator[ToolStreamItem]:
        del arguments, context
        raise RuntimeError("boom")


@dataclass(slots=True)
class CancelledTool(StreamingTool):
    name: str = "cancelled"

    def stream_call(
        self,
        arguments: dict[str, object],
        context: ToolExecutionContext,
    ) -> Iterator[ToolStreamItem]:
        del arguments, context
        raise ToolCancelledError(tool_name=self.name)


@dataclass(slots=True)
class TrackingTool(FakeTool):
    trace: list[str] = field(default_factory=list)

    def call(self, arguments: dict[str, object]) -> ToolResult:
        label = str(arguments.get("label", self.name))
        self.trace.append(f"start:{label}")
        self.trace.append(f"end:{label}")
        return ToolResult(tool_name=self.name, success=True, content=[label])


@dataclass(slots=True)
class StaticPolicyEngine:
    outcome: ToolPolicyOutcome

    def evaluate(
        self,
        tool: object,
        tool_call: ToolCall,
        context: ToolExecutionContext,
    ) -> ToolPolicyOutcome:
        del tool, tool_call, context
        return self.outcome


def test_tool_executor_stream_emits_started_and_result() -> None:
    tool = FakeTool(name="echo")
    executor = SimpleToolExecutor(StaticToolRegistry([tool]))

    events = list(
        executor.run_tool_stream(
            [ToolCall(tool_name="echo", arguments={"text": "streamed"}, call_id="toolu_1")],
            context=ToolExecutionContext(session_id="sess_stream"),
        )
    )

    assert [event.event_type for event in events] == [
        RuntimeEventType.TOOL_STARTED,
        RuntimeEventType.TOOL_RESULT,
    ]
    assert events[0].payload["tool_use_id"] == "toolu_1"
    assert events[1].payload["tool_use_id"] == "toolu_1"


def test_tool_executor_stream_emits_progress_and_result() -> None:
    tool = StreamingTool()
    executor = SimpleToolExecutor(StaticToolRegistry([tool]))

    events = list(
        executor.run_tool_stream(
            [ToolCall(tool_name="streaming", arguments={"text": "done"}, call_id="toolu_2")],
            context=ToolExecutionContext(session_id="sess_stream"),
        )
    )

    assert [event.event_type for event in events] == [
        RuntimeEventType.TOOL_STARTED,
        RuntimeEventType.TOOL_PROGRESS,
        RuntimeEventType.TOOL_RESULT,
    ]
    assert events[1].payload["message"] == "working"


def test_tool_executor_stream_emits_failed_and_cancelled() -> None:
    executor = SimpleToolExecutor(StaticToolRegistry([FailingTool(), CancelledTool()]))

    failed_events = list(
        executor.run_tool_stream(
            [ToolCall(tool_name="failing", arguments={}, call_id="toolu_fail")],
            context=ToolExecutionContext(session_id="sess_stream"),
        )
    )
    cancelled_events = list(
        executor.run_tool_stream(
            [ToolCall(tool_name="cancelled", arguments={}, call_id="toolu_cancel")],
            context=ToolExecutionContext(session_id="sess_stream"),
        )
    )

    assert failed_events[-1].event_type is RuntimeEventType.TOOL_FAILED
    assert cancelled_events[-1].event_type is RuntimeEventType.TOOL_CANCELLED


def test_tool_executor_serializes_non_concurrency_safe_calls() -> None:
    tool = TrackingTool(name="tracking", concurrency_safe=False)
    executor = SimpleToolExecutor(StaticToolRegistry([tool]))

    list(
        executor.run_tool_stream(
            [
                ToolCall(tool_name="tracking", arguments={"label": "one"}, call_id="toolu_3"),
                ToolCall(tool_name="tracking", arguments={"label": "two"}, call_id="toolu_4"),
            ],
            context=ToolExecutionContext(session_id="sess_stream"),
        )
    )

    assert tool.trace == ["start:one", "end:one", "start:two", "end:two"]


def test_tool_executor_uses_policy_engine_override() -> None:
    tool = FakeTool(name="echo", permission=PermissionDecision.ALLOW)
    deny_executor = SimpleToolExecutor(
        StaticToolRegistry([tool]),
        policy_engine=StaticPolicyEngine(
            ToolPolicyOutcome(
                decision=PermissionDecision.DENY,
                reason="Denied by external policy",
            )
        ),
    )
    ask_executor = SimpleToolExecutor(
        StaticToolRegistry([tool]),
        policy_engine=StaticPolicyEngine(
            ToolPolicyOutcome(
                decision=PermissionDecision.ASK,
                reason="Approval required by external policy",
            )
        ),
    )

    try:
        list(
            deny_executor.run_tool_stream(
                [ToolCall(tool_name="echo", arguments={}, call_id="toolu_5")],
                context=ToolExecutionContext(session_id="sess_stream"),
            )
        )
    except Exception as exc:
        assert "Denied by external policy" in str(exc)
    else:
        raise AssertionError("Expected ToolPermissionDeniedError")

    try:
        list(
            ask_executor.run_tool_stream(
                [ToolCall(tool_name="echo", arguments={}, call_id="toolu_6")],
                context=ToolExecutionContext(session_id="sess_stream"),
            )
        )
    except Exception as exc:
        assert "Approval required by external policy" in str(exc)
    else:
        raise AssertionError("Expected RequiresActionError")


def test_rule_based_tool_policy_engine_matches_and_falls_back() -> None:
    tool = FakeTool(name="echo", permission=PermissionDecision.ASK)
    engine = RuleBasedToolPolicyEngine(
        rules=[
            ToolPolicyRule(
                tool_name="echo",
                session_id_prefix="sess_allow",
                decision=PermissionDecision.ALLOW,
                reason="Trusted session",
            )
        ]
    )
    executor = SimpleToolExecutor(StaticToolRegistry([tool]), policy_engine=engine)

    allow_events = list(
        executor.run_tool_stream(
            [ToolCall(tool_name="echo", arguments={"text": "ok"}, call_id="toolu_7")],
            context=ToolExecutionContext(session_id="sess_allow_1"),
        )
    )

    assert allow_events[-1].event_type is RuntimeEventType.TOOL_RESULT

    try:
        list(
            executor.run_tool_stream(
                [
                    ToolCall(
                        tool_name="echo",
                        arguments={"text": "needs approval"},
                        call_id="toolu_8",
                    )
                ],
                context=ToolExecutionContext(session_id="sess_other"),
            )
        )
    except Exception as exc:
        assert "Permission required" in str(exc)
    else:
        raise AssertionError("Expected RequiresActionError via fallback tool policy")
