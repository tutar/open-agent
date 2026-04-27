"""Overlay helpers for durable-memory visibility and binding."""

from __future__ import annotations

from collections.abc import Iterable

from openagent.durable_memory.models import MemoryOverlay, MemoryRecord


def normalize_overlay_selector(
    overlays: Iterable[MemoryOverlay | str] | None,
) -> set[MemoryOverlay] | None:
    """Normalize an overlay selector into enum values."""

    if overlays is None:
        return None
    normalized = {MemoryOverlay(str(overlay)) for overlay in overlays}
    return normalized or None


def record_matches_overlay(
    record: MemoryRecord,
    overlays: set[MemoryOverlay] | None,
    *,
    session_id: str,
    agent_id: str | None = None,
) -> bool:
    """Return whether a memory record is visible in the current overlay view."""

    if overlays is not None and record.scope not in overlays:
        return False
    if record.scope is MemoryOverlay.AGENT and agent_id is not None and record.agent_id:
        return record.agent_id == agent_id
    if record.scope is MemoryOverlay.LOCAL and record.session_id is not None:
        return record.session_id == session_id
    return True


def overlay_family() -> tuple[MemoryOverlay, ...]:
    """Return the stable overlay family for the local durable-memory baseline."""

    return (
        MemoryOverlay.USER,
        MemoryOverlay.PROJECT,
        MemoryOverlay.TEAM,
        MemoryOverlay.AGENT,
        MemoryOverlay.LOCAL,
    )

