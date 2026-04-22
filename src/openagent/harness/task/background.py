"""Background task execution helpers for the local harness baseline."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Lock
from time import perf_counter

from openagent.harness.task.manager import FileTaskManager, InMemoryTaskManager
from openagent.harness.task.models import (
    BackgroundTaskContext,
    BackgroundTaskHandle,
)
from openagent.object_model import (
    JsonObject,
    RuntimeEvent,
    RuntimeEventType,
    TaskRecord,
    TerminalStatus,
)
from openagent.observability import (
    AgentObservability,
    ProgressUpdate,
    RuntimeMetric,
    SpanHandle,
)


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
        self._append_event(task_id, killed_event)
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
            self._append_event(handle.task_id, completed_event)
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
            self._append_event(handle.task_id, failed_event)
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
        self._append_event(task_id, progress_event)
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
        self._append_event(task_id, progress_event)
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
