"""Short-term memory store interfaces."""

from __future__ import annotations

from typing import Any, Protocol

from openagent.session.short_term_memory.models import (
    ShortTermMemoryUpdateResult,
    ShortTermSessionMemory,
)


class ShortTermMemoryStore(Protocol):
    def load(self, session_id: str) -> ShortTermSessionMemory | None:
        """Load the current stable short-term memory snapshot."""

    def update(
        self,
        session_id: str,
        transcript_delta: list[Any],
        current_memory: ShortTermSessionMemory | None,
    ) -> ShortTermMemoryUpdateResult:
        """Schedule or perform a short-term memory update."""

    def get_coverage_boundary(self, session_id: str) -> int | None:
        """Return the transcript coverage boundary for the latest stable memory."""

    def wait_until_stable(
        self,
        session_id: str,
        timeout_ms: int,
    ) -> ShortTermSessionMemory | None:
        """Wait for the latest update to reach a stable version."""
