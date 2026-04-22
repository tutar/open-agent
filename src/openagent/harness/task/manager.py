"""Task managers with in-memory and file-backed persistence baselines."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from openagent.harness.task.models import BackgroundTaskHandle, LocalTaskKind
from openagent.object_model import JsonObject, TaskRecord, TerminalStatus


class InMemoryTaskManager:
    """Task manager suitable for local harness task tests."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._handles: dict[str, BackgroundTaskHandle] = {}
        self._counter = 0

    def create_task(self, description: str, metadata: JsonObject | None = None) -> TaskRecord:
        self._counter += 1
        task_id = f"task_{self._counter}"
        record = TaskRecord(
            task_id=task_id,
            type=LocalTaskKind.GENERIC.value,
            status=TerminalStatus.COMPLETED,
            description=description,
            start_time=datetime.now(UTC).isoformat(),
            metadata=metadata,
        )
        self._tasks[task_id] = record
        return record

    def create_background_task(
        self,
        description: str,
        metadata: JsonObject | None = None,
        detached: bool = True,
    ) -> BackgroundTaskHandle:
        record = self._create_running_task(
            task_kind=LocalTaskKind.BACKGROUND,
            description=description,
            metadata=metadata,
        )
        handle = BackgroundTaskHandle(
            task_id=record.task_id,
            task_kind=LocalTaskKind.BACKGROUND,
            description=description,
            detached=detached,
        )
        self._handles[record.task_id] = handle
        return handle

    def create_verifier_task(
        self,
        description: str,
        metadata: JsonObject | None = None,
    ) -> BackgroundTaskHandle:
        record = self._create_running_task(
            task_kind=LocalTaskKind.VERIFIER,
            description=description,
            metadata=metadata,
        )
        handle = BackgroundTaskHandle(
            task_id=record.task_id,
            task_kind=LocalTaskKind.VERIFIER,
            description=description,
            detached=False,
        )
        self._handles[record.task_id] = handle
        return handle

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

    def checkpoint_task(self, task_id: str, checkpoint: JsonObject) -> None:
        handle = self._handles[task_id]
        handle.checkpoints.append(checkpoint)

    def complete_task(
        self,
        task_id: str,
        output_ref: str | None = None,
        metadata: JsonObject | None = None,
    ) -> None:
        record = self._tasks[task_id]
        record.status = TerminalStatus.COMPLETED
        record.end_time = datetime.now(UTC).isoformat()
        record.output_ref = output_ref
        if metadata is not None:
            record.metadata = metadata

    def fail_task(
        self,
        task_id: str,
        reason: str,
        metadata: JsonObject | None = None,
    ) -> None:
        record = self._tasks[task_id]
        record.status = TerminalStatus.FAILED
        record.end_time = datetime.now(UTC).isoformat()
        merged_metadata = dict(record.metadata or {})
        merged_metadata["reason"] = reason
        if metadata is not None:
            merged_metadata.update(metadata)
        record.metadata = merged_metadata

    def get_task(self, task_id: str) -> TaskRecord:
        return self._tasks[task_id]

    def get_handle(self, task_id: str) -> BackgroundTaskHandle:
        return self._handles[task_id]

    def list_tasks(self) -> list[TaskRecord]:
        return [self._tasks[task_id] for task_id in sorted(self._tasks)]

    def _create_running_task(
        self,
        task_kind: LocalTaskKind,
        description: str,
        metadata: JsonObject | None,
    ) -> TaskRecord:
        self._counter += 1
        task_id = f"task_{self._counter}"
        record = TaskRecord(
            task_id=task_id,
            type=task_kind.value,
            status="running",
            description=description,
            start_time=datetime.now(UTC).isoformat(),
            metadata=metadata,
        )
        self._tasks[task_id] = record
        return record


