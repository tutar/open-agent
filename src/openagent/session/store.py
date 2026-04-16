"""In-memory session storage baseline."""

from __future__ import annotations

import json
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from openagent.object_model import RuntimeEvent, SessionHarnessLease
from openagent.session.models import (
    ResumeSnapshot,
    SessionCheckpoint,
    SessionCursor,
    SessionMessage,
    SessionRecord,
    ShortTermMemoryUpdateResult,
    ShortTermSessionMemory,
    WakeRequest,
)


class InMemorySessionStore:
    """Simple session store used for tests and local baseline wiring."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionRecord] = {}
        self._leases: dict[str, SessionHarnessLease] = {}

    def append_event(self, event: RuntimeEvent) -> None:
        self.append_events(event.session_id, [event])

    def append_events(self, session_id: str, events: list[RuntimeEvent]) -> SessionCheckpoint:
        session = self.load_session(session_id)
        session.events.extend(events)
        self.save_session(session_id, session)
        return self.get_checkpoint(session_id)

    def read_events(
        self,
        session_id: str,
        after: int = 0,
        cursor: SessionCursor | None = None,
    ) -> list[RuntimeEvent]:
        session = self.load_session(session_id)
        start = cursor.event_offset if cursor is not None else after
        return session.events[start:]

    def load_session(self, session_id: str) -> SessionRecord:
        return self._sessions.setdefault(session_id, SessionRecord(session_id=session_id))

    def save_session(self, session_id: str, state: SessionRecord) -> None:
        self._sessions[session_id] = state

    def get_checkpoint(self, session_id: str) -> SessionCheckpoint:
        session = self.load_session(session_id)
        last_event_id = session.events[-1].event_id if session.events else None
        cursor = SessionCursor(
            session_id=session_id,
            event_offset=len(session.events),
            last_event_id=last_event_id,
        )
        return SessionCheckpoint(
            session_id=session_id,
            event_offset=len(session.events),
            last_event_id=last_event_id,
            cursor=cursor,
            committed_at=datetime.now(UTC).isoformat(),
        )

    def mark_restored(self, session_id: str, cursor: SessionCursor | None = None) -> None:
        session = self.load_session(session_id)
        checkpoint = self.get_checkpoint(session_id)
        marker = (
            cursor.last_event_id
            if cursor is not None and cursor.last_event_id is not None
            else checkpoint.last_event_id
        )
        session.restore_marker = marker
        self.save_session(session_id, session)

    def get_resume_snapshot(self, wake_request: WakeRequest) -> ResumeSnapshot:
        session = self.load_session(wake_request.session_id)
        events = self.read_events(
            wake_request.session_id,
            cursor=wake_request.cursor,
        )
        return ResumeSnapshot(
            session_id=wake_request.session_id,
            runtime_state={
                "status": session.status.value,
                "restore_marker": session.restore_marker,
            },
            transcript_slice=[message.to_dict() for message in session.messages],
            working_state={
                "pending_tool_calls": [
                    tool_call.to_dict() for tool_call in session.pending_tool_calls
                ],
                "event_count": len(events),
            },
            short_term_memory=(
                dict(session.short_term_memory)
                if isinstance(session.short_term_memory, dict)
                else None
            ),
        )

    def acquire_lease(
        self,
        session_id: str,
        harness_instance_id: str,
        agent_id: str,
    ) -> SessionHarnessLease:
        existing = self._leases.get(session_id)
        if existing is not None and existing.harness_instance_id != harness_instance_id:
            raise ValueError("Session already has an active harness lease")
        lease = SessionHarnessLease(
            session_id=session_id,
            harness_instance_id=harness_instance_id,
            agent_id=agent_id,
            acquired_at=datetime.now(UTC).isoformat(),
        )
        self._leases[session_id] = lease
        return lease

    def release_lease(self, session_id: str, harness_instance_id: str) -> bool:
        existing = self._leases.get(session_id)
        if existing is None or existing.harness_instance_id != harness_instance_id:
            return False
        self._leases.pop(session_id, None)
        return True

    def get_active_lease(self, session_id: str) -> SessionHarnessLease | None:
        return self._leases.get(session_id)


class FileSessionStore:
    """Durable JSON-backed session store for resume semantics."""

    def __init__(self, root_dir: str | Path) -> None:
        self._root_dir = Path(root_dir)
        self._root_dir.mkdir(parents=True, exist_ok=True)

    def append_event(self, event: RuntimeEvent) -> None:
        self.append_events(event.session_id, [event])

    def append_events(self, session_id: str, events: list[RuntimeEvent]) -> SessionCheckpoint:
        session = self.load_session(session_id)
        session.events.extend(events)
        log_path = self._event_log_path(session_id)
        with log_path.open("a", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event.to_dict()) + "\n")
        self.save_session(session_id, session)
        return self.get_checkpoint(session_id)

    def read_events(
        self,
        session_id: str,
        after: int = 0,
        cursor: SessionCursor | None = None,
    ) -> list[RuntimeEvent]:
        log_path = self._event_log_path(session_id)
        if not log_path.exists():
            return []
        events: list[RuntimeEvent] = []
        start = cursor.event_offset if cursor is not None else after
        for line in log_path.read_text(encoding="utf-8").splitlines()[start:]:
            raw = json.loads(line)
            if isinstance(raw, dict):
                events.append(RuntimeEvent.from_dict(raw))
        return events

    def load_session(self, session_id: str) -> SessionRecord:
        path = self._session_path(session_id)
        if not path.exists():
            return SessionRecord(session_id=session_id, events=self.read_events(session_id))

        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError("Session file must contain a JSON object")
        record = SessionRecord.from_dict(data)
        record.events = self.read_events(session_id)
        return record

    def save_session(self, session_id: str, state: SessionRecord) -> None:
        path = self._session_path(session_id)
        path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
        log_path = self._event_log_path(session_id)
        log_lines = [json.dumps(event.to_dict()) for event in state.events]
        if log_lines:
            log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
        elif log_path.exists():
            log_path.unlink()

    def get_checkpoint(self, session_id: str) -> SessionCheckpoint:
        all_events = self.read_events(session_id)
        last_event_id = all_events[-1].event_id if all_events else None
        cursor = SessionCursor(
            session_id=session_id,
            event_offset=len(all_events),
            last_event_id=last_event_id,
        )
        return SessionCheckpoint(
            session_id=session_id,
            event_offset=len(all_events),
            last_event_id=last_event_id,
            cursor=cursor,
            committed_at=datetime.now(UTC).isoformat(),
        )

    def mark_restored(self, session_id: str, cursor: SessionCursor | None = None) -> None:
        session = self.load_session(session_id)
        checkpoint = self.get_checkpoint(session_id)
        marker = (
            cursor.last_event_id
            if cursor is not None and cursor.last_event_id is not None
            else checkpoint.last_event_id
        )
        session.restore_marker = marker
        self.save_session(session_id, session)

    def get_resume_snapshot(self, wake_request: WakeRequest) -> ResumeSnapshot:
        session = self.load_session(wake_request.session_id)
        events = self.read_events(
            wake_request.session_id,
            cursor=wake_request.cursor,
        )
        return ResumeSnapshot(
            session_id=wake_request.session_id,
            runtime_state={
                "status": session.status.value,
                "restore_marker": session.restore_marker,
            },
            transcript_slice=[message.to_dict() for message in session.messages],
            working_state={
                "pending_tool_calls": [
                    tool_call.to_dict() for tool_call in session.pending_tool_calls
                ],
                "event_count": len(events),
            },
            short_term_memory=(
                dict(session.short_term_memory)
                if isinstance(session.short_term_memory, dict)
                else None
            ),
        )

    def acquire_lease(
        self,
        session_id: str,
        harness_instance_id: str,
        agent_id: str,
    ) -> SessionHarnessLease:
        existing = self.get_active_lease(session_id)
        if existing is not None and existing.harness_instance_id != harness_instance_id:
            raise ValueError("Session already has an active harness lease")
        lease = SessionHarnessLease(
            session_id=session_id,
            harness_instance_id=harness_instance_id,
            agent_id=agent_id,
            acquired_at=datetime.now(UTC).isoformat(),
        )
        self._lease_path(session_id).write_text(
            json.dumps(lease.to_dict(), indent=2),
            encoding="utf-8",
        )
        return lease

    def release_lease(self, session_id: str, harness_instance_id: str) -> bool:
        existing = self.get_active_lease(session_id)
        if existing is None or existing.harness_instance_id != harness_instance_id:
            return False
        path = self._lease_path(session_id)
        if path.exists():
            path.unlink()
        return True

    def get_active_lease(self, session_id: str) -> SessionHarnessLease | None:
        path = self._lease_path(session_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError("Lease file must contain a JSON object")
        return SessionHarnessLease.from_dict(data)

    def _session_path(self, session_id: str) -> Path:
        return self._root_dir / f"{session_id}.json"

    def _event_log_path(self, session_id: str) -> Path:
        return self._root_dir / f"{session_id}.events.jsonl"

    def _lease_path(self, session_id: str) -> Path:
        return self._root_dir / f"{session_id}.lease.json"


class InMemoryShortTermMemoryStore:
    """Store session continuity summaries with background-safe updates."""

    def __init__(self) -> None:
        self._records: dict[str, ShortTermSessionMemory] = {}
        self._pending: dict[str, Future[ShortTermSessionMemory]] = {}
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._lock = Lock()

    def load(self, session_id: str) -> ShortTermSessionMemory | None:
        with self._lock:
            return self._records.get(session_id)

    def update(
        self,
        session_id: str,
        transcript_delta: list[SessionMessage],
        current_memory: ShortTermSessionMemory | None,
    ) -> ShortTermMemoryUpdateResult:
        with self._lock:
            future = self._executor.submit(
                self._build_memory,
                session_id,
                transcript_delta,
                current_memory,
            )
            self._pending[session_id] = future
        return ShortTermMemoryUpdateResult(
            memory=current_memory,
            scheduled=True,
            stable=False,
        )

    def get_coverage_boundary(self, session_id: str) -> int | None:
        memory = self.load(session_id)
        return memory.coverage_boundary if memory is not None else None

    def wait_until_stable(
        self,
        session_id: str,
        timeout_ms: int,
    ) -> ShortTermSessionMemory | None:
        with self._lock:
            future = self._pending.get(session_id)
            if future is None:
                return self._records.get(session_id)
        try:
            memory = future.result(timeout=timeout_ms / 1000)
        except FutureTimeoutError:
            return None
        with self._lock:
            self._records[session_id] = memory
            self._pending.pop(session_id, None)
        return memory

    def _build_memory(
        self,
        session_id: str,
        transcript_delta: list[SessionMessage],
        current_memory: ShortTermSessionMemory | None,
    ) -> ShortTermSessionMemory:
        if not transcript_delta:
            if current_memory is not None:
                return current_memory
            return ShortTermSessionMemory(session_id=session_id, summary="")
        last_user = next(
            (message.content for message in reversed(transcript_delta) if message.role == "user"),
            None,
        )
        recent_points = [message.content for message in transcript_delta[-3:]]
        summary = " | ".join(recent_points)
        prior_progress = list(current_memory.progress) if current_memory is not None else []
        progress = [*prior_progress[-2:], *recent_points[-2:]]
        return ShortTermSessionMemory(
            session_id=session_id,
            summary=summary,
            current_goal=last_user,
            progress=progress,
            constraints=list(current_memory.constraints) if current_memory is not None else [],
            coverage_boundary=len(transcript_delta),
            stable=True,
        )


class FileShortTermMemoryStore(InMemoryShortTermMemoryStore):
    """Persist short-term session continuity snapshots to disk."""

    def __init__(self, root: str | Path) -> None:
        super().__init__()
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._load_existing()

    def load(self, session_id: str) -> ShortTermSessionMemory | None:
        memory = super().load(session_id)
        if memory is not None:
            return memory
        path = self._memory_path(session_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError("Short-term memory file must contain a JSON object")
        memory = ShortTermSessionMemory.from_dict(data)
        self._records[session_id] = memory
        return memory

    def wait_until_stable(
        self,
        session_id: str,
        timeout_ms: int,
    ) -> ShortTermSessionMemory | None:
        memory = super().wait_until_stable(session_id, timeout_ms)
        if memory is not None:
            self._memory_path(session_id).write_text(
                json.dumps(memory.to_dict(), indent=2, sort_keys=True),
                encoding="utf-8",
            )
        return memory

    def _memory_path(self, session_id: str) -> Path:
        return self._root / f"{session_id}.short-term.json"

    def _load_existing(self) -> None:
        for path in sorted(self._root.glob("*.short-term.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            memory = ShortTermSessionMemory.from_dict(data)
            self._records[memory.session_id] = memory
