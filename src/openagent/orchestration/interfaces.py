"""Orchestration interface definitions."""

from __future__ import annotations

from typing import Any, Protocol

from openagent.object_model import TaskRecord


class TaskManager(Protocol):
    def create_task(self, description: str, metadata: dict[str, Any] | None = None) -> TaskRecord:
        """Create a task record."""

    def update_task(
        self,
        task_id: str,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Update a task record."""

    def get_task(self, task_id: str) -> TaskRecord:
        """Load a task record by identifier."""
