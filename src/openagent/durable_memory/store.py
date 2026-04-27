"""In-memory and file-backed durable memory baselines."""

from __future__ import annotations

import builtins
import json
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import cast

from openagent.durable_memory.models import (
    AutoMemoryRuntimeConfig,
    DirectMemoryWriteRequest,
    DirectMemoryWriteResult,
    DreamConsolidationRequest,
    DreamConsolidationResult,
    DurableMemoryRecallRequest,
    DurableWritePath,
    MemoryConsolidationJob,
    MemoryConsolidationResult,
    MemoryExtractionRequest,
    MemoryExtractionResult,
    MemoryOverlay,
    MemoryPayloadType,
    MemoryRecallHandle,
    MemoryRecallResult,
    MemoryRecord,
)
from openagent.durable_memory.operations import (
    build_entrypoint_index,
    build_manifest_entries,
    select_recall_candidates,
)
from openagent.durable_memory.runtime import AutoMemoryRuntime
from openagent.object_model import JsonValue
from openagent.session.models import SessionMessage


class InMemoryMemoryStore:
    """Persist durable memory records in memory for tests and local runtime use."""

    def __init__(self, runtime_config: AutoMemoryRuntimeConfig | None = None) -> None:
        self.runtime_config = runtime_config or AutoMemoryRuntimeConfig()
        self.runtime = AutoMemoryRuntime(self.runtime_config)
        self._records: dict[str, MemoryRecord] = {}
        self._counter = 0
        self._recall_handles: dict[str, MemoryRecallHandle] = {}
        self._jobs: dict[str, tuple[str, list[SessionMessage], str]] = {}
        self._pending_jobs: dict[str, Future[MemoryConsolidationResult]] = {}
        self._executor = ThreadPoolExecutor(max_workers=2)

    def is_enabled(self) -> bool:
        return self.runtime.is_enabled()

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
        records = list(self._records.values())
        if selector is None:
            return records
        scope = selector.get("scope")
        payload_type = selector.get("type")
        source = selector.get("source")
        if isinstance(scope, str):
            records = [record for record in records if record.scope.value == scope]
        if isinstance(payload_type, str):
            records = [record for record in records if str(record.type) == payload_type]
        if isinstance(source, str):
            records = [record for record in records if record.source == source]
        return records

    def read(self, memory_refs: builtins.list[str]) -> builtins.list[MemoryRecord]:
        return [self._records[memory_id] for memory_id in memory_refs if memory_id in self._records]

    def upsert_memory(self, record: MemoryRecord) -> MemoryRecord:
        self._records[record.memory_id] = record
        return record

    def direct_write(self, request: DirectMemoryWriteRequest) -> DirectMemoryWriteResult:
        if not self.runtime.allows_direct_write():
            raise RuntimeError("auto-memory runtime is disabled")
        stored = self.upsert_memory(request.record)
        metadata = dict(stored.metadata)
        metadata["write_path"] = DurableWritePath.DIRECT_WRITE.value
        stored = self.update_memory(stored.memory_id, {"metadata": metadata})
        return DirectMemoryWriteResult(record=stored)

    def extract(
        self,
        transcript_slice: builtins.list[SessionMessage],
        existing_memory_context: builtins.list[MemoryRecord] | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
    ) -> builtins.list[MemoryRecord]:
        if not transcript_slice:
            return []
        joined = " ".join(message.content for message in transcript_slice).lower()
        if self._should_exclude_from_durable_memory(joined):
            return []
        self._counter += 1
        last_message = transcript_slice[-1]
        title = last_message.content[:80]
        return [
            MemoryRecord(
                memory_id=f"memory_{self._counter}",
                scope=self._infer_overlay(transcript_slice),
                type=self._infer_payload_type(transcript_slice),
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

    def extract_memories(self, request: MemoryExtractionRequest) -> MemoryExtractionResult:
        if not self.runtime.allows_extract():
            return MemoryExtractionResult(session_id=request.session_id)
        transcript = [SessionMessage.from_dict(item) for item in request.transcript_slice]
        extracted = self.extract(
            transcript,
            existing_memory_context=list(self._records.values()),
            session_id=request.session_id,
            agent_id=request.agent_id,
        )
        skipped_refs: list[str] = []
        for record in extracted:
            duplicate = self._find_duplicate(record)
            if duplicate is not None:
                skipped_refs.append(duplicate.memory_id)
                continue
            metadata = dict(record.metadata)
            metadata["write_path"] = DurableWritePath.EXTRACT.value
            updated_record = cast(
                JsonValue,
                {**record.to_dict(), "metadata": cast(JsonValue, metadata)},
            )
            self.upsert_memory(
                MemoryRecord.from_dict(cast(dict[str, JsonValue], updated_record))
            )
        stored_ids = {record.memory_id for record in self._records.values()}
        stored = [record for record in extracted if record.memory_id in stored_ids]
        return MemoryExtractionResult(
            session_id=request.session_id,
            extracted=stored,
            skipped_refs=skipped_refs,
        )

    def prefetch(self, query: str, runtime_context: dict[str, object]) -> MemoryRecallHandle:
        request = DurableMemoryRecallRequest(
            session_ref=str(runtime_context.get("session_id", "")),
            query=query,
            scope_selector=cast(list[str], runtime_context.get("scopes", [])),
            agent_id=cast(str | None, runtime_context.get("agent_id")),
            max_results=self.runtime_config.max_results,
            max_total_bytes=self.runtime_config.max_total_bytes,
        )
        return self.prefetch_request(request)

    def prefetch_request(self, request: DurableMemoryRecallRequest) -> MemoryRecallHandle:
        if not self.runtime.allows_recall():
            handle = MemoryRecallHandle(
                handle_id=f"recall_{len(self._recall_handles) + 1}",
                query=request.query,
            )
            self._recall_handles[handle.handle_id] = handle
            return handle
        records = list(self._records.values())
        candidate_records = select_recall_candidates(records, request)
        handle = MemoryRecallHandle(
            handle_id=f"recall_{len(self._recall_handles) + 1}",
            query=request.query,
            candidate_ids=[record.memory_id for record in candidate_records],
            entrypoint_index=build_entrypoint_index(records),
            manifest_entries=build_manifest_entries(candidate_records),
        )
        self._recall_handles[handle.handle_id] = handle
        return handle

    def collect(self, recall_handle: MemoryRecallHandle) -> MemoryRecallResult:
        return MemoryRecallResult(
            query=recall_handle.query,
            entrypoint_index=recall_handle.entrypoint_index,
            manifest_entries=list(recall_handle.manifest_entries),
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
            write_path=DurableWritePath.EXTRACT,
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
        request = DurableMemoryRecallRequest(
            session_ref=session_id,
            query=query,
            agent_id=agent_id,
            max_results=limit,
            max_total_bytes=self.runtime_config.max_total_bytes,
        )
        handle = self.prefetch_request(request)
        return self.collect(handle)

    def consolidate(
        self,
        session_id: str,
        transcript_slice: builtins.list[SessionMessage],
        agent_id: str | None = None,
    ) -> MemoryConsolidationResult:
        result = self.extract_memories(
            MemoryExtractionRequest.from_session_messages(
                session_id=session_id,
                transcript_slice=transcript_slice,
                agent_id=agent_id,
            )
        )
        return MemoryConsolidationResult(
            session_id=session_id,
            write_path=DurableWritePath.EXTRACT,
            new_records=result.extracted,
            skipped_refs=result.skipped_refs,
        )

    def dream(self, request: DreamConsolidationRequest) -> DreamConsolidationResult:
        if not self.runtime.allows_dream():
            return DreamConsolidationResult(session_id=request.session_id)
        snapshot = dict(self._records)
        if request.force_failure:
            raise RuntimeError("dream consolidation failed")
        transcript = [SessionMessage.from_dict(item) for item in request.transcript_slice]
        extracted = self.extract(
            transcript,
            existing_memory_context=list(snapshot.values()),
            session_id=request.session_id,
            agent_id=request.agent_id,
        )
        consolidated: list[MemoryRecord] = []
        skipped_refs: list[str] = []
        for record in extracted:
            duplicate = next(
                (
                    existing
                    for existing in snapshot.values()
                    if existing.title == record.title and existing.scope == record.scope
                ),
                None,
            )
            if duplicate is not None:
                merged_metadata = dict(duplicate.metadata)
                merged_metadata["write_path"] = DurableWritePath.DREAM.value
                updated = self.update_memory(
                    duplicate.memory_id,
                    {
                        "summary": record.summary,
                        "content": record.content,
                        "updated_at": datetime.now(UTC).isoformat(),
                        "metadata": merged_metadata,
                    },
                )
                consolidated.append(updated)
                skipped_refs.append(duplicate.memory_id)
                continue
            metadata = dict(record.metadata)
            metadata["write_path"] = DurableWritePath.DREAM.value
            dream_record = MemoryRecord.from_dict({**record.to_dict(), "metadata": metadata})
            self.upsert_memory(dream_record)
            consolidated.append(dream_record)
        return DreamConsolidationResult(
            session_id=request.session_id,
            consolidated=consolidated,
            skipped_refs=skipped_refs,
        )

    def run(self, consolidation_job: MemoryConsolidationJob) -> MemoryConsolidationResult:
        session_id, transcript_slice, agent_id = self._jobs[consolidation_job.job_id]
        if consolidation_job.write_path is DurableWritePath.DREAM:
            dream_result = self.dream(
                DreamConsolidationRequest.from_session_messages(
                    session_id=session_id,
                    transcript_slice=transcript_slice,
                    agent_id=agent_id or None,
                )
            )
            result = MemoryConsolidationResult(
                session_id=session_id,
                write_path=DurableWritePath.DREAM,
                updated_records=dream_result.consolidated,
                skipped_refs=dream_result.skipped_refs,
            )
        else:
            result = self.consolidate(session_id, transcript_slice, agent_id=agent_id or None)
        self._pending_jobs.pop(consolidation_job.job_id, None)
        return result

    def _find_duplicate(self, record: MemoryRecord) -> MemoryRecord | None:
        return next(
            (
                existing
                for existing in self._records.values()
                if existing.title == record.title
                and existing.scope == record.scope
                and str(existing.type) == str(record.type)
            ),
            None,
        )

    def _should_exclude_from_durable_memory(self, joined: str) -> bool:
        excluded_patterns = [
            "def ",
            "class ",
            "git commit",
            "task progress",
            "build log",
            "stack trace",
        ]
        return any(pattern in joined for pattern in excluded_patterns)

    def _infer_overlay(self, transcript_slice: builtins.list[SessionMessage]) -> MemoryOverlay:
        joined = " ".join(message.content.lower() for message in transcript_slice)
        if "team" in joined:
            return MemoryOverlay.TEAM
        if "preference" in joined or "i like" in joined:
            return MemoryOverlay.USER
        if "agent" in joined:
            return MemoryOverlay.AGENT
        if "localhost" in joined or "machine" in joined or "env" in joined:
            return MemoryOverlay.LOCAL
        return MemoryOverlay.PROJECT

    def _infer_payload_type(
        self,
        transcript_slice: builtins.list[SessionMessage],
    ) -> MemoryPayloadType:
        joined = " ".join(message.content.lower() for message in transcript_slice)
        if "prefer" in joined or "i like" in joined or "my favorite" in joined:
            return MemoryPayloadType.USER
        if "should" in joined or "always use" in joined or "workflow" in joined:
            return MemoryPayloadType.FEEDBACK
        if "http" in joined or "docs" in joined or "reference" in joined:
            return MemoryPayloadType.REFERENCE
        if "launch" in joined or "initiative" in joined or "project" in joined:
            return MemoryPayloadType.PROJECT
        return MemoryPayloadType.NOTE


class FileMemoryStore(InMemoryMemoryStore):
    """Persist durable memory records to disk for restart-safe recall."""

    def __init__(
        self,
        root: str | Path,
        runtime_config: AutoMemoryRuntimeConfig | None = None,
    ) -> None:
        super().__init__(runtime_config=runtime_config)
        self._root = Path(root)
        self._io_lock = Lock()
        self._root.mkdir(parents=True, exist_ok=True)
        if self.runtime_config.memory_root is None:
            self.runtime_config.memory_root = str(self._root)
        self._load_existing()

    def upsert_memory(self, record: MemoryRecord) -> MemoryRecord:
        stored = super().upsert_memory(record)
        self._write_record(stored)
        return stored

    def direct_write(self, request: DirectMemoryWriteRequest) -> DirectMemoryWriteResult:
        result = super().direct_write(request)
        self._write_record(result.record)
        return result

    def extract_memories(self, request: MemoryExtractionRequest) -> MemoryExtractionResult:
        result = super().extract_memories(request)
        for record in result.extracted:
            self._write_record(record)
        self._write_skipped_records(result.skipped_refs)
        return result

    def consolidate(
        self,
        session_id: str,
        transcript_slice: builtins.list[SessionMessage],
        agent_id: str | None = None,
    ) -> MemoryConsolidationResult:
        result = super().consolidate(session_id, transcript_slice, agent_id=agent_id)
        for record in result.new_records:
            self._write_record(record)
        for record in result.updated_records:
            self._write_record(record)
        self._write_skipped_records(result.skipped_refs)
        return result

    def dream(self, request: DreamConsolidationRequest) -> DreamConsolidationResult:
        result = super().dream(request)
        for record in result.consolidated:
            self._write_record(record)
        self._write_skipped_records(result.skipped_refs)
        return result

    def _record_path(self, memory_id: str) -> Path:
        return self._root / f"{memory_id}.json"

    def _write_record(self, record: MemoryRecord) -> None:
        payload = json.dumps(record.to_dict(), indent=2, sort_keys=True)
        path = self._record_path(record.memory_id)
        temp_path = path.with_suffix(".tmp")
        with self._io_lock:
            temp_path.write_text(payload, encoding="utf-8")
            temp_path.replace(path)

    def _write_skipped_records(self, skipped_refs: builtins.list[str]) -> None:
        for memory_id in skipped_refs:
            record = self._records.get(memory_id)
            if record is not None:
                self._write_record(record)

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
        for path in sorted(self._root.glob("*.json")):
            raw_text = path.read_text(encoding="utf-8")
            if not raw_text.strip():
                continue
            record = MemoryRecord.from_dict(json.loads(raw_text))
            self._records[record.memory_id] = record
