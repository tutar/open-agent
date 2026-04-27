"""Background task execution helpers for the local harness baseline."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from datetime import UTC, datetime
from threading import Lock
from time import perf_counter
from typing import Any

from openagent.harness.task.manager import FileTaskManager, InMemoryTaskManager
from openagent.harness.task.models import (
    BackgroundTaskContext,
    BackgroundTaskHandle,
    VerificationResult,
    VerifierTaskHandle,
)
from openagent.harness.task.registry import TaskImplementation
from openagent.object_model import (
    JsonObject,
    JsonValue,
    RuntimeEvent,
    RuntimeEventType,
    TaskEvent,
    TaskRecord,
    TerminalState,
    TerminalStatus,
)
from openagent.observability import (
    AgentObservability,
    ProgressUpdate,
    RuntimeMetric,
    SpanHandle,
)
from openagent.observability.metrics import normalized_duration_metrics


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


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
        self._futures: dict[str, Future[Any]] = {}
        self._lock = Lock()
        self._observability = observability or AgentObservability()
        self._task_spans: dict[str, SpanHandle] = {}
        self._task_started_at: dict[str, float] = {}

    def start_background_task(
        self,
        description: str,
        worker: Callable[[BackgroundTaskContext], JsonObject | str | None],
        metadata: JsonObject | None = None,
        *,
        session_id: str | None = None,
        agent_id: str | None = None,
        parent_task_id: str | None = None,
    ) -> BackgroundTaskHandle:
        handle = self._task_manager.create_background_task(
            description,
            metadata=metadata,
            session_id=session_id,
            agent_id=agent_id,
            parent_task_id=parent_task_id,
        )
        created_event = self._runtime_event(
            RuntimeEventType.TASK_CREATED,
            handle.task_id,
            {"description": description, **(metadata or {})},
            session_id=session_id,
            agent_id=agent_id,
        )
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
        self._task_manager.register_implementation(
            handle.task_id,
            TaskImplementation(
                await_task=self._await_task,
                kill_task=self._kill_task,
                read_output=lambda task_id, cursor: self._task_manager.registry.read_output(
                    task_id,
                    cursor,
                ),
                read_events=lambda task_id, cursor: self._task_manager.registry.read_events(
                    task_id,
                    cursor,
                ),
            ),
        )
        future = self._executor.submit(self._run_worker, handle, worker)
        self._futures[handle.task_id] = future
        return handle

    def start_verifier_task(
        self,
        description: str,
        worker: Callable[[BackgroundTaskContext], VerificationResult],
        metadata: JsonObject | None = None,
        *,
        session_id: str | None = None,
        agent_id: str | None = None,
        parent_task_id: str | None = None,
    ) -> VerifierTaskHandle:
        handle = self._task_manager.create_verifier_task(
            description,
            metadata=metadata,
            session_id=session_id,
            agent_id=agent_id,
            parent_task_id=parent_task_id,
        )
        self._task_manager.register_implementation(
            handle.task_id,
            TaskImplementation(
                await_task=self._await_task,
                kill_task=self._kill_task,
                read_output=lambda task_id, cursor: self._task_manager.registry.read_output(
                    task_id,
                    cursor,
                ),
                read_events=lambda task_id, cursor: self._task_manager.registry.read_events(
                    task_id,
                    cursor,
                ),
            ),
        )
        future = self._executor.submit(self._run_verifier_worker, handle.task_id, worker)
        self._futures[handle.task_id] = future
        return handle

    def list_events(self, task_id: str, after: int = 0) -> list[RuntimeEvent]:
        slice_ = self._task_manager.read_events(task_id, after)
        return [self._to_runtime_event(event) for event in slice_.events]

    def get_task(self, task_id: str) -> TaskRecord:
        return self._task_manager.get(task_id)

    def kill_task(self, task_id: str) -> None:
        self._kill_task(task_id)

    def _await_task(self, task_id: str, timeout: float | None) -> TaskRecord:
        future = self._futures.get(task_id)
        if future is not None:
            try:
                future.result(timeout=timeout)
            except TimeoutError:
                return self._task_manager.get(task_id)
        return self._task_manager.get(task_id)

    def _kill_task(self, task_id: str) -> TaskRecord:
        future = self._futures.get(task_id)
        if future is not None:
            future.cancel()
        terminal_state = TerminalState(status=TerminalStatus.KILLED, reason="killed")
        record = self._task_manager.update(
            task_id,
            status=TerminalStatus.KILLED.value,
            terminal_state=terminal_state,
        )
        killed_event = TaskEvent(
            task_id=task_id,
            event_id=f"task_killed:{task_id}:{self._task_manager.read_events(task_id).cursor + 1}",
            timestamp=_iso_now(),
            type="killed",
            payload={"reason": "killed"},
            terminal_state=terminal_state.to_dict(),
        )
        self._task_manager.append_event(task_id, killed_event)
        self._observability.project_external_event(
            self._runtime_event(
                RuntimeEventType.TASK_KILLED,
                task_id,
                {"reason": "killed"},
                session_id=record.session_id,
                agent_id=record.agent_id,
            )
        )
        self._finish_task_span(task_id, status="cancelled", payload={"reason": "killed"})
        return record

    def _run_worker(
        self,
        handle: BackgroundTaskHandle,
        worker: Callable[[BackgroundTaskContext], JsonObject | str | None],
    ) -> JsonObject | str | None:
        context = BackgroundTaskContext(
            task_id=handle.task_id,
            _checkpoint=self._checkpoint_task,
            _emit_progress=self._emit_progress,
            _append_output=self._append_output,
        )
        try:
            result = worker(context)
            output_ref = self._coerce_output_ref(result) or f"memory://tasks/{handle.task_id}/output"
            metadata = result if isinstance(result, dict) else None
            self._task_manager.complete_task(
                handle.task_id,
                output_ref=output_ref,
                metadata=metadata,
            )
            self._observability.project_external_event(
                self._runtime_event(
                    RuntimeEventType.TASK_COMPLETED,
                    handle.task_id,
                    {"output_ref": output_ref, **(metadata or {})},
                )
            )
            self._emit_terminal_progress(handle.task_id, "completed", metadata or {})
            self._finish_task_span(
                handle.task_id,
                status="completed",
                payload={"output_ref": output_ref, **(metadata or {})},
            )
            return result
        except Exception as exc:
            self._task_manager.fail_task(handle.task_id, str(exc))
            self._observability.project_external_event(
                self._runtime_event(
                    RuntimeEventType.TASK_FAILED,
                    handle.task_id,
                    {"reason": str(exc)},
                )
            )
            self._emit_terminal_progress(handle.task_id, "task_failed", {"reason": str(exc)})
            self._finish_task_span(
                handle.task_id,
                status="error",
                payload={"reason": str(exc)},
            )
            raise

    def _run_verifier_worker(
        self,
        task_id: str,
        worker: Callable[[BackgroundTaskContext], VerificationResult],
    ) -> VerificationResult:
        context = BackgroundTaskContext(
            task_id=task_id,
            _checkpoint=self._checkpoint_task,
            _emit_progress=self._emit_progress,
            _append_output=self._append_output,
        )
        result = worker(context)
        self._task_manager.complete_verifier(task_id, result)
        self._emit_terminal_progress(
            task_id,
            "verification_completed",
            {"verdict": result.verdict.value, "summary": result.summary},
        )
        self._finish_task_span(
            task_id,
            status="completed",
            payload={"verdict": result.verdict.value, "summary": result.summary},
        )
        return result

    def _checkpoint_task(self, task_id: str, payload: JsonObject) -> None:
        self._task_manager.checkpoint_task(task_id, payload)
        self._observability.project_external_event(
            self._runtime_event(RuntimeEventType.TASK_PROGRESS, task_id, payload)
        )
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
        event = TaskEvent(
            task_id=task_id,
            event_id=(
                f"task_progress:{task_id}:{self._task_manager.read_events(task_id).cursor + 1}"
            ),
            timestamp=_iso_now(),
            type="progress",
            payload=payload,
        )
        self._task_manager.append_event(task_id, event)
        self._observability.project_external_event(
            self._runtime_event(RuntimeEventType.TASK_PROGRESS, task_id, payload)
        )
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

    def _append_output(self, task_id: str, payload: JsonValue) -> int:
        return self._task_manager.append_output(task_id, payload)

    def _emit_terminal_progress(
        self,
        task_id: str,
        last_activity: str,
        payload: JsonObject,
    ) -> None:
        record = self._task_manager.get(task_id)
        started_at = self._task_started_at.get(task_id)
        duration_ms = (perf_counter() - started_at) * 1000 if started_at is not None else None
        self._observability.emit_progress(
            ProgressUpdate(
                scope="background_agent",
                task_id=task_id,
                summary=str(payload.get("summary", last_activity)),
                last_activity=last_activity,
                duration_ms=duration_ms,
                attributes=payload,
            )
        )
        if duration_ms is not None and str(record.status) == TerminalStatus.COMPLETED.value:
            self._observability.emit_runtime_metric(
                RuntimeMetric(
                    name="background_task.duration_ms",
                    value=duration_ms,
                    unit="ms",
                    task_id=task_id,
                )
            )
            for metric in normalized_duration_metrics(
                scope="task",
                total_duration_ms=duration_ms,
                total_api_duration_ms=0.0,
                session_id=record.session_id,
                task_id=task_id,
                agent_id=record.agent_id,
                callsite="background_task.execution",
                aggregation="terminal",
            ):
                self._observability.emit_runtime_metric(metric)

    def _finish_task_span(self, task_id: str, *, status: str, payload: JsonObject) -> None:
        started_at = self._task_started_at.get(task_id)
        duration_ms = (perf_counter() - started_at) * 1000 if started_at is not None else None
        span = self._task_spans.pop(task_id, None)
        if span is not None:
            self._observability.end_span(
                span,
                payload,
                status=status,
                duration_ms=duration_ms,
            )

    def _runtime_event(
        self,
        event_type: RuntimeEventType,
        task_id: str,
        payload: JsonObject,
        *,
        session_id: str | None = None,
        agent_id: str | None = None,
    ) -> RuntimeEvent:
        record = self._task_manager.get(task_id)
        return RuntimeEvent(
            event_type=event_type,
            event_id=f"{event_type.value}:{task_id}:{self._task_manager.read_events(task_id).cursor}",
            timestamp=_iso_now(),
            session_id=session_id or record.session_id or "background",
            task_id=task_id,
            agent_id=agent_id or record.agent_id,
            payload=payload,
        )

    def _to_runtime_event(self, payload: JsonObject) -> RuntimeEvent:
        event_type_map = {
            "started": RuntimeEventType.TASK_STARTED,
            "progress": RuntimeEventType.TASK_PROGRESS,
            "completed": RuntimeEventType.TASK_COMPLETED,
            "failed": RuntimeEventType.TASK_FAILED,
            "killed": RuntimeEventType.TASK_KILLED,
            "notification": RuntimeEventType.TASK_NOTIFICATION,
        }
        task_event = TaskEvent.from_dict(payload)
        record = self._task_manager.get(task_event.task_id)
        return RuntimeEvent(
            event_type=event_type_map.get(task_event.type, RuntimeEventType.TASK_PROGRESS),
            event_id=task_event.event_id,
            timestamp=task_event.timestamp,
            session_id=record.session_id or "background",
            task_id=task_event.task_id,
            agent_id=record.agent_id,
            payload=task_event.payload,
        )

    def _coerce_output_ref(self, result: JsonObject | str | None) -> str | None:
        if isinstance(result, str):
            return result
        if isinstance(result, dict) and isinstance(result.get("output_ref"), str):
            return str(result["output_ref"])
        return None
