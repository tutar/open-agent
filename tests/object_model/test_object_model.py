from openagent.object_model import (
    RuntimeEvent,
    RuntimeEventType,
    SchemaEnvelope,
    TerminalState,
    TerminalStatus,
)


def test_runtime_event_serializes_enum_values() -> None:
    event = RuntimeEvent(
        event_type=RuntimeEventType.TOOL_RESULT,
        event_id="evt_2",
        timestamp="2026-04-14T00:00:01Z",
        session_id="sess_2",
        payload={"tool_name": "echo", "ok": True},
    )

    assert event.to_dict()["event_type"] == "tool_result"


def test_runtime_event_from_dict_restores_enum() -> None:
    event = RuntimeEvent.from_dict(
        {
            "event_type": "assistant_message",
            "event_id": "evt_3",
            "timestamp": "2026-04-14T00:00:02Z",
            "session_id": "sess_3",
            "payload": {"message": "hello"},
        }
    )

    assert event.event_type is RuntimeEventType.ASSISTANT_MESSAGE


def test_terminal_state_serializes_enum_values() -> None:
    terminal = TerminalState(status=TerminalStatus.BLOCKED, reason="approval_required")

    assert terminal.to_dict()["status"] == "blocked"


def test_schema_envelope_to_json_matches_to_dict() -> None:
    envelope = SchemaEnvelope(
        schema_name="RuntimeEvent",
        schema_version="0.1",
        payload={"event_type": "assistant_message"},
    )

    assert envelope.to_json() == envelope.to_dict()
