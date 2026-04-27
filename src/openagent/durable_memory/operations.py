"""Selection helpers for layered durable-memory recall."""

from __future__ import annotations

from collections.abc import Iterable

from openagent.durable_memory.models import (
    DurableMemoryEntrypointIndex,
    DurableMemoryManifestEntry,
    DurableMemoryRecallRequest,
    MemoryRecord,
    durable_memory_content_size,
)
from openagent.durable_memory.scopes import normalize_overlay_selector, record_matches_overlay


def build_entrypoint_index(records: Iterable[MemoryRecord]) -> DurableMemoryEntrypointIndex:
    """Build a small resident entrypoint/index view for current durable records."""

    entries: set[str] = set()
    pointers: set[str] = set()
    updated_at: str | None = None
    for record in records:
        entries.add(f"{record.scope.value}:{str(record.type)}")
        pointers.add(record.memory_id)
        updated_at = max(updated_at or record.updated_at, record.updated_at)
    return DurableMemoryEntrypointIndex(
        entrypoints=sorted(entries),
        pointers=sorted(pointers),
        updated_at=updated_at,
    )


def build_manifest_entries(records: Iterable[MemoryRecord]) -> list[DurableMemoryManifestEntry]:
    """Build bounded manifest/header entries from durable payloads."""

    return [DurableMemoryManifestEntry.from_record(record) for record in records]


def select_recall_candidates(
    records: Iterable[MemoryRecord],
    request: DurableMemoryRecallRequest,
) -> list[MemoryRecord]:
    """Select bounded durable-memory payloads for the current turn."""

    tokens = {token for token in request.query.lower().split() if token}
    overlays = normalize_overlay_selector(request.scope_selector)
    already_surfaced = set(request.already_surfaced_refs)
    max_results = request.max_results or 5
    max_total_bytes = request.max_total_bytes or 16_000
    scored: list[tuple[int, MemoryRecord]] = []
    for record in records:
        if record.memory_id in already_surfaced:
            continue
        if not record_matches_overlay(
            record,
            overlays,
            session_id=request.session_ref,
            agent_id=request.agent_id,
        ):
            continue
        haystack = f"{record.title} {record.content} {record.summary}".lower()
        score = sum(1 for token in tokens if token in haystack)
        if request.agent_id is not None and record.agent_id == request.agent_id:
            score += 2
        if record.session_id == request.session_ref:
            score += 1
        if score > 0 or not tokens:
            scored.append((score, record))
    ordered = [record for _, record in sorted(scored, key=lambda item: item[0], reverse=True)]
    bounded: list[MemoryRecord] = []
    total_bytes = 0
    for record in ordered:
        if len(bounded) >= max_results:
            break
        estimated = durable_memory_content_size(record)
        if bounded and total_bytes + estimated > max_total_bytes:
            break
        bounded.append(record)
        total_bytes += estimated
    return bounded

