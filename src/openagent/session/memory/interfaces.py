"""Memory store interfaces."""

from __future__ import annotations

import builtins
from typing import Protocol

from openagent.session.memory.models import (
    MemoryConsolidationJob,
    MemoryConsolidationResult,
    MemoryRecallHandle,
    MemoryRecallResult,
    MemoryRecord,
)
from openagent.session.models import SessionMessage


class DurableMemoryStore(Protocol):
    def put(self, record: MemoryRecord) -> str:
        """Persist a durable memory record and return its identifier."""

    def update_memory(self, memory_id: str, patch: dict[str, object]) -> MemoryRecord:
        """Patch an existing memory record."""

    def delete(self, memory_id: str) -> bool:
        """Delete a durable memory record."""

    def list(self, selector: dict[str, object] | None = None) -> list[MemoryRecord]:
        """List durable memory records matching the selector."""

    def read(self, memory_refs: builtins.list[str]) -> builtins.list[MemoryRecord]:
        """Read durable memory records by identifier."""


class DurableMemoryExtractor(Protocol):
    def extract(
        self,
        transcript_slice: builtins.list[SessionMessage],
        existing_memory_context: builtins.list[MemoryRecord] | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
    ) -> builtins.list[MemoryRecord]:
        """Extract durable memory candidates from a transcript slice."""


class MemoryRecallEngine(Protocol):
    def prefetch(self, query: str, runtime_context: dict[str, object]) -> MemoryRecallHandle:
        """Prepare a bounded durable memory recall."""

    def collect(self, recall_handle: MemoryRecallHandle) -> MemoryRecallResult:
        """Collect memory attachments from a recall handle."""

    def dedupe(
        self,
        memory_attachments: builtins.list[MemoryRecord],
        already_loaded: builtins.list[str],
    ) -> builtins.list[MemoryRecord]:
        """Deduplicate recalled records against already-loaded memory identifiers."""


class MemoryConsolidator(Protocol):
    def schedule(
        self,
        session_id: str,
        transcript_slice: builtins.list[SessionMessage],
        agent_id: str | None = None,
    ) -> MemoryConsolidationJob:
        """Schedule a durable memory consolidation job."""

    def run(self, consolidation_job: MemoryConsolidationJob) -> MemoryConsolidationResult:
        """Run a previously scheduled durable consolidation job."""


class MemoryStore(
    DurableMemoryStore,
    DurableMemoryExtractor,
    MemoryRecallEngine,
    MemoryConsolidator,
    Protocol,
):
    def upsert_memory(self, record: MemoryRecord) -> MemoryRecord:
        """Persist or update a durable memory record."""

    def recall(
        self,
        session_id: str,
        query: str,
        limit: int = 5,
        agent_id: str | None = None,
    ) -> MemoryRecallResult:
        """Recall durable memories relevant to the current turn."""

    def consolidate(
        self,
        session_id: str,
        transcript_slice: builtins.list[SessionMessage],
        agent_id: str | None = None,
    ) -> MemoryConsolidationResult:
        """Extract or merge durable memory from a transcript slice."""
