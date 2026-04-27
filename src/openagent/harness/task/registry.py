"""Task registries for local task state and execution dispatch."""

from __future__ import annotations

import builtins
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from openagent.harness.task.models import (
    TaskEventSlice,
    TaskOutputSlice,
    TaskRetentionPolicy,
    TaskSelector,
)
from openagent.harness.task.storage import FileTaskStorage, InMemoryTaskStorage
from openagent.object_model import (
    JsonObject,
    JsonValue,
    TaskEvent,
    TaskRecord,
    TerminalState,
    TerminalStatus,
)

_TERMINAL_STATUSES = {
    TerminalStatus.COMPLETED.value,
    TerminalStatus.FAILED.value,
    TerminalStatus.CANCELLED.value,
    TerminalStatus.KILLED.value,
    TerminalStatus.STOPPED.value,
}


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _coerce_terminal_state(
    terminal_state: TerminalState | JsonObject | None,
) -> JsonObject | None:
    if terminal_state is None:
        return None
    if isinstance(terminal_state, TerminalState):
        return terminal_state.to_dict()
    return dict(terminal_state)


@dataclass(slots=True)
class TaskImplementation:
    await_task: Callable[[str, float | None], TaskRecord] | None = None
    kill_task: Callable[[str], TaskRecord] | None = None
    read_output: Callable[[str, int], TaskOutputSlice] | None = None
    read_events: Callable[[str, int], TaskEventSlice] | None = None
    send_input: Callable[[str, JsonObject], Any] | None = None


class TaskImplementationRegistry:
    """Task-type specific runtime implementations."""

    def __init__(self) -> None:
        self._task_impls: dict[str, TaskImplementation] = {}
        self._type_impls: dict[str, TaskImplementation] = {}

    def register_type(self, task_type: str, implementation: TaskImplementation) -> None:
        self._type_impls[task_type] = implementation

    def register_task(self, task_id: str, implementation: TaskImplementation) -> None:
        self._task_impls[task_id] = implementation

    def get(self, task_id: str, task_type: str) -> TaskImplementation | None:
        return self._task_impls.get(task_id) or self._type_impls.get(task_type)

    def remove(self, task_id: str) -> None:
        self._task_impls.pop(task_id, None)


