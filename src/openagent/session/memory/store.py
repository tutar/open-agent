"""In-memory and file-backed durable memory baselines."""

from __future__ import annotations

import builtins
import json
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from openagent.object_model import JsonValue
from openagent.session.memory.models import (
    MemoryConsolidationJob,
    MemoryConsolidationResult,
    MemoryRecallHandle,
    MemoryRecallResult,
    MemoryRecord,
    MemoryScope,
)
from openagent.session.models import SessionMessage


class InMemoryMemoryStore:
    """Persist durable memory records in memory for tests and local runtime use."""

    def __init__(self) -> None:
        self._records: dict[str, MemoryRecord] = {}
        self._counter = 0
        self._recall_handles: dict[str, MemoryRecallHandle] = {}
        self._jobs: dict[str, tuple[str, list[SessionMessage], str]] = {}
        self._pending_jobs: dict[str, Future[MemoryConsolidationResult]] = {}
        self._executor = ThreadPoolExecutor(max_workers=2)

    def put(self, record: MemoryRecord) -> str:
        self.upsert_memory(record)
        return record.memory_id

    def update_memory(self, memory_id: str, patch: dict[str, object]) -> MemoryRecord:
        record = self._records[memory_id]
        updated_data = record.to_dict()
        updated_data.update({str(key): cast(JsonValue, value) for key, value in patch.items()})
        updated = MemoryRecord.from_dict(updated_data)
        updated.updated_at = datetime.now(UTC).isoformat()
        self._records[memory_id] = updated
        return updated

    def delete(self, memory_id: str) -> bool:
        return self._records.pop(memory_id, None) is not None

    def list(self, selector: dict[str, object] | None = None) -> list[MemoryRecord]:
        if selector is None:
            return list(self._records.values())
        scope = selector.get("scope")
        source = selector.get("source")
        records = list(self._records.values())
        if isinstance(scope, str):
            records = [record for record in records if record.scope.value == scope]
        if isinstance(source, str):
            records = [record for record in records if record.source == source]
        return records

    def read(self, memory_refs: builtins.list[str]) -> builtins.list[MemoryRecord]:
        return [self._records[memory_id] for memory_id in memory_refs if memory_id in self._records]

    def upsert_memory(self, record: MemoryRecord) -> MemoryRecord:
        self._records[record.memory_id] = record
        return record

    def extract(
        self,
        transcript_slice: builtins.list[SessionMessage],
        existing_memory_context: builtins.list[MemoryRecord] | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
    ) -> builtins.list[MemoryRecord]:
        if not transcript_slice:
            return []
        existing_titles = {record.title for record in existing_memory_context or []}
        self._counter += 1
        last_message = transcript_slice[-1]
        title = last_message.content[:80]
        if title in existing_titles:
            return []
        return [
            MemoryRecord(
                memory_id=f"memory_{self._counter}",
                scope=self._infer_scope(transcript_slice),
                type="note",
                title=title or f"memory_{self._counter}",
                content="\n".join(
                    f"{message.role}: {message.content}" for message in transcript_slice
                ),
                summary=last_message.content[:120],
                source=f"session:{session_id or 'unknown'}",
                created_at=datetime.now(UTC).isoformat(),
                updated_at=datetime.now(UTC).isoformat(),
                freshness="fresh",
                session_id=session_id,
                agent_id=agent_id,
                metadata={"message_count": len(transcript_slice)},
            )
        ]

    def prefetch(self, query: str, runtime_context: dict[str, object]) -> MemoryRecallHandle:
        session_id = str(runtime_context.get("session_id", ""))
        agent_id = runtime_context.get("agent_id")
        scopes = runtime_context.get("scopes")
        selected = self._select_records(
            query,
            session_id=session_id,
            agent_id=str(agent_id) if isinstance(agent_id, str) else None,
            scopes=scopes,
        )
        handle = MemoryRecallHandle(
            handle_id=f"recall_{len(self._recall_handles) + 1}",
            query=query,
            candidate_ids=[record.memory_id for record in selected],
        )
        self._recall_handles[handle.handle_id] = handle
        return handle

    def collect(self, recall_handle: MemoryRecallHandle) -> MemoryRecallResult:
        return MemoryRecallResult(
            query=recall_handle.query,
            recalled=self.read(recall_handle.candidate_ids),
        )

    def dedupe(
        self,
        memory_attachments: builtins.list[MemoryRecord],
        already_loaded: builtins.list[str],
    ) -> builtins.list[MemoryRecord]:
        loaded = set(already_loaded)
        return [record for record in memory_attachments if record.memory_id not in loaded]

    def schedule(
        self,
        session_id: str,
        transcript_slice: builtins.list[SessionMessage],
        agent_id: str | None = None,
    ) -> MemoryConsolidationJob:
        job = MemoryConsolidationJob(
            job_id=f"memory_job_{len(self._jobs) + 1}",
            session_id=session_id,
            transcript_size=len(transcript_slice),
        )
        snapshot = [
            SessionMessage(
                role=item.role,
                content=item.content,
                metadata=item.metadata,
            )
            for item in transcript_slice
        ]
        self._jobs[job.job_id] = (session_id, snapshot, agent_id or "")
        self._pending_jobs[job.job_id] = self._executor.submit(self.run, job)
        return job

    def wait_for_job(
        self,
        job_id: str,
        timeout_seconds: float | None = None,
    ) -> MemoryConsolidationResult:
        future = self._pending_jobs[job_id]
        return future.result(timeout=timeout_seconds)

    def recall(
        self,
        session_id: str,
        query: str,
        limit: int = 5,
        agent_id: str | None = None,
    ) -> MemoryRecallResult:
        handle = self.prefetch(query, {"session_id": session_id, "agent_id": agent_id})
        result = self.collect(handle)
        return MemoryRecallResult(query=query, recalled=result.recalled[:limit])

    def consolidate(
        self,
        session_id: str,
        transcript_slice: builtins.list[SessionMessage],
        agent_id: str | None = None,
    ) -> MemoryConsolidationResult:
        extracted = self.extract(
            transcript_slice,
            existing_memory_context=list(self._records.values()),
            session_id=session_id,
            agent_id=agent_id,
        )
        if not extracted:
            return MemoryConsolidationResult(session_id=session_id)
        for record in extracted:
            self._records[record.memory_id] = record
        return MemoryConsolidationResult(session_id=session_id, new_records=extracted)

    def run(self, consolidation_job: MemoryConsolidationJob) -> MemoryConsolidationResult:
        session_id, transcript_slice, agent_id = self._jobs[consolidation_job.job_id]
        result = self.consolidate(session_id, transcript_slice, agent_id=agent_id or None)
        self._pending_jobs.pop(consolidation_job.job_id, None)
        return result

    def _select_records(
        self,
        query: str,
        session_id: str,
        agent_id: str | None = None,
        scopes: object = None,
    ) -> builtins.list[MemoryRecord]:
        tokens = {token for token in query.lower().split() if token}
        selected_scopes = (
            {str(scope) for scope in scopes if isinstance(scope, str)}
            if isinstance(scopes, list)
            else None
        )
        scored: list[tuple[int, MemoryRecord]] = []
        for record in self._records.values():
            if selected_scopes is not None and record.scope.value not in selected_scopes:
                continue
            bonus = 1 if record.session_id is not None and record.session_id == session_id else 0
            if (
                record.scope is MemoryScope.AGENT
                and agent_id is not None
                and record.agent_id == agent_id
            ):
                bonus += 2
            haystack = f"{record.title} {record.content} {record.summary}".lower()
            score = sum(1 for token in tokens if token in haystack) + bonus
            if score > 0 or not tokens:
                scored.append((score, record))
        return [record for _, record in sorted(scored, key=lambda item: item[0], reverse=True)]

    def _infer_scope(self, transcript_slice: builtins.list[SessionMessage]) -> MemoryScope:
        joined = " ".join(message.content.lower() for message in transcript_slice)
        if "preference" in joined or "i like" in joined:
            return MemoryScope.USER
        if "agent" in joined:
            return MemoryScope.AGENT
        if "localhost" in joined or "machine" in joined or "env" in joined:
            return MemoryScope.LOCAL
        return MemoryScope.PROJECT


