import json
from dataclasses import dataclass
from pathlib import Path

from openagent.harness.runtime import ModelTurnRequest, ModelTurnResponse
from openagent.local import create_file_runtime
from openagent.object_model import RuntimeEventType, ToolResult
from openagent.session import (
    FileSessionStore,
    FileShortTermMemoryStore,
    InMemoryShortTermMemoryStore,
    SessionMessage,
    SessionRecord,
    WakeRequest,
)
from openagent.tools import ToolCall


@dataclass(slots=True)
class ScriptedModel:
    responses: list[ModelTurnResponse]

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        del request
        return self.responses.pop(0)


@dataclass(slots=True)
class ToolRoundTripModel:
    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        if request.messages[-1]["role"] == "user":
            return ModelTurnResponse(
                tool_calls=[ToolCall(tool_name="echo", arguments={"text": "hello"})]
            )
        return ModelTurnResponse(assistant_message="done")


@dataclass(slots=True)
class EchoTool:
    name: str = "echo"
    input_schema: dict[str, object] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.input_schema is None:
            self.input_schema = {"type": "object"}

    def description(self) -> str:
        return self.name

    def call(self, arguments: dict[str, object]) -> ToolResult:
        return ToolResult(tool_name=self.name, success=True, content=[str(arguments)])

    def check_permissions(self, arguments: dict[str, object]) -> str:
        del arguments
        return "allow"

    def is_concurrency_safe(self) -> bool:
        return True


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


def test_resume_snapshot_includes_short_term_memory(tmp_path: Path) -> None:
    sessions = FileSessionStore(tmp_path / "sessions")
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


def test_file_runtime_checkpoint_and_readback(tmp_path: Path) -> None:
    runtime = create_file_runtime(
        model=ScriptedModel([ModelTurnResponse(assistant_message="ok")]),
        session_root=str(tmp_path / "agent_default" / "sessions"),
    )
    events, _ = runtime.run_turn("hello", "sess_mem")

    store = runtime.sessions
    checkpoint = store.get_checkpoint("sess_mem")
    replayed = store.read_events("sess_mem")
    replayed_from_cursor = store.read_events("sess_mem", cursor=checkpoint.cursor)

    assert isinstance(store, FileSessionStore)
    assert checkpoint.event_offset == len(events)
    assert [event.event_type for event in replayed] == [event.event_type for event in events]
    assert replayed_from_cursor == []


def test_file_session_store_appends_event_log(tmp_path: Path) -> None:
    runtime = create_file_runtime(
        model=ScriptedModel(
            [
                ModelTurnResponse(assistant_message="saved"),
                ModelTurnResponse(assistant_message="saved-again"),
            ]
        ),
        session_root=str(tmp_path / "sessions"),
    )
    store = runtime.sessions

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


def test_file_session_store_writes_transcript_separately(tmp_path: Path) -> None:
    runtime = create_file_runtime(
        model=ScriptedModel([ModelTurnResponse(assistant_message="saved")]),
        session_root=str(tmp_path / "sessions"),
    )

    runtime.run_turn("hello", "sess_transcript")

    root = tmp_path / "sessions"
    session_root = root / "sess_transcript"
    state_payload = json.loads((session_root / "state.json").read_text(encoding="utf-8"))
    transcript_ref = (session_root / "transcript.ref").read_text(encoding="utf-8").strip()
    transcript_lines = Path(transcript_ref).read_text(
        encoding="utf-8"
    ).splitlines()
    event_lines = (session_root / "events.jsonl").read_text(encoding="utf-8").splitlines()

    assert "messages" not in state_payload
    assert state_payload["transcript_message_count"] == 2
    assert state_payload["event_count"] == 3
    assert len(transcript_lines) == 2
    assert len(event_lines) == 3
    first_entry = json.loads(transcript_lines[0])
    assert first_entry["session_id"] == "sess_transcript"
    assert first_entry["role"] == "user"
    assert first_entry["content"] == "hello"
    assert first_entry["turn_id"] is not None

def test_file_runtime_assigns_session_workspace_under_session_root(tmp_path: Path) -> None:
    runtime = create_file_runtime(
        model=ScriptedModel([ModelTurnResponse(assistant_message="ok")]),
        session_root=str(tmp_path / "agent_default" / "sessions"),
    )

    runtime.run_turn("hello", "sess_workspace")
    session = runtime.sessions.load_session("sess_workspace")

    assert session.metadata["workdir"] == str(
        (tmp_path / "agent_default" / "local-agent" / "workspace").resolve()
    )
    assert Path(str(session.metadata["workdir"])).is_dir()


