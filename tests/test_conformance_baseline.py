from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openagent.harness import ModelTurnRequest, ModelTurnResponse, SimpleHarness
from openagent.object_model import RuntimeEventType, TerminalStatus, ToolResult
from openagent.session import FileSessionStore, InMemorySessionStore, SessionStatus
from openagent.tools import PermissionDecision, SimpleToolExecutor, StaticToolRegistry, ToolCall


@dataclass(slots=True)
class FakeTool:
    name: str
    permission: PermissionDecision = PermissionDecision.ALLOW
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})

    def description(self) -> str:
        return self.name

    def call(self, arguments: dict[str, object]) -> ToolResult:
        text = arguments.get("text", "ok")
        return ToolResult(tool_name=self.name, success=True, content=[str(text)])

    def check_permissions(self, arguments: dict[str, object]) -> str:
        del arguments
        return self.permission.value

    def is_concurrency_safe(self) -> bool:
        return True


@dataclass(slots=True)
class ScriptedModel:
    responses: list[ModelTurnResponse]

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        del request
        return self.responses.pop(0)


def test_conformance_basic_turn() -> None:
    store = InMemorySessionStore()
    harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="hello")]),
        sessions=store,
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
    )

    events, terminal = harness.run_turn("hi", "case_basic")
    session = store.load_session("case_basic")

    assert terminal.status is TerminalStatus.COMPLETED
    assert [event.event_type for event in events] == [
        RuntimeEventType.TURN_STARTED,
        RuntimeEventType.ASSISTANT_MESSAGE,
        RuntimeEventType.TURN_COMPLETED,
    ]
    assert session.status is SessionStatus.IDLE
    assert [message.role for message in session.messages] == ["user", "assistant"]


def test_conformance_tool_call_roundtrip() -> None:
    tool = FakeTool(name="echo")
    registry = StaticToolRegistry([tool])
    harness = SimpleHarness(
        model=ScriptedModel(
            [
                ModelTurnResponse(tool_calls=[ToolCall(tool_name="echo", arguments={"text": "x"})]),
                ModelTurnResponse(assistant_message="done"),
            ]
        ),
        sessions=InMemorySessionStore(),
        tools=registry,
        executor=SimpleToolExecutor(registry),
    )

    events, terminal = harness.run_turn("use tool", "case_tool")

    assert terminal.status is TerminalStatus.COMPLETED
    assert RuntimeEventType.TURN_STARTED in [event.event_type for event in events]
    assert RuntimeEventType.TOOL_STARTED in [event.event_type for event in events]
    assert RuntimeEventType.TOOL_RESULT in [event.event_type for event in events]
    assert events[-1].event_type is RuntimeEventType.TURN_COMPLETED


def test_conformance_requires_action_approval() -> None:
    tool = FakeTool(name="admin", permission=PermissionDecision.ASK)
    registry = StaticToolRegistry([tool])
    store = InMemorySessionStore()
    harness = SimpleHarness(
        model=ScriptedModel(
            [
                ModelTurnResponse(
                    tool_calls=[ToolCall(tool_name="admin", arguments={"text": "rotate"})]
                ),
                ModelTurnResponse(assistant_message="approved and finished"),
            ]
        ),
        sessions=store,
        tools=registry,
        executor=SimpleToolExecutor(registry),
    )

    first_events, first_terminal = harness.run_turn("please rotate", "case_approval")
    session = store.load_session("case_approval")

    assert first_terminal.status is TerminalStatus.BLOCKED
    assert session.status is SessionStatus.REQUIRES_ACTION
    assert first_events[-1].event_type is RuntimeEventType.REQUIRES_ACTION
    assert first_events[-1].payload["tool_name"] == "admin"

    second_events, second_terminal = harness.continue_turn("case_approval", approved=True)
    resumed = store.load_session("case_approval")

    assert second_terminal.status is TerminalStatus.COMPLETED
    assert [event.event_type for event in second_events[:2]] == [
        RuntimeEventType.TOOL_STARTED,
        RuntimeEventType.TOOL_RESULT,
    ]
    assert second_events[-1].event_type is RuntimeEventType.TURN_COMPLETED
    assert resumed.status is SessionStatus.IDLE


def test_conformance_session_resume(tmp_path: Path) -> None:
    session_root = tmp_path / "sessions"
    store = FileSessionStore(session_root)
    harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="first reply")]),
        sessions=store,
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
    )

    first_events, first_terminal = harness.run_turn("first", "case_resume")

    assert first_terminal.status is TerminalStatus.COMPLETED
    assert first_events[-1].event_type is RuntimeEventType.TURN_COMPLETED

    restored_store = FileSessionStore(session_root)
    resumed_harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="second reply")]),
        sessions=restored_store,
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
    )
    second_events, second_terminal = resumed_harness.run_turn("second", "case_resume")
    restored_session = restored_store.load_session("case_resume")

    assert second_terminal.status is TerminalStatus.COMPLETED
    assert second_events[-1].event_type is RuntimeEventType.TURN_COMPLETED
    assert [message.content for message in restored_session.messages] == [
        "first",
        "first reply",
        "second",
        "second reply",
    ]
