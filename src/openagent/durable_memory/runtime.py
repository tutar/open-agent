"""Auto-memory runtime helpers for the local durable-memory baseline."""

from __future__ import annotations

from dataclasses import dataclass

from openagent.durable_memory.models import AutoMemoryRuntimeConfig, DurableWritePath


@dataclass(slots=True)
class AutoMemoryRuntime:
    """Evaluate durable-memory runtime gates for local execution."""

    config: AutoMemoryRuntimeConfig

    def is_enabled(self) -> bool:
        return self.config.enabled

    def allows_recall(self) -> bool:
        return self.config.enabled

    def allows_direct_write(self) -> bool:
        return self.config.enabled

    def allows_extract(self) -> bool:
        return self.config.enabled

    def allows_dream(self) -> bool:
        return self.config.enabled

    def allows_write_path(self, write_path: DurableWritePath) -> bool:
        if write_path is DurableWritePath.DIRECT_WRITE:
            return self.allows_direct_write()
        if write_path is DurableWritePath.EXTRACT:
            return self.allows_extract()
        return self.allows_dream()