class TaskRegistry:
    """Single source of truth for durable task state."""

    def __init__(
        self,
        storage: InMemoryTaskStorage | FileTaskStorage,
        retention_policy: TaskRetentionPolicy | None = None,
    ) -> None:
        self._storage = storage
        self._retention_policy = retention_policy or TaskRetentionPolicy()

    def next_task_id(self) -> str:
        return self._storage.next_task_id()

    def register(self, task: TaskRecord) -> TaskRecord:
        self._write_task(task)
        self._write_events(task.task_id, self._read_events(task.task_id))
        self._write_outputs(task.task_id, self._read_outputs(task.task_id))
        self._write_observers(task.task_id, self._read_observers(task.task_id))
        return task

    def get(self, task_id: str) -> TaskRecord:
        if isinstance(self._storage, InMemoryTaskStorage):
            return self._storage.tasks[task_id]
        return self._storage.read_task(task_id)

    def list(self, selector: TaskSelector | None = None) -> builtins.list[TaskRecord]:
        records = (
            list(self._storage.tasks.values())
            if isinstance(self._storage, InMemoryTaskStorage)
            else self._storage.list_tasks()
        )
        if selector is None:
            return sorted(records, key=lambda record: record.task_id)
        return [
            record
            for record in sorted(records, key=lambda item: item.task_id)
            if (selector.status is None or str(record.status) == selector.status)
            and (selector.type is None or record.type == selector.type)
            and (selector.session_id is None or record.session_id == selector.session_id)
            and (selector.agent_id is None or record.agent_id == selector.agent_id)
        ]

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
        record = self.get(task_id)
        if status is not None:
            try:
                record.status = TerminalStatus(status)
            except ValueError:
                record.status = status
            if status in _TERMINAL_STATUSES and record.end_time is None:
                record.end_time = end_time or _iso_now()
        if metadata is not None:
            merged_metadata = dict(record.metadata or {})
            merged_metadata.update(metadata)
            record.metadata = merged_metadata
        if output_ref is not None:
            record.output_ref = output_ref
        if output_cursor is not None:
            record.output_cursor = output_cursor
        if terminal_state is not None:
            record.terminal_state = _coerce_terminal_state(terminal_state)
        if notified is not None:
            record.notified = notified
        if end_time is not None:
            record.end_time = end_time
        self._write_task(record)
        return record

    def append_event(self, task_id: str, event: TaskEvent) -> TaskEvent:
        events = self._read_events(task_id)
        events.append(event)
        self._write_events(task_id, events)
        return event

    def read_events(self, task_id: str, cursor: int = 0) -> TaskEventSlice:
        events = self._read_events(task_id)
        record = self.get(task_id)
        return TaskEventSlice(
            task_id=task_id,
            cursor=len(events),
            events=[event.to_dict() for event in events[cursor:]],
            done=str(record.status) in _TERMINAL_STATUSES,
        )

    def append_output(self, task_id: str, item: JsonValue) -> int:
        outputs = self._read_outputs(task_id)
        outputs.append(item)
        self._write_outputs(task_id, outputs)
        self.update(task_id, output_cursor=len(outputs))
        return len(outputs)

    def attach_output(self, task_id: str, output_ref: str) -> TaskRecord:
        return self.update(task_id, output_ref=output_ref)

    def read_output(self, task_id: str, cursor: int = 0) -> TaskOutputSlice:
        outputs = self._read_outputs(task_id)
        record = self.get(task_id)
        return TaskOutputSlice(
            task_id=task_id,
            output_ref=record.output_ref,
            cursor=len(outputs),
            items=outputs[cursor:],
            done=str(record.status) in _TERMINAL_STATUSES,
        )

    def attach_observer(self, task_id: str, binding_id: str) -> None:
        bindings = self._read_observers(task_id)
        bindings.add(binding_id)
        self._write_observers(task_id, bindings)

    def detach_observer(self, task_id: str, binding_id: str) -> None:
        bindings = self._read_observers(task_id)
        bindings.discard(binding_id)
        self._write_observers(task_id, bindings)

    def observer_count(self, task_id: str) -> int:
        return len(self._read_observers(task_id))

    def mark_notified(self, task_id: str) -> TaskRecord:
        return self.update(task_id, notified=True)

    def evict_expired(self, *, now_timestamp: str | None = None) -> builtins.list[str]:
        if not self._retention_policy.evict_terminal_without_observers:
            return []
        now = (
            datetime.fromisoformat(now_timestamp)
            if isinstance(now_timestamp, str)
            else datetime.now(UTC)
        )
        removed: list[str] = []
        for record in self.list():
            if str(record.status) not in _TERMINAL_STATUSES:
                continue
            if self.observer_count(record.task_id) > 0:
                continue
            if not record.notified:
                continue
            if not isinstance(record.end_time, str):
                continue
            ended_at = datetime.fromisoformat(record.end_time)
            if now < ended_at + timedelta(seconds=self._retention_policy.grace_period_seconds):
                continue
            self._remove(record.task_id)
            removed.append(record.task_id)
        return removed

    def _remove(self, task_id: str) -> None:
        if isinstance(self._storage, InMemoryTaskStorage):
            self._storage.tasks.pop(task_id, None)
            self._storage.events.pop(task_id, None)
            self._storage.observers.pop(task_id, None)
            if self._retention_policy.evict_output_with_state:
                self._storage.outputs.pop(task_id, None)
            return
        self._storage.remove_task_state(
            task_id,
            remove_output=self._retention_policy.evict_output_with_state,
        )

    def _read_events(self, task_id: str) -> builtins.list[TaskEvent]:
        if isinstance(self._storage, InMemoryTaskStorage):
            return cast(list[TaskEvent], list(self._storage.events.get(task_id, [])))
        return self._storage.read_events(task_id)

    def _write_events(self, task_id: str, events: builtins.list[TaskEvent]) -> None:
        if isinstance(self._storage, InMemoryTaskStorage):
            self._storage.events[task_id] = list(events)
            return
        self._storage.write_events(task_id, events)

    def _read_outputs(self, task_id: str) -> builtins.list[JsonValue]:
        if isinstance(self._storage, InMemoryTaskStorage):
            return cast(list[JsonValue], list(self._storage.outputs.get(task_id, [])))
        return self._storage.read_outputs(task_id)

    def _write_outputs(self, task_id: str, items: builtins.list[JsonValue]) -> None:
        if isinstance(self._storage, InMemoryTaskStorage):
            self._storage.outputs[task_id] = list(items)
            return
        self._storage.write_outputs(task_id, list(items))

    def _read_observers(self, task_id: str) -> set[str]:
        if isinstance(self._storage, InMemoryTaskStorage):
            return set(self._storage.observers.get(task_id, set()))
        return self._storage.read_observers(task_id)

    def _write_observers(self, task_id: str, bindings: set[str]) -> None:
        if isinstance(self._storage, InMemoryTaskStorage):
            self._storage.observers[task_id] = set(bindings)
            return
        self._storage.write_observers(task_id, bindings)

    def _write_task(self, record: TaskRecord) -> None:
        if isinstance(self._storage, InMemoryTaskStorage):
            self._storage.tasks[record.task_id] = record
            return
        self._storage.write_task(record)
