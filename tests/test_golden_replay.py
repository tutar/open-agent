import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from openagent.harness import ModelTurnRequest, ModelTurnResponse, SimpleHarness
from openagent.object_model import RuntimeEvent, RuntimeEventType, TerminalStatus, ToolResult
from openagent.session import FileSessionStore, InMemorySessionStore, SessionStatus
from openagent.tools import PermissionDecision, SimpleToolExecutor, StaticToolRegistry, ToolCall

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "agent-sdk-spec" / "conformance" / "golden"


def _load_golden(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((GOLDEN_DIR / name).read_text(encoding="utf-8")))


def _event_types(events: list[RuntimeEvent]) -> list[str]:
    mapping = {
        RuntimeEventType.ASSISTANT_MESSAGE: "assistant_output",
    }
    return [mapping.get(event.event_type, event.event_type.value) for event in events]


@dataclass(slots=True)
class FakeTool:
    name: str
    permission: PermissionDecision = PermissionDecision.ALLOW
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})

    def description(self) -> str:
        return self.name

    def call(self, arguments: dict[str, Any]) -> ToolResult:
        text = arguments.get("text", "ok")
        return ToolResult(tool_name=self.name, success=True, content=[str(text)])

    def check_permissions(self, arguments: dict[str, Any]) -> str:
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


def test_basic_turn_matches_golden() -> None:
    golden = _load_golden("basic-turn.events.json")
    store = InMemorySessionStore()
    harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="hello")]),
        sessions=store,
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
    )

    events, terminal = harness.run_turn("hi", "golden_basic")

    assert _event_types(events) == [item["type"] for item in golden["events"]]
    assert terminal.status.value == golden["events"][-1]["terminal_state"]
    assert [
        SessionStatus.IDLE.value,
        SessionStatus.RUNNING.value,
        store.load_session("golden_basic").status.value,
    ] == golden["lifecycle"]


def test_tool_roundtrip_matches_golden() -> None:
    golden = _load_golden("tool-call-roundtrip.events.json")
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

    events, terminal = harness.run_turn("use tool", "golden_tool")
    comparable_events = [events[0], events[1], events[2], events[-1]]

    assert _event_types(comparable_events) == [item["type"] for item in golden["events"]]
    assert terminal.status.value == golden["events"][-1]["terminal_state"]
    assert (
        comparable_events[1].payload["tool_use_id"] == comparable_events[2].payload["tool_use_id"]
    )


def test_requires_action_matches_golden() -> None:
    golden = _load_golden("requires-action-approval.events.json")
    tool = FakeTool(name="admin", permission=PermissionDecision.ASK)
    registry = StaticToolRegistry([tool])
    store = InMemorySessionStore()
    harness = SimpleHarness(
        model=ScriptedModel(
            [
                ModelTurnResponse(
                    tool_calls=[ToolCall(tool_name="admin", arguments={"text": "rotate"})]
                ),
                ModelTurnResponse(assistant_message="done after approval"),
            ]
        ),
        sessions=store,
        tools=registry,
        executor=SimpleToolExecutor(registry),
    )

    first_events, _ = harness.run_turn("rotate", "golden_approval")
    first_session = store.load_session("golden_approval")
    assert [item["type"] for item in golden["phase_1"]["events"]] == _event_types(
        [first_events[0], first_events[-1]]
    )
    assert first_session.status.value == golden["phase_1"]["lifecycle"][-1]
    assert first_events[-1].payload["tool_name"] == "admin"
    assert first_events[-1].payload["tool_use_id"] == "toolu_1"

    second_events, second_terminal = harness.continue_turn("golden_approval", approved=True)
    second_session = store.load_session("golden_approval")
    assert _event_types([second_events[0], second_events[1], second_events[-1]]) == [
        item["type"] for item in golden["phase_2"]["events"]
    ]
    assert second_events[0].payload["tool_use_id"] == second_events[1].payload["tool_use_id"]
    assert second_terminal.status.value == golden["phase_2"]["events"][-1]["terminal_state"]
    assert second_session.status.value == golden["phase_2"]["lifecycle"][-1]


def test_session_resume_matches_golden(tmp_path: Path) -> None:
    golden = _load_golden("session-resume.event-log.json")
    session_root = tmp_path / "sessions"
    initial_store = FileSessionStore(session_root)
    first_harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="first reply")]),
        sessions=initial_store,
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
    )

    first_harness.run_turn("first", "golden_resume")
    before_restore = initial_store.load_session("golden_resume")
    before_event_count = len(before_restore.events)

    restored_store = FileSessionStore(session_root)
    second_harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="second reply")]),
        sessions=restored_store,
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
    )
    second_events, terminal = second_harness.run_turn("second", "golden_resume")
    restored = restored_store.load_session("golden_resume")

    assert terminal.status is TerminalStatus.COMPLETED
    assert restored.session_id == "golden_resume"
    assert len(restored.events) > before_event_count
    assert second_events[-1].event_type is RuntimeEventType.TURN_COMPLETED
    assert restored.messages[-2].content == "second"
    assert restored.messages[-1].content == "second reply"
    assert golden["requirements"][0] == "session id remains stable across restore"
