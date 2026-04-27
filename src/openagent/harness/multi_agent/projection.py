"""Viewed transcript projection for delegated workers."""

from __future__ import annotations

from openagent.harness.multi_agent.models import ViewedTranscript, ViewedTranscriptEntry
from openagent.harness.task.interfaces import TaskManager
from openagent.harness.task.retention import TaskRetentionRuntime


class ViewedTranscriptProjector:
    """Project task events and output slices into a viewed transcript."""

    def __init__(
        self,
        task_manager: TaskManager,
        retention: TaskRetentionRuntime,
    ) -> None:
        self._task_manager = task_manager
        self._retention = retention

    def attach_view(self, task_id: str, binding_id: str) -> None:
        self._retention.attach_chat(task_id, binding_id)

    def release_view(self, task_id: str, binding_id: str) -> None:
        self._retention.detach_chat(task_id, binding_id)

    def project(self, task_id: str) -> ViewedTranscript:
        event_slice = self._task_manager.read_events(task_id)
        output_slice = self._task_manager.read_output(task_id)
        entries: list[ViewedTranscriptEntry] = []
        for event in event_slice.events:
            entries.append(
                ViewedTranscriptEntry(
                    source="task_event",
                    kind=str(event.get("type", "event")),
                    payload=event,
                )
            )
        for item in output_slice.items:
            entries.append(
                ViewedTranscriptEntry(
                    source="task_output",
                    kind="output",
                    payload=item,
                )
            )
        return ViewedTranscript(
            task_id=task_id,
            entries=entries,
            retained=self._task_manager.registry.observer_count(task_id) > 0,  # type: ignore[attr-defined]
        )
