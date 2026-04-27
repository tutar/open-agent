"""Task lifecycle interfaces for the local harness baseline."""

from __future__ import annotations

import builtins
from typing import Protocol

from openagent.harness.task.models import (
    BackgroundTaskHandle,
    TaskEventSlice,
    TaskOutputSlice,
    TaskSelector,
    VerificationRequest,
    VerificationResult,
    VerifierTaskHandle,
)
from openagent.object_model import JsonObject, JsonValue, TaskEvent, TaskRecord, TerminalState


class TaskManager(Protocol):
    def spawn(
        self,
        *,
        task_type: str,
        description: str,
        metadata: JsonObject | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
        parent_task_id: str | None = None,
    ) -> TaskRecord:
        """Create and register a task."""

    def register(self, task: TaskRecord) -> TaskRecord:
        """Persist a task that has already been constructed."""

    def append_event(self, task_id: str, event: TaskEvent) -> TaskEvent:
        """Append a durable task event."""

    def append_output(self, task_id: str, item: JsonValue) -> int:
        """Append task output and return the new cursor."""

    def update(
        self,
        task_id: str,
        *,
        status: str | None = None,
        metadata: JsonObject | None = None,
        output_ref: str | None = None,
        output_cursor: int | str | None = None,
        terminal_state: TerminalState | JsonObject | None = None,
        notified: bool | None = None,
        end_time: str | None = None,
    ) -> TaskRecord:
        """Patch a task record."""

    def attach_output(self, task_id: str, output_ref: str) -> TaskRecord:
        """Associate a stable output handle with a task."""

    def kill(self, task_id: str) -> TaskRecord:
        """Terminate a task."""

    def list(self, selector: TaskSelector | None = None) -> builtins.list[TaskRecord]:
        """List tasks matching an optional selector."""

    def get(self, task_id: str) -> TaskRecord:
        """Load a task by identifier."""

    def read_output(self, task_id: str, cursor: int = 0) -> TaskOutputSlice:
        """Read task output from a cursor."""

    def read_events(self, task_id: str, cursor: int = 0) -> TaskEventSlice:
        """Read task events from a cursor."""

    def await_task(self, task_id: str, timeout: float | None = None) -> TaskRecord:
        """Wait for task completion or return the latest state."""

    def attach_observer(self, task_id: str, binding_id: str) -> None:
        """Mark a channel/session observer as attached."""

    def detach_observer(self, task_id: str, binding_id: str) -> None:
        """Release a channel/session observer."""

    def mark_notified(self, task_id: str) -> TaskRecord:
        """Mark a terminal task as having emitted its stable notification."""

    def evict_expired(self, *, now_timestamp: str | None = None) -> builtins.list[str]:
        """Evict expired terminal tasks and return removed ids."""

    def create_background_task(
        self,
        description: str,
        metadata: JsonObject | None = None,
        detached: bool = True,
        session_id: str | None = None,
        agent_id: str | None = None,
        parent_task_id: str | None = None,
    ) -> BackgroundTaskHandle:
        """Compatibility helper for background tasks."""

    def create_verifier_task(
        self,
        description: str,
        metadata: JsonObject | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
        parent_task_id: str | None = None,
    ) -> VerifierTaskHandle:
        """Compatibility helper for verifier tasks."""

    def run_verifier(
        self,
        request: VerificationRequest,
        *,
        detached: bool = False,
    ) -> VerifierTaskHandle:
        """Spawn a verifier task."""

    def await_verifier(
        self,
        handle: VerifierTaskHandle,
        timeout: float | None = None,
    ) -> VerificationResult:
        """Wait for verifier completion."""
