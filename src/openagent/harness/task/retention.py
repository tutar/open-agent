"""Retention helpers for local task observers and eviction."""

from __future__ import annotations

from dataclasses import dataclass

from openagent.harness.task.interfaces import TaskManager
from openagent.harness.task.models import TaskRetentionPolicy


@dataclass(slots=True)
class TaskRetentionRuntime:
    """Thin helper around task observer attachment and eviction."""

    manager: TaskManager
    policy: TaskRetentionPolicy

    def attach_chat(self, task_id: str, chat_binding_id: str) -> None:
        self.manager.attach_observer(task_id, chat_binding_id)

    def detach_chat(self, task_id: str, chat_binding_id: str) -> None:
        self.manager.detach_observer(task_id, chat_binding_id)

    def mark_terminal_notified(self, task_id: str) -> None:
        self.manager.mark_notified(task_id)

    def evict(self, *, now_timestamp: str | None = None) -> list[str]:
        return self.manager.evict_expired(now_timestamp=now_timestamp)
