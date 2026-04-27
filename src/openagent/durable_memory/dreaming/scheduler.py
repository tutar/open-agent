"""Runtime-local dreaming scheduling helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from openagent.durable_memory.dreaming.models import DreamingConfig


@dataclass(slots=True)
class DreamingScheduler:
    config: DreamingConfig
    last_run_at: datetime | None = None

    def should_run(self) -> bool:
        if not self.config.enabled:
            return False
        if self.last_run_at is None:
            return True
        elapsed = (datetime.now(UTC) - self.last_run_at).total_seconds()
        return elapsed >= self.config.min_interval_seconds

    def mark_scheduled(self) -> None:
        self.last_run_at = datetime.now(UTC)
