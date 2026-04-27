"""Task managers with in-memory and file-backed persistence baselines."""

from __future__ import annotations

import builtins
from datetime import UTC, datetime
from typing import cast

from openagent.harness.task.interfaces import TaskManager
from openagent.harness.task.models import (
    BackgroundTaskHandle,
    LocalTaskKind,
    TaskEventSlice,
    TaskOutputSlice,
    TaskRetentionPolicy,
    TaskSelector,
    VerificationRequest,
    VerificationResult,
    VerifierTaskHandle,
)
from openagent.harness.task.registry import (
    TaskImplementation,
    TaskImplementationRegistry,
    TaskRegistry,
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


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


class _TaskManagerBase(TaskManager):
    """Shared task lifecycle implementation for in-memory and file-backed managers."""

    def __init__(self, registry: TaskRegistry) -> None:
        self.registry = registry
        self.implementations = TaskImplementationRegistry()
        self._verification_results: dict[str, VerificationResult] = {}

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
        task = TaskRecord(
            task_id=self.registry.next_task_id(),
            type=task_type,
            status=TerminalStatus.PENDING,
            description=description,
            start_time=_iso_now(),
            session_id=session_id,
            agent_id=agent_id,
            parent_task_id=parent_task_id,
            metadata=metadata,
        )
        self.register(task)
        self.update(task.task_id, status=TerminalStatus.RUNNING.value)
        self.append_event(
            task.task_id,
            TaskEvent(
                task_id=task.task_id,
                event_id=f"task_started:{task.task_id}:1",
                timestamp=_iso_now(),
                type="started",
                payload={"description": description, **(metadata or {})},
            ),
        )
        return self.get(task.task_id)

    def register(self, task: TaskRecord) -> TaskRecord:
        return self.registry.register(task)

    def append_event(self, task_id: str, event: TaskEvent) -> TaskEvent:
        return self.registry.append_event(task_id, event)

    def append_output(self, task_id: str, item: JsonValue) -> int:
        return self.registry.append_output(task_id, item)

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
        return self.registry.update(
            task_id,
            status=status,
            metadata=metadata,
            output_ref=output_ref,
            output_cursor=output_cursor,
            terminal_state=terminal_state,
            notified=notified,
            end_time=end_time,
        )

    def attach_output(self, task_id: str, output_ref: str) -> TaskRecord:
        return self.registry.attach_output(task_id, output_ref)

    def kill(self, task_id: str) -> TaskRecord:
        record = self.get(task_id)
        impl = self.implementations.get(task_id, record.type)
        if impl is not None and impl.kill_task is not None:
            return impl.kill_task(task_id)
        terminal_state = TerminalState(status=TerminalStatus.KILLED, reason="killed")
        updated = self.update(
            task_id,
            status=TerminalStatus.KILLED.value,
            terminal_state=terminal_state,
        )
        self.append_event(
            task_id,
            TaskEvent(
                task_id=task_id,
                event_id=f"task_killed:{task_id}:{self.read_events(task_id).cursor + 1}",
                timestamp=_iso_now(),
                type="killed",
                payload={"reason": "killed"},
                terminal_state=terminal_state.to_dict(),
            ),
        )
        return updated

    def list(self, selector: TaskSelector | None = None) -> builtins.list[TaskRecord]:
        return self.registry.list(selector)

    def get(self, task_id: str) -> TaskRecord:
        return self.registry.get(task_id)

    def read_output(self, task_id: str, cursor: int = 0) -> TaskOutputSlice:
        record = self.get(task_id)
        impl = self.implementations.get(task_id, record.type)
        if impl is not None and impl.read_output is not None:
            return impl.read_output(task_id, cursor)
        return self.registry.read_output(task_id, cursor)

    def read_events(self, task_id: str, cursor: int = 0) -> TaskEventSlice:
        record = self.get(task_id)
        impl = self.implementations.get(task_id, record.type)
        if impl is not None and impl.read_events is not None:
            return impl.read_events(task_id, cursor)
        return self.registry.read_events(task_id, cursor)

    def await_task(self, task_id: str, timeout: float | None = None) -> TaskRecord:
        record = self.get(task_id)
        impl = self.implementations.get(task_id, record.type)
        if impl is not None and impl.await_task is not None:
            return impl.await_task(task_id, timeout)
        return self.get(task_id)

    def attach_observer(self, task_id: str, binding_id: str) -> None:
        self.registry.attach_observer(task_id, binding_id)

    def detach_observer(self, task_id: str, binding_id: str) -> None:
        self.registry.detach_observer(task_id, binding_id)

    def mark_notified(self, task_id: str) -> TaskRecord:
        return self.registry.mark_notified(task_id)

    def evict_expired(self, *, now_timestamp: str | None = None) -> builtins.list[str]:
        removed = self.registry.evict_expired(now_timestamp=now_timestamp)
        for task_id in removed:
            self.implementations.remove(task_id)
            self._verification_results.pop(task_id, None)
        return removed

    def register_implementation(self, task_id: str, implementation: TaskImplementation) -> None:
        self.implementations.register_task(task_id, implementation)

    def create_task(self, description: str, metadata: JsonObject | None = None) -> TaskRecord:
        record = self.spawn(
            task_type=LocalTaskKind.GENERIC.value,
            description=description,
            metadata=metadata,
        )
        terminal_state = TerminalState(status=TerminalStatus.COMPLETED, reason="completed")
        self.update(
            record.task_id,
            status=TerminalStatus.COMPLETED.value,
            terminal_state=terminal_state,
        )
        return self.get(record.task_id)

    def update_task(
        self,
        task_id: str,
        status: str,
        metadata: JsonObject | None = None,
    ) -> None:
        self.update(task_id, status=status, metadata=metadata)

    def checkpoint_task(self, task_id: str, checkpoint: JsonObject) -> None:
        self.append_event(
            task_id,
            TaskEvent(
                task_id=task_id,
                event_id=f"task_progress:{task_id}:{self.read_events(task_id).cursor + 1}",
                timestamp=_iso_now(),
                type="progress",
                payload=checkpoint,
            ),
        )

    def complete_task(
        self,
        task_id: str,
        output_ref: str | None = None,
        metadata: JsonObject | None = None,
    ) -> None:
        if output_ref is not None:
            self.attach_output(task_id, output_ref)
        terminal_state = TerminalState(status=TerminalStatus.COMPLETED, reason="completed")
        self.update(
            task_id,
            status=TerminalStatus.COMPLETED.value,
            metadata=metadata,
            output_ref=output_ref,
            terminal_state=terminal_state,
        )
        self.append_event(
            task_id,
            TaskEvent(
                task_id=task_id,
                event_id=f"task_completed:{task_id}:{self.read_events(task_id).cursor + 1}",
                timestamp=_iso_now(),
                type="completed",
                payload=cast(JsonObject, {"output_ref": output_ref, **(metadata or {})}),
                terminal_state=terminal_state.to_dict(),
            ),
        )

    def fail_task(
        self,
        task_id: str,
        reason: str,
        metadata: JsonObject | None = None,
    ) -> None:
        merged = {"reason": reason, **(metadata or {})}
        terminal_state = TerminalState(status=TerminalStatus.FAILED, reason=reason)
        self.update(
            task_id,
            status=TerminalStatus.FAILED.value,
            metadata=merged,
            terminal_state=terminal_state,
        )
        self.append_event(
            task_id,
            TaskEvent(
                task_id=task_id,
                event_id=f"task_failed:{task_id}:{self.read_events(task_id).cursor + 1}",
                timestamp=_iso_now(),
                type="failed",
                payload=merged,
                terminal_state=terminal_state.to_dict(),
                error={"reason": reason},
            ),
        )

    def get_task(self, task_id: str) -> TaskRecord:
        return self.get(task_id)

    def list_tasks(self) -> builtins.list[TaskRecord]:
        return self.list()

    def get_handle(self, task_id: str) -> BackgroundTaskHandle:
        record = self.get(task_id)
        events = self.read_events(task_id).events
        checkpoints: list[JsonObject] = []
        for event in events:
            payload = event.get("payload")
            if event.get("type") == "progress" and isinstance(payload, dict):
                checkpoints.append(dict(payload))
        task_kind = (
            LocalTaskKind(record.type)
            if record.type in {item.value for item in LocalTaskKind}
            else LocalTaskKind.GENERIC
        )
        return BackgroundTaskHandle(
            task_id=record.task_id,
            task_kind=task_kind,
            description=record.description,
            detached=bool(
                (record.metadata or {}).get("detached", task_kind is LocalTaskKind.BACKGROUND)
            ),
            session_id=record.session_id,
            agent_id=record.agent_id,
            status=str(record.status),
            output_ref=record.output_ref,
            checkpoints=checkpoints,
        )

    def create_background_task(
        self,
        description: str,
        metadata: JsonObject | None = None,
        detached: bool = True,
        session_id: str | None = None,
        agent_id: str | None = None,
        parent_task_id: str | None = None,
    ) -> BackgroundTaskHandle:
        task_metadata = dict(metadata or {})
        task_metadata["detached"] = detached
        record = self.spawn(
            task_type=LocalTaskKind.BACKGROUND.value,
            description=description,
            metadata=task_metadata,
            session_id=session_id,
            agent_id=agent_id,
            parent_task_id=parent_task_id,
        )
        return BackgroundTaskHandle(
            task_id=record.task_id,
            task_kind=LocalTaskKind.BACKGROUND,
            description=description,
            detached=detached,
            session_id=session_id,
            agent_id=agent_id,
            status=str(record.status),
            output_ref=record.output_ref,
        )

    def create_verifier_task(
        self,
        description: str,
        metadata: JsonObject | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
        parent_task_id: str | None = None,
    ) -> VerifierTaskHandle:
        record = self.spawn(
            task_type=LocalTaskKind.VERIFIER.value,
            description=description,
            metadata=metadata,
            session_id=session_id,
            agent_id=agent_id,
            parent_task_id=parent_task_id,
        )
        return VerifierTaskHandle(
            task_id=record.task_id,
            description=description,
            session_id=session_id,
            agent_id=agent_id,
            status=str(record.status),
            output_ref=record.output_ref,
        )

    def run_verifier(
        self,
        request: VerificationRequest,
        *,
        detached: bool = False,
    ) -> VerifierTaskHandle:
        del detached
        return self.create_verifier_task(
            description=request.prompt,
            metadata=cast(JsonObject, {
                "target_session": request.target_session,
                "original_task": request.original_task,
                "changed_artifacts": cast(list[JsonValue], list(request.changed_artifacts)),
                "evidence_scope": cast(list[JsonValue], list(request.evidence_scope)),
                "review_policy": request.review_policy,
                "source_command_id": request.source_command_id,
            }),
            session_id=request.target_session,
        )

    def complete_verifier(
        self,
        task_id: str,
        result: VerificationResult,
    ) -> VerificationResult:
        self._verification_results[task_id] = result
        self.complete_task(
            task_id,
            output_ref=result.output_ref,
            metadata=cast(JsonObject, {
                "verdict": result.verdict.value,
                "summary": result.summary,
                "evidence": cast(list[JsonValue], list(result.evidence)),
                "findings": cast(list[JsonValue], list(result.findings)),
                "limitations": cast(list[JsonValue], list(result.limitations)),
                **(result.metadata or {}),
            }),
        )
        return result

    def await_verifier(
        self,
        handle: VerifierTaskHandle,
        timeout: float | None = None,
    ) -> VerificationResult:
        self.await_task(handle.task_id, timeout)
        result = self._verification_results.get(handle.task_id)
        if result is not None:
            return result
        record = self.get(handle.task_id)
        metadata = cast(JsonObject, record.metadata or {})
        from openagent.harness.task.models import VerificationResult, VerificationVerdict

        verdict_raw = metadata.get("verdict", "partial")
        try:
            verdict = VerificationVerdict(str(verdict_raw))
        except ValueError:
            verdict = VerificationVerdict.PARTIAL
        evidence_raw = metadata.get("evidence")
        findings_raw = metadata.get("findings")
        limitations_raw = metadata.get("limitations")
        return VerificationResult(
            verdict=verdict,
            summary=str(metadata.get("summary", record.description)),
            evidence=(
                [str(item) for item in evidence_raw] if isinstance(evidence_raw, list) else []
            ),
            findings=(
                [str(item) for item in findings_raw] if isinstance(findings_raw, list) else []
            ),
            limitations=(
                [str(item) for item in limitations_raw]
                if isinstance(limitations_raw, list)
                else []
            ),
            task_id=handle.task_id,
            output_ref=record.output_ref,
            metadata=metadata,
        )


class InMemoryTaskManager(_TaskManagerBase):
    """Task manager suitable for local harness task tests."""

    def __init__(self, retention_policy: TaskRetentionPolicy | None = None) -> None:
        super().__init__(TaskRegistry(InMemoryTaskStorage(), retention_policy=retention_policy))


class FileTaskManager(_TaskManagerBase):
    """Persist local tasks and handles for restart-safe background workflows."""

    def __init__(
        self,
        root: str,
        retention_policy: TaskRetentionPolicy | None = None,
    ) -> None:
        self.root = root
        super().__init__(TaskRegistry(FileTaskStorage(root), retention_policy=retention_policy))
