from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openagent.harness.runtime import ModelTurnRequest, ModelTurnResponse, SimpleHarness
from openagent.object_model import RuntimeEventType, TerminalStatus, ToolResult
from openagent.session import (
    FileSessionStore,
    FileShortTermMemoryStore,
    SessionMessage,
    SessionRecord,
    SessionStatus,
)
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


def test_conformance_session_transcript_vs_short_term_memory_boundary(tmp_path: Path) -> None:
    short_term_store = FileShortTermMemoryStore(tmp_path / "short-term")
    store = FileSessionStore(tmp_path / "sessions")
    harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="done")]),
        sessions=store,
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
        short_term_memory_store=short_term_store,
    )

    harness.run_turn("first user turn", "case_short_term_boundary")
    session = store.load_session("case_short_term_boundary")
    harness.schedule_memory_maintenance(session)
    harness.stabilize_short_term_memory(session)

    stable_memory = short_term_store.load("case_short_term_boundary")
    request = harness.build_model_input(
        SessionRecord(
            session_id="case_short_term_boundary",
            messages=[*session.messages, SessionMessage(role="user", content="continue")],
            short_term_memory=stable_memory.to_dict() if stable_memory is not None else None,
        ),
        [],
    )

    assert stable_memory is not None
    assert stable_memory.coverage_boundary == len(session.messages)
    assert request.messages[0]["content"] == "first user turn"
    assert request.short_term_memory is not None
    assert request.short_term_memory["summary"]


def test_conformance_session_working_state_vs_lifecycle_state(tmp_path: Path) -> None:
    tool = FakeTool(name="admin", permission=PermissionDecision.ASK)
    registry = StaticToolRegistry([tool])
    session_root = tmp_path / "sessions"
    first_store = FileSessionStore(session_root)
    first_harness = SimpleHarness(
        model=ScriptedModel(
            [
                ModelTurnResponse(
                    tool_calls=[ToolCall(tool_name="admin", arguments={"text": "rotate"})]
                ),
                ModelTurnResponse(assistant_message="approved"),
            ]
        ),
        sessions=first_store,
        tools=registry,
        executor=SimpleToolExecutor(registry),
    )

    first_events, first_terminal = first_harness.run_turn("rotate", "case_working_state")
    persisted = first_store.load_session("case_working_state")

    assert first_terminal.status is TerminalStatus.BLOCKED
    assert first_events[-1].event_type is RuntimeEventType.REQUIRES_ACTION
    assert persisted.status is SessionStatus.REQUIRES_ACTION
    assert persisted.pending_tool_calls

    restored_store = FileSessionStore(session_root)
    restored_harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="approved")]),
        sessions=restored_store,
        tools=registry,
        executor=SimpleToolExecutor(registry),
    )
    resumed_events, resumed_terminal = restored_harness.continue_turn(
        "case_working_state",
        approved=True,
    )
    resumed = restored_store.load_session("case_working_state")

    assert resumed_terminal.status is TerminalStatus.COMPLETED
    assert resumed_events[0].event_type is RuntimeEventType.TOOL_STARTED
    assert resumed_events[0].payload["tool_name"] == "admin"
    assert resumed.status is SessionStatus.IDLE
