from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from openagent.harness import ModelTurnRequest, ModelTurnResponse
from openagent.local import create_in_memory_runtime
from openagent.object_model import RuntimeEventType, ToolResult
from openagent.session import (
    FileSessionStore,
    FileShortTermMemoryStore,
    InMemorySessionStore,
    InMemoryShortTermMemoryStore,
    SessionMessage,
    WakeRequest,
)
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


def test_in_memory_short_term_memory_store_stabilizes_updates() -> None:
    store = InMemoryShortTermMemoryStore()
    transcript = [
        SessionMessage(role="user", content="Finish the release checklist"),
        SessionMessage(role="assistant", content="Tracking the checklist now"),
    ]

    result = store.update("sess_short", transcript, current_memory=None)
    stable = store.wait_until_stable("sess_short", 1000)

    assert result.scheduled is True
    assert result.stable is False
    assert stable is not None
    assert "checklist" in stable.summary.lower()
    assert stable.coverage_boundary == 2


def test_file_short_term_memory_store_persists_snapshots(tmp_path: Path) -> None:
    root = tmp_path / "short_term"
    store = FileShortTermMemoryStore(root)
    transcript = [SessionMessage(role="user", content="Remember the deployment status")]

    store.update("sess_short_file", transcript, current_memory=None)
    stable = store.wait_until_stable("sess_short_file", 1000)
    restored = FileShortTermMemoryStore(root)

    assert stable is not None
    loaded = restored.load("sess_short_file")
    assert loaded is not None
    assert "deployment status" in loaded.summary.lower()


def test_resume_snapshot_includes_short_term_memory() -> None:
    sessions = InMemorySessionStore()
    session = sessions.load_session("sess_resume_short")
    session.messages.append(SessionMessage(role="user", content="Continue the migration"))
    session.short_term_memory = {
        "summary": "Continue the migration plan.",
        "coverage_boundary": 1,
    }
    sessions.save_session("sess_resume_short", session)

    snapshot = sessions.get_resume_snapshot(WakeRequest(session_id="sess_resume_short"))

    assert snapshot.short_term_memory is not None
    assert snapshot.short_term_memory["summary"] == "Continue the migration plan."


def test_in_memory_session_store_checkpoint_and_readback() -> None:
    runtime = create_in_memory_runtime(
        model=ScriptedModel([ModelTurnResponse(assistant_message="ok")])
    )
    events, _ = runtime.run_turn("hello", "sess_mem")

    store = runtime.sessions
    checkpoint = store.get_checkpoint("sess_mem")
    replayed = store.read_events("sess_mem")
    replayed_from_cursor = store.read_events("sess_mem", cursor=checkpoint.cursor)

    assert isinstance(store, InMemorySessionStore)
    assert checkpoint.event_offset == len(events)
    assert [event.event_type for event in replayed] == [event.event_type for event in events]
    assert replayed_from_cursor == []


def test_file_session_store_appends_event_log(tmp_path: Path) -> None:
    store = FileSessionStore(tmp_path / "sessions")
    runtime = create_in_memory_runtime(
        model=ScriptedModel(
            [
                ModelTurnResponse(assistant_message="saved"),
                ModelTurnResponse(assistant_message="saved-again"),
            ]
        )
    )
    runtime.sessions = store

    first_events, _ = runtime.run_turn("first", "sess_file")
    first_checkpoint = store.get_checkpoint("sess_file")
    second_events, _ = runtime.run_turn("second", "sess_file")
    all_events = store.read_events("sess_file")
    resumed = store.get_resume_snapshot(WakeRequest(session_id="sess_file"))
    store.mark_restored("sess_file", first_checkpoint.cursor)
    restored_record = store.load_session("sess_file")

    assert first_checkpoint.event_offset == len(first_events)
    assert first_checkpoint.cursor is not None
    assert len(all_events) == len(first_events) + len(second_events)
    assert all_events[-1].event_type is RuntimeEventType.TURN_COMPLETED
    assert resumed.working_state["event_count"] == len(all_events)
    assert restored_record.restore_marker == first_checkpoint.last_event_id
