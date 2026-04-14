"""Stable enums for canonical object model semantics."""

from __future__ import annotations

from enum import StrEnum


class RuntimeEventType(StrEnum):
    TURN_STARTED = "turn_started"
    ASSISTANT_DELTA = "assistant_delta"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_STARTED = "tool_started"
    TOOL_PROGRESS = "tool_progress"
    TOOL_RESULT = "tool_result"
    REQUIRES_ACTION = "requires_action"
    TASK_NOTIFICATION = "task_notification"
    TURN_COMPLETED = "turn_completed"
    TURN_FAILED = "turn_failed"


class TerminalStatus(StrEnum):
    COMPLETED = "completed"
    STOPPED = "stopped"
    BLOCKED = "blocked"
    FAILED = "failed"
    BUDGET_EXHAUSTED = "budget_exhausted"