class FileTaskManager:
    """Persist local tasks and handles for restart-safe background workflows."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._tasks_dir = self._root / "tasks"
        self._handles_dir = self._root / "handles"
        self._counter_file = self._root / "counter.txt"
        self._tasks_dir.mkdir(parents=True, exist_ok=True)
        self._handles_dir.mkdir(parents=True, exist_ok=True)

    def create_task(self, description: str, metadata: JsonObject | None = None) -> TaskRecord:
        task_id = self._next_task_id()
        record = TaskRecord(
            task_id=task_id,
            type=LocalTaskKind.GENERIC.value,
            status=TerminalStatus.COMPLETED,
            description=description,
            start_time=datetime.now(UTC).isoformat(),
            metadata=metadata,
        )
        self._write_task(record)
        return record

    def create_background_task(
        self,
        description: str,
        metadata: JsonObject | None = None,
        detached: bool = True,
    ) -> BackgroundTaskHandle:
        record = self._create_running_task(LocalTaskKind.BACKGROUND, description, metadata)
        handle = BackgroundTaskHandle(
            task_id=record.task_id,
            task_kind=LocalTaskKind.BACKGROUND,
            description=description,
            detached=detached,
        )
        self._write_handle(handle)
        return handle

    def create_verifier_task(
        self,
        description: str,
        metadata: JsonObject | None = None,
    ) -> BackgroundTaskHandle:
        record = self._create_running_task(LocalTaskKind.VERIFIER, description, metadata)
        handle = BackgroundTaskHandle(
            task_id=record.task_id,
            task_kind=LocalTaskKind.VERIFIER,
            description=description,
            detached=False,
        )
        self._write_handle(handle)
        return handle

    def update_task(
        self,
        task_id: str,
        status: str,
        metadata: JsonObject | None = None,
    ) -> None:
        record = self.get_task(task_id)
        record.status = status
        if metadata is not None:
            record.metadata = metadata
        self._write_task(record)

    def checkpoint_task(self, task_id: str, checkpoint: JsonObject) -> None:
        handle = self.get_handle(task_id)
        handle.checkpoints.append(checkpoint)
        self._write_handle(handle)

    def complete_task(
        self,
        task_id: str,
        output_ref: str | None = None,
        metadata: JsonObject | None = None,
    ) -> None:
        record = self.get_task(task_id)
        record.status = TerminalStatus.COMPLETED
        record.end_time = datetime.now(UTC).isoformat()
        record.output_ref = output_ref
        if metadata is not None:
            record.metadata = metadata
        self._write_task(record)

    def fail_task(
        self,
        task_id: str,
        reason: str,
        metadata: JsonObject | None = None,
    ) -> None:
        record = self.get_task(task_id)
        record.status = TerminalStatus.FAILED
        record.end_time = datetime.now(UTC).isoformat()
        merged_metadata = dict(record.metadata or {})
        merged_metadata["reason"] = reason
        if metadata is not None:
            merged_metadata.update(metadata)
        record.metadata = merged_metadata
        self._write_task(record)

    def get_task(self, task_id: str) -> TaskRecord:
        return TaskRecord.from_dict(self._read_json(self._task_path(task_id)))

    def get_handle(self, task_id: str) -> BackgroundTaskHandle:
        return BackgroundTaskHandle.from_dict(self._read_json(self._handle_path(task_id)))

    def list_tasks(self) -> list[TaskRecord]:
        records: list[TaskRecord] = []
        for path in sorted(self._tasks_dir.glob("*.json")):
            records.append(TaskRecord.from_dict(self._read_json(path)))
        return records

    def _create_running_task(
        self,
        task_kind: LocalTaskKind,
        description: str,
        metadata: JsonObject | None,
    ) -> TaskRecord:
        task_id = self._next_task_id()
        record = TaskRecord(
            task_id=task_id,
            type=task_kind.value,
            status="running",
            description=description,
            start_time=datetime.now(UTC).isoformat(),
            metadata=metadata,
        )
        self._write_task(record)
        return record

    def _next_task_id(self) -> str:
        current = 0
        if self._counter_file.exists():
            current = int(self._counter_file.read_text(encoding="utf-8").strip() or "0")
        current += 1
        self._counter_file.write_text(str(current), encoding="utf-8")
        return f"task_{current}"

    def _task_path(self, task_id: str) -> Path:
        return self._tasks_dir / f"{task_id}.json"

    def _handle_path(self, task_id: str) -> Path:
        return self._handles_dir / f"{task_id}.json"

    def _write_task(self, record: TaskRecord) -> None:
        self._task_path(record.task_id).write_text(
            json.dumps(record.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _write_handle(self, handle: BackgroundTaskHandle) -> None:
        self._handle_path(handle.task_id).write_text(
            json.dumps(handle.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _read_json(self, path: Path) -> JsonObject:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Expected JSON object in {path}")
        return cast(JsonObject, payload)
