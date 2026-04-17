"""Local task managers with in-memory and file-backed persistence baselines."""

from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from threading import Lock
from time import perf_counter

from openagent.object_model import (
    JsonObject,
    RuntimeEvent,
    RuntimeEventType,
    SerializableModel,
    TaskRecord,
    TerminalStatus,
)
from openagent.observability import (
    AgentObservability,
    ProgressUpdate,
    RuntimeMetric,
    SpanHandle,
)


class LocalTaskKind(StrEnum):
    GENERIC = "generic"
    BACKGROUND = "background"
    VERIFIER = "verifier"


@dataclass(slots=True)
class BackgroundTaskHandle(SerializableModel):
    task_id: str
    task_kind: LocalTaskKind
    description: str
    detached: bool = False
    checkpoints: list[JsonObject] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: JsonObject) -> BackgroundTaskHandle:
        checkpoints = data.get("checkpoints")
        return cls(
            task_id=str(data["task_id"]),
            task_kind=LocalTaskKind(str(data["task_kind"])),
            description=str(data["description"]),
            detached=bool(data.get("detached", False)),
            checkpoints=[item for item in checkpoints if isinstance(item, dict)]
            if isinstance(checkpoints, list)
            else [],
        )


@dataclass(slots=True)
class BackgroundTaskContext:
    task_id: str
    _checkpoint: Callable[[str, JsonObject], None]
    _emit_progress: Callable[[str, JsonObject], None]

    def checkpoint(self, payload: JsonObject) -> None:
        self._checkpoint(self.task_id, payload)

    def progress(self, payload: JsonObject) -> None:
        self._emit_progress(self.task_id, payload)


class InMemoryTaskManager:
    """Task manager suitable for local orchestration tests."""

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
        return json.loads(path.read_text(encoding="utf-8"))


