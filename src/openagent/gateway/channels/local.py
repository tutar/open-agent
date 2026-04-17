"""Built-in local channel adapter for the terminal frontend."""

from __future__ import annotations

from dataclasses import dataclass

from openagent.object_model import RuntimeEventType


def _default_local_event_types() -> list[str]:
    """Return the default UI-facing event projection set for the terminal channel."""

    return [
        RuntimeEventType.TURN_STARTED.value,
        RuntimeEventType.ASSISTANT_DELTA.value,
        RuntimeEventType.ASSISTANT_MESSAGE.value,
        RuntimeEventType.TOOL_STARTED.value,
        RuntimeEventType.TOOL_PROGRESS.value,
        RuntimeEventType.TOOL_FAILED.value,
        RuntimeEventType.TOOL_CANCELLED.value,
        RuntimeEventType.TOOL_RESULT.value,
        RuntimeEventType.REQUIRES_ACTION.value,
        RuntimeEventType.TASK_NOTIFICATION.value,
        RuntimeEventType.TURN_COMPLETED.value,
        RuntimeEventType.TURN_FAILED.value,
    ]


@dataclass(slots=True)
class TerminalChannelAdapter:
    """Default channel adapter metadata for the terminal TUI."""

    channel_type: str = "terminal"

    def accepted_event_types(self) -> list[str]:
        """Project only UI-relevant runtime events to the terminal frontend."""

        return _default_local_event_types()
