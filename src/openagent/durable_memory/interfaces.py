"""Durable memory interfaces."""

from __future__ import annotations

import builtins
from typing import Protocol

from openagent.durable_memory.models import (
    AutoMemoryRuntimeConfig,
    DirectMemoryWriteRequest,
    DirectMemoryWriteResult,
    DreamConsolidationRequest,
    DreamConsolidationResult,
    DurableMemoryRecallRequest,
    MemoryConsolidationJob,
    MemoryConsolidationResult,
    MemoryExtractionRequest,
    MemoryExtractionResult,
    MemoryRecallHandle,
    MemoryRecallResult,
    MemoryRecord,
)
from openagent.session.models import SessionMessage


class DurableMemoryStore(Protocol):
    def put(self, record: MemoryRecord) -> str:
        """Persist a durable memory record and return its identifier."""

    def update_memory(self, memory_id: str, patch: dict[str, object]) -> MemoryRecord:
        """Patch an existing durable memory record."""

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

    def extract_memories(self, request: MemoryExtractionRequest) -> MemoryExtractionResult:
        """Run the turn-end extraction path without mutating transcript state."""


class MemoryRecallEngine(Protocol):
    def prefetch(self, query: str, runtime_context: dict[str, object]) -> MemoryRecallHandle:
        """Prepare a bounded durable memory recall."""

    def prefetch_request(self, request: DurableMemoryRecallRequest) -> MemoryRecallHandle:
        """Prepare layered durable recall from an explicit recall request."""

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
        write_path: object | None = None,
        dreaming_config: object | None = None,
    ) -> MemoryConsolidationJob:
        """Schedule a durable memory extraction/consolidation job."""

    def run(self, consolidation_job: MemoryConsolidationJob) -> MemoryConsolidationResult:
        """Run a previously scheduled durable consolidation job."""

    def dream(self, request: DreamConsolidationRequest) -> DreamConsolidationResult:
        """Run dream-style consolidation as a separate write path."""


class MemoryStore(
    DurableMemoryStore,
    DurableMemoryExtractor,
    MemoryRecallEngine,
    MemoryConsolidator,
    Protocol,
):
    runtime_config: AutoMemoryRuntimeConfig

    def is_enabled(self) -> bool:
        """Return whether the auto-memory runtime is enabled."""

    def upsert_memory(self, record: MemoryRecord) -> MemoryRecord:
        """Persist or update a durable memory record."""

    def direct_write(self, request: DirectMemoryWriteRequest) -> DirectMemoryWriteResult:
        """Persist a memory record through the foreground direct-write path."""

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