class LocalBackgroundAgentOrchestrator:
    """Run background tasks without blocking the parent local runtime path."""

    def __init__(
        self,
        task_manager: InMemoryTaskManager | FileTaskManager,
        max_workers: int = 4,
        observability: AgentObservability | None = None,
    ) -> None:
        self._task_manager = task_manager
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._events: dict[str, list[RuntimeEvent]] = {}
        self._futures: dict[str, Future[JsonObject | str | None]] = {}
        self._lock = Lock()
        self._observability = observability or AgentObservability()
        self._task_spans: dict[str, SpanHandle] = {}
        self._task_started_at: dict[str, float] = {}

    def start_background_task(
        self,
        description: str,
        worker: Callable[[BackgroundTaskContext], JsonObject | str | None],
        metadata: JsonObject | None = None,
    ) -> BackgroundTaskHandle:
        handle = self._task_manager.create_background_task(description, metadata=metadata)
        created_event = self._task_event(
            RuntimeEventType.TASK_CREATED,
            handle.task_id,
            {"description": description, **(metadata or {})},
        )
        self._append_event(handle.task_id, created_event)
        self._observability.project_external_event(created_event)
        self._observability.emit_progress(
            ProgressUpdate(
                scope="background_agent",
                task_id=handle.task_id,
                summary=description,
                last_activity="task_created",
                attributes=metadata or {},
            )
        )
        self._task_started_at[handle.task_id] = perf_counter()
        self._task_spans[handle.task_id] = self._observability.start_span(
            "background_task",
            {"description": description, **(metadata or {})},
            task_id=handle.task_id,
        )
        future = self._executor.submit(self._run_worker, handle, worker)
        self._futures[handle.task_id] = future
        return handle

    def list_events(self, task_id: str, after: int = 0) -> list[RuntimeEvent]:
        return self._events.get(task_id, [])[after:]

    def get_task(self, task_id: str) -> TaskRecord:
        return self._task_manager.get_task(task_id)

    def kill_task(self, task_id: str) -> None:
        future = self._futures.get(task_id)
        if future is not None:
            future.cancel()
        self._task_manager.update_task(task_id, TerminalStatus.STOPPED.value)
        killed_event = self._task_event(
            RuntimeEventType.TASK_KILLED,
            task_id,
            {"reason": "killed"},
        )
        self._append_event(
            task_id,
            killed_event,
        )
        self._observability.project_external_event(killed_event)
        started_at = self._task_started_at.get(task_id)
        duration_ms = (perf_counter() - started_at) * 1000 if started_at is not None else None
        self._observability.emit_progress(
            ProgressUpdate(
                scope="background_agent",
                task_id=task_id,
                summary="killed",
                last_activity="task_killed",
                duration_ms=duration_ms,
            )
        )
        span = self._task_spans.pop(task_id, None)
        if span is not None:
            self._observability.end_span(
                span,
                {"reason": "killed"},
                status="cancelled",
                duration_ms=duration_ms,
            )

    def _run_worker(
        self,
        handle: BackgroundTaskHandle,
        worker: Callable[[BackgroundTaskContext], JsonObject | str | None],
    ) -> JsonObject | str | None:
        context = BackgroundTaskContext(
            task_id=handle.task_id,
            _checkpoint=self._checkpoint_task,
            _emit_progress=self._emit_progress,
        )
        try:
            result = worker(context)
            output_ref = self._coerce_output_ref(result)
            metadata = result if isinstance(result, dict) else None
            self._task_manager.complete_task(
                handle.task_id,
                output_ref=output_ref,
                metadata=metadata,
            )
            completed_event = self._task_event(
                RuntimeEventType.TASK_COMPLETED,
                handle.task_id,
                {"output_ref": output_ref, **(metadata or {})},
            )
            self._append_event(
                handle.task_id,
                completed_event,
            )
            self._observability.project_external_event(completed_event)
            started_at = self._task_started_at.get(handle.task_id)
            duration_ms = (
                (perf_counter() - started_at) * 1000 if started_at is not None else None
            )
            self._observability.emit_progress(
                ProgressUpdate(
                    scope="background_agent",
                    task_id=handle.task_id,
                    summary="completed",
                    last_activity="task_completed",
                    duration_ms=duration_ms,
                    attributes=metadata or {},
                )
            )
            if duration_ms is not None:
                self._observability.emit_runtime_metric(
                    RuntimeMetric(
                        name="background_task.duration_ms",
                        value=duration_ms,
                        unit="ms",
                        task_id=handle.task_id,
                    )
                )
            span = self._task_spans.pop(handle.task_id, None)
            if span is not None:
                self._observability.end_span(
                    span,
                    {"output_ref": output_ref, **(metadata or {})},
                    status="completed",
                    duration_ms=duration_ms,
                )
            return result
        except Exception as exc:
            self._task_manager.fail_task(handle.task_id, str(exc))
            failed_event = self._task_event(
                RuntimeEventType.TASK_FAILED,
                handle.task_id,
                {"reason": str(exc)},
            )
            self._append_event(
                handle.task_id,
                failed_event,
            )
            self._observability.project_external_event(failed_event)
            started_at = self._task_started_at.get(handle.task_id)
            duration_ms = (
                (perf_counter() - started_at) * 1000 if started_at is not None else None
            )
            self._observability.emit_progress(
                ProgressUpdate(
                    scope="background_agent",
                    task_id=handle.task_id,
                    summary=str(exc),
                    last_activity="task_failed",
                    duration_ms=duration_ms,
                )
            )
            span = self._task_spans.pop(handle.task_id, None)
            if span is not None:
                self._observability.end_span(
                    span,
                    {"reason": str(exc)},
                    status="error",
                    duration_ms=duration_ms,
                )
            raise

    def _checkpoint_task(self, task_id: str, payload: JsonObject) -> None:
        self._task_manager.checkpoint_task(task_id, payload)
        progress_event = self._task_event(RuntimeEventType.TASK_PROGRESS, task_id, payload)
        self._append_event(
            task_id,
            progress_event,
        )
        self._observability.project_external_event(progress_event)
        self._observability.emit_progress(
            ProgressUpdate(
                scope="task",
                task_id=task_id,
                summary="checkpoint",
                last_activity="checkpoint",
                attributes=payload,
            )
        )

    def _emit_progress(self, task_id: str, payload: JsonObject) -> None:
        progress_event = self._task_event(RuntimeEventType.TASK_PROGRESS, task_id, payload)
        self._append_event(
            task_id,
            progress_event,
        )
        self._observability.project_external_event(progress_event)
        self._observability.emit_progress(
            ProgressUpdate(
                scope="background_agent",
                task_id=task_id,
                summary=(
                    str(payload.get("summary", "progress"))
                    if payload.get("summary") is not None
                    else "progress"
                ),
                last_activity="task_progress",
                attributes=payload,
            )
        )

    def _append_event(self, task_id: str, event: RuntimeEvent) -> None:
        with self._lock:
            self._events.setdefault(task_id, []).append(event)

    def _task_event(
        self,
        event_type: RuntimeEventType,
        task_id: str,
        payload: JsonObject,
    ) -> RuntimeEvent:
        return RuntimeEvent(
            event_type=event_type,
            event_id=f"{event_type.value}:{task_id}:{len(self._events.get(task_id, [])) + 1}",
            timestamp=datetime.now(UTC).isoformat(),
            session_id="background",
            task_id=task_id,
            payload=payload,
        )

    def _coerce_output_ref(self, result: JsonObject | str | None) -> str | None:
        if isinstance(result, str):
            return result
        if isinstance(result, dict) and isinstance(result.get("output_ref"), str):
            return str(result["output_ref"])
        return None
