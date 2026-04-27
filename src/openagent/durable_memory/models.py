"""Durable memory models."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import cast

from openagent.object_model import JsonObject, SerializableModel


class MemoryOverlay(StrEnum):
    USER = "user"
    PROJECT = "project"
    TEAM = "team"
    AGENT = "agent"
    LOCAL = "local"


class MemoryPayloadType(StrEnum):
    USER = "user"
    FEEDBACK = "feedback"
    PROJECT = "project"
    REFERENCE = "reference"
    NOTE = "note"


class DurableWritePath(StrEnum):
    DIRECT_WRITE = "direct_write"
    EXTRACT = "extract"
    DREAM = "dream"


class AutoMemoryIndexMode(StrEnum):
    RESIDENT = "resident"
    DISABLED = "disabled"


class AutoMemoryWriteMode(StrEnum):
    DIRECT = "direct"
    EXTRACT = "extract"
    APPEND_FIRST = "append_first"


class AutoMemoryRecallPolicy(StrEnum):
    BOUNDED = "bounded"


@dataclass(slots=True)
class AutoMemoryRuntimeConfig(SerializableModel):
    enabled: bool = True
    memory_scope: MemoryOverlay = MemoryOverlay.PROJECT
    memory_root: str | None = None
    index_mode: AutoMemoryIndexMode = AutoMemoryIndexMode.RESIDENT
    write_mode: AutoMemoryWriteMode = AutoMemoryWriteMode.EXTRACT
    recall_policy: AutoMemoryRecallPolicy = AutoMemoryRecallPolicy.BOUNDED
    max_results: int = 5
    max_total_bytes: int = 16_000

    @classmethod
    def from_dict(cls, data: JsonObject) -> AutoMemoryRuntimeConfig:
        raw_max_results = data.get("max_results", 5)
        raw_max_total_bytes = data.get("max_total_bytes", 16_000)
        return cls(
            enabled=bool(data.get("enabled", True)),
            memory_scope=MemoryOverlay(str(data.get("memory_scope", MemoryOverlay.PROJECT))),
            memory_root=cast(str | None, data.get("memory_root")),
            index_mode=AutoMemoryIndexMode(
                str(data.get("index_mode", AutoMemoryIndexMode.RESIDENT))
            ),
            write_mode=AutoMemoryWriteMode(
                str(data.get("write_mode", AutoMemoryWriteMode.EXTRACT))
            ),
            recall_policy=AutoMemoryRecallPolicy(
                str(data.get("recall_policy", AutoMemoryRecallPolicy.BOUNDED))
            ),
            max_results=(
                int(raw_max_results)
                if isinstance(raw_max_results, int | float | str)
                else 5
            ),
            max_total_bytes=(
                int(raw_max_total_bytes)
                if isinstance(raw_max_total_bytes, int | float | str)
                else 16_000
            ),
        )


@dataclass(slots=True)
class DurableMemoryEntrypointIndex(SerializableModel):
    entrypoints: list[str] = field(default_factory=list)
    pointers: list[str] = field(default_factory=list)
    updated_at: str | None = None


@dataclass(slots=True)
class DurableMemoryManifestEntry(SerializableModel):
    memory_ref: str
    title: str | None = None
    description: str | None = None
    type: MemoryPayloadType | str | None = None
    scope: MemoryOverlay | str | None = None
    mtime: str | None = None
    freshness: str | None = None

    @classmethod
    def from_record(cls, record: MemoryRecord) -> DurableMemoryManifestEntry:
        return cls(
            memory_ref=record.memory_id,
            title=record.title,
            description=record.summary,
            type=record.type,
            scope=record.scope,
            mtime=record.updated_at,
            freshness=record.freshness,
        )


@dataclass(slots=True)
class DurableMemoryRecallRequest(SerializableModel):
    session_ref: str
    query: str
    scope_selector: list[MemoryOverlay | str] = field(default_factory=list)
    already_surfaced_refs: list[str] = field(default_factory=list)
    recent_tools: list[str] = field(default_factory=list)
    max_results: int | None = None
    max_total_bytes: int | None = None
    agent_id: str | None = None


@dataclass(slots=True)
class MemoryRecord(SerializableModel):
    memory_id: str
    scope: MemoryOverlay
    type: MemoryPayloadType | str
    title: str
    content: str
    summary: str
    source: str
    created_at: str
    updated_at: str
    freshness: str = "current"
    session_id: str | None = None
    agent_id: str | None = None
    metadata: JsonObject = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: JsonObject) -> MemoryRecord:
        raw_type = str(data["type"])
        raw_scope = str(data["scope"])
        try:
            payload_type: MemoryPayloadType | str = MemoryPayloadType(raw_type)
        except ValueError:
            payload_type = raw_type
        return cls(
            memory_id=str(data["memory_id"]),
            scope=MemoryOverlay(raw_scope),
            type=payload_type,
            title=str(data["title"]),
            content=str(data["content"]),
            summary=str(data["summary"]),
            source=str(data["source"]),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            freshness=str(data.get("freshness", "current")),
            session_id=cast(str | None, data.get("session_id")),
            agent_id=cast(str | None, data.get("agent_id")),
            metadata=cast(JsonObject, data.get("metadata", {})),
        )


@dataclass(slots=True)
class MemoryRecallResult(SerializableModel):
    query: str
    entrypoint_index: DurableMemoryEntrypointIndex | None = None
    manifest_entries: list[DurableMemoryManifestEntry] = field(default_factory=list)
    recalled: list[MemoryRecord] = field(default_factory=list)


@dataclass(slots=True)
class MemoryConsolidationResult(SerializableModel):
    session_id: str
    write_path: DurableWritePath
    new_records: list[MemoryRecord] = field(default_factory=list)
    updated_records: list[MemoryRecord] = field(default_factory=list)
    skipped_refs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MemoryConsolidationJob(SerializableModel):
    job_id: str
    session_id: str
    transcript_size: int
    write_path: DurableWritePath = DurableWritePath.EXTRACT


@dataclass(slots=True)
class MemoryRecallHandle(SerializableModel):
    handle_id: str
    query: str
    candidate_ids: list[str] = field(default_factory=list)
    entrypoint_index: DurableMemoryEntrypointIndex | None = None
    manifest_entries: list[DurableMemoryManifestEntry] = field(default_factory=list)


@dataclass(slots=True)
class DirectMemoryWriteRequest(SerializableModel):
    record: MemoryRecord


@dataclass(slots=True)
class DirectMemoryWriteResult(SerializableModel):
    record: MemoryRecord
    write_path: DurableWritePath = DurableWritePath.DIRECT_WRITE


@dataclass(slots=True)
class MemoryExtractionRequest(SerializableModel):
    session_id: str
    transcript_slice: list[JsonObject] = field(default_factory=list)
    agent_id: str | None = None

    @classmethod
    def from_session_messages(
        cls,
        session_id: str,
        transcript_slice: Sequence[object],
        agent_id: str | None = None,
    ) -> MemoryExtractionRequest:
        serialized = cast(
            list[JsonObject],
            [
                message.to_dict() if hasattr(message, "to_dict") else cast(JsonObject, message)
                for message in transcript_slice
            ],
        )
        return cls(session_id=session_id, transcript_slice=serialized, agent_id=agent_id)


@dataclass(slots=True)
class MemoryExtractionResult(SerializableModel):
    session_id: str
    extracted: list[MemoryRecord] = field(default_factory=list)
    skipped_refs: list[str] = field(default_factory=list)
    write_path: DurableWritePath = DurableWritePath.EXTRACT


@dataclass(slots=True)
class DreamConsolidationRequest(SerializableModel):
    session_id: str
    transcript_slice: list[JsonObject] = field(default_factory=list)
    agent_id: str | None = None
    force_failure: bool = False

    @classmethod
    def from_session_messages(
        cls,
        session_id: str,
        transcript_slice: Sequence[object],
        agent_id: str | None = None,
        force_failure: bool = False,
    ) -> DreamConsolidationRequest:
        serialized = cast(
            list[JsonObject],
            [
                message.to_dict() if hasattr(message, "to_dict") else cast(JsonObject, message)
                for message in transcript_slice
            ],
        )
        return cls(
            session_id=session_id,
            transcript_slice=serialized,
            agent_id=agent_id,
            force_failure=force_failure,
        )


@dataclass(slots=True)
class DreamConsolidationResult(SerializableModel):
    session_id: str
    consolidated: list[MemoryRecord] = field(default_factory=list)
    skipped_refs: list[str] = field(default_factory=list)
    write_path: DurableWritePath = DurableWritePath.DREAM


def durable_memory_content_size(record: MemoryRecord) -> int:
    """Estimate payload size for bounded recall."""

    return len(record.title.encode("utf-8")) + len(record.content.encode("utf-8"))


def is_durable_taxonomy(value: str) -> bool:
    """Return whether a type tag belongs to the stable durable-memory taxonomy."""

    try:
        MemoryPayloadType(value)
    except ValueError:
        return False
    return True