def test_tool_events_persist_turn_task_id(tmp_path: Path) -> None:
    runtime = create_file_runtime(
        model=ToolRoundTripModel(),
        session_root=str(tmp_path / "sessions"),
        tools=[EchoTool()],
    )

    runtime.run_turn("hello", "sess_tools")

    event_lines = (tmp_path / "sessions" / "sess_tools" / "events.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    tool_events = [json.loads(line) for line in event_lines if "tool_" in line]

    assert tool_events
    assert all(event["task_id"] for event in tool_events)


def test_file_session_store_allows_metadata_only_save_when_transcript_file_is_missing(
    tmp_path: Path,
) -> None:
    sessions = FileSessionStore(tmp_path / "sessions")
    session = SessionRecord(
        session_id="sess_missing_transcript",
        messages=[SessionMessage(role="user", content="hello")],
        metadata={"workdir": str((tmp_path / "workspace").resolve())},
    )
    sessions.save_session("sess_missing_transcript", session)

    session_root = tmp_path / "sessions" / "sess_missing_transcript"
    transcript_ref = (session_root / "transcript.ref").read_text(encoding="utf-8").strip()
    Path(transcript_ref).unlink()

    restored = sessions.load_session("sess_missing_transcript")
    restored.metadata["restored_via"] = "gateway"
    sessions.save_session_state_only("sess_missing_transcript", restored)

    state_payload = json.loads((session_root / "state.json").read_text(encoding="utf-8"))
    assert state_payload["transcript_message_count"] == 1
    assert state_payload["metadata"]["restored_via"] == "gateway"


def test_file_session_store_rejects_append_when_transcript_backing_is_missing(
    tmp_path: Path,
) -> None:
    sessions = FileSessionStore(tmp_path / "sessions")
    session = SessionRecord(
        session_id="sess_heal_transcript",
        messages=[SessionMessage(role="user", content="hello")],
    )
    sessions.save_session("sess_heal_transcript", session)

    session_root = tmp_path / "sessions" / "sess_heal_transcript"
    transcript_ref = (session_root / "transcript.ref").read_text(encoding="utf-8").strip()
    Path(transcript_ref).unlink()

    restored = sessions.load_session("sess_heal_transcript")
    restored.messages.append(SessionMessage(role="user", content="next"))
    try:
        sessions.save_session("sess_heal_transcript", restored)
    except ValueError as exc:
        assert "Append-only transcript backing is missing or truncated" in str(exc)
    else:
        raise AssertionError("Expected save_session to reject appending with missing transcript")

    state_payload = json.loads((session_root / "state.json").read_text(encoding="utf-8"))
    assert state_payload["transcript_message_count"] == 1


def test_file_session_store_allows_state_only_save_when_event_log_is_missing(
    tmp_path: Path,
) -> None:
    runtime = create_file_runtime(
        model=ScriptedModel([ModelTurnResponse(assistant_message="saved")]),
        session_root=str(tmp_path / "sessions"),
    )
    runtime.run_turn("hello", "sess_missing_events")

    sessions = runtime.sessions
    session_root = tmp_path / "sessions" / "sess_missing_events"
    (session_root / "events.jsonl").unlink()

    restored = sessions.load_session("sess_missing_events")
    restored.metadata["restored_via"] = "gateway"
    sessions.save_session_state_only("sess_missing_events", restored)

    state_payload = json.loads((session_root / "state.json").read_text(encoding="utf-8"))
    assert state_payload["event_count"] == 3
    assert state_payload["metadata"]["restored_via"] == "gateway"


def test_file_session_store_rejects_append_when_event_log_backing_is_missing(
    tmp_path: Path,
) -> None:
    runtime = create_file_runtime(
        model=ScriptedModel([ModelTurnResponse(assistant_message="saved")]),
        session_root=str(tmp_path / "sessions"),
    )
    runtime.run_turn("hello", "sess_missing_events_append")

    sessions = runtime.sessions
    session_root = tmp_path / "sessions" / "sess_missing_events_append"
    (session_root / "events.jsonl").unlink()

    restored = sessions.load_session("sess_missing_events_append")
    restored.events.append(
        runtime._new_event(
            session_id="sess_missing_events_append",
            event_type=RuntimeEventType.TURN_STARTED,
            payload={"input": "again"},
        )
    )
    try:
        sessions.save_session("sess_missing_events_append", restored)
    except ValueError as exc:
        assert "Append-only event log backing is missing or truncated" in str(exc)
    else:
        raise AssertionError("Expected save_session to reject appending with missing event log")

    state_payload = json.loads((session_root / "state.json").read_text(encoding="utf-8"))
    assert state_payload["event_count"] == 3
