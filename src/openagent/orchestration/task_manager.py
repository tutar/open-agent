"""In-memory task manager baseline."""

from __future__ import annotations

from datetime import UTC, datetime

from openagent.object_model import JsonObject, TaskRecord, TerminalStatus


class InMemoryTaskManager:
    """Task manager suitable for local orchestration tests."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._counter = 0

    def create_task(self, description: str, metadata: JsonObject | None = None) -> TaskRecord:
        self._counter += 1
        task_id = f"task_{self._counter}"
        record = TaskRecord(
            task_id=task_id,
            type="generic",
            status=TerminalStatus.COMPLETED,
            description=description,
            start_time=datetime.now(UTC).isoformat(),
            metadata=metadata,
        )
        self._tasks[task_id] = record
        return record

    def update_task(
        self,
        task_id: str,
        status: str,
        metadata: JsonObject | None = None,
    ) -> None:
        record = self._tasks[task_id]
        record.status = status
        if metadata is not None:
            record.metadata = metadata

    def get_task(self, task_id: str) -> TaskRecord:
        return self._tasks[task_id]
