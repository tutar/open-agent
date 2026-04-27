"""Post-turn processing executed after a turn reaches a stable stop boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from openagent.session import SessionRecord


class MemoryMaintenanceRuntime(Protocol):
    def schedule_memory_maintenance(self, session: SessionRecord) -> None:
        """Schedule runtime-local memory maintenance for a completed turn."""

    def stabilize_short_term_memory(self, session: SessionRecord) -> None:
        """Persist the latest short-term memory snapshot for the session."""


@dataclass(slots=True)
class PostTurnContext:
    session_handle: str
    session: SessionRecord
    runtime: MemoryMaintenanceRuntime


class PostTurnProcessor(Protocol):
    def should_run(self, context: PostTurnContext) -> bool:
        """Return whether this processor should run."""

    def run(self, context: PostTurnContext) -> None:
        """Perform post-turn processing."""


@dataclass(slots=True)
class PostTurnRegistry:
    processors: list[PostTurnProcessor] = field(default_factory=list)

    def register(self, processor: PostTurnProcessor) -> None:
        self.processors.append(processor)

    def execute_all(self, context: PostTurnContext) -> None:
        for processor in self.processors:
            if processor.should_run(context):
                processor.run(context)


class MemoryMaintenanceProcessor:
    """Persist session-following memory updates after a stable turn boundary."""

    def should_run(self, context: PostTurnContext) -> bool:
        runtime = context.runtime
        return bool(
            getattr(runtime, "short_term_memory_store", None) is not None
            or getattr(runtime, "memory_store", None) is not None
        )

    def run(self, context: PostTurnContext) -> None:
        runtime = context.runtime
        runtime.schedule_memory_maintenance(context.session)
        runtime.stabilize_short_term_memory(context.session)
