"""Runtime state projection helpers."""

from __future__ import annotations

from openagent.object_model import RuntimeEvent, RuntimeEventType, TerminalState, TerminalStatus


def terminal_state_from_event(event: RuntimeEvent) -> TerminalState:
    if event.event_type in {RuntimeEventType.TURN_COMPLETED, RuntimeEventType.TURN_FAILED}:
        return TerminalState.from_dict(event.payload)
    if event.event_type is RuntimeEventType.REQUIRES_ACTION:
        summary = str(event.payload.get("description", "requires action"))
        return TerminalState(
            status=TerminalStatus.BLOCKED,
            reason="requires_action",
            summary=summary,
        )
    raise ValueError("Event stream did not terminate with a terminal event")
