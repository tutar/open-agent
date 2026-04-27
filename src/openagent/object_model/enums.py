"""Stable enums for canonical object model semantics."""

from __future__ import annotations

from enum import StrEnum


class RuntimeEventType(StrEnum):
    TURN_STARTED = "turn_started"
    ASSISTANT_DELTA = "assistant_delta"
    ASSISTANT_MESSAGE = "assistant_message"
    TASK_CREATED = "task_created"
    TASK_STARTED = "task_started"
    TASK_PROGRESS = "task_progress"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_KILLED = "task_killed"
    TOOL_STARTED = "tool_started"
    TOOL_PROGRESS = "tool_progress"
    TOOL_FAILED = "tool_failed"
    TOOL_CANCELLED = "tool_cancelled"
    TOOL_RESULT = "tool_result"
    REQUIRES_ACTION = "requires_action"
    TASK_NOTIFICATION = "task_notification"
    TURN_COMPLETED = "turn_completed"
    TURN_FAILED = "turn_failed"


class TerminalStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    KILLED = "killed"
    STOPPED = "stopped"
    BLOCKED = "blocked"
    FAILED = "failed"
    BUDGET_EXHAUSTED = "budget_exhausted"
