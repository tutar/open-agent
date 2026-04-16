"""Durable memory models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from openagent.object_model import JsonObject, SerializableModel


class MemoryScope(StrEnum):
    USER = "user"
    PROJECT = "project"
    AGENT = "agent"
    LOCAL = "local"


@dataclass(slots=True)
class MemoryRecord(SerializableModel):
    memory_id: str
    scope: MemoryScope
    type: str
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


@dataclass(slots=True)
class MemoryRecallResult(SerializableModel):
    query: str
    recalled: list[MemoryRecord] = field(default_factory=list)


@dataclass(slots=True)
class MemoryConsolidationResult(SerializableModel):
    session_id: str
    new_records: list[MemoryRecord] = field(default_factory=list)
    updated_records: list[MemoryRecord] = field(default_factory=list)


@dataclass(slots=True)
class MemoryConsolidationJob(SerializableModel):
    job_id: str
    session_id: str
    transcript_size: int


@dataclass(slots=True)
class MemoryRecallHandle(SerializableModel):
    handle_id: str
    query: str
    candidate_ids: list[str] = field(default_factory=list)
