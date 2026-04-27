"""Short-term memory store baselines."""

from __future__ import annotations

import json
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from threading import Lock

from openagent.session.models import SessionMessage
from openagent.session.short_term_memory.models import (
    ShortTermMemoryUpdateResult,
    ShortTermSessionMemory,
)


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