class FileMemoryStore(InMemoryMemoryStore):
    """Persist durable memory records to disk for restart-safe recall."""

    def __init__(self, root: str | Path) -> None:
        super().__init__()
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._load_existing()

    def upsert_memory(self, record: MemoryRecord) -> MemoryRecord:
        stored = super().upsert_memory(record)
        self._write_record(stored)
        return stored

    def consolidate(
        self,
        session_id: str,
        transcript_slice: builtins.list[SessionMessage],
        agent_id: str | None = None,
    ) -> MemoryConsolidationResult:
        result = super().consolidate(session_id, transcript_slice, agent_id=agent_id)
        records_to_write = result.new_records or list(self._records.values())
        for record in records_to_write:
            self._write_record(record)
        return result

    def _record_path(self, memory_id: str) -> Path:
        return self._root / f"{memory_id}.json"

    def _write_record(self, record: MemoryRecord) -> None:
        self._record_path(record.memory_id).write_text(
            json.dumps(record.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def update_memory(self, memory_id: str, patch: dict[str, object]) -> MemoryRecord:
        updated = super().update_memory(memory_id, patch)
        self._write_record(updated)
        return updated

    def delete(self, memory_id: str) -> bool:
        deleted = super().delete(memory_id)
        if deleted:
            path = self._record_path(memory_id)
            if path.exists():
                path.unlink()
        return deleted

    def _load_existing(self) -> None:
        max_counter = 0
        for path in sorted(self._root.glob("memory_*.json")):
            raw_text = path.read_text(encoding="utf-8").strip()
            if not raw_text:
                continue
            record = MemoryRecord.from_dict(json.loads(raw_text))
            self._records[record.memory_id] = record
            try:
                max_counter = max(max_counter, int(record.memory_id.removeprefix("memory_")))
            except ValueError:
                continue
        self._counter = max_counter
