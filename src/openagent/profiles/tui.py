"""TUI-first host profile assembly placeholder."""

from __future__ import annotations

from dataclasses import dataclass

from openagent.harness import ModelAdapter, SimpleHarness
from openagent.orchestration import InMemoryTaskManager
from openagent.session import InMemorySessionStore
from openagent.tools import SimpleToolExecutor, StaticToolRegistry, ToolDefinition


@dataclass(slots=True)
class TuiProfile:
    """Assembly placeholder for a local single-process host profile.

    Profiles represent deployment and wiring choices, not new domain modules.
    """

    name: str = "tui"

    def create_runtime(
        self,
        model: ModelAdapter,
        tools: list[ToolDefinition] | None = None,
    ) -> SimpleHarness:
        registry = StaticToolRegistry(tools or [])
        return SimpleHarness(
            model=model,
            sessions=InMemorySessionStore(),
            tools=registry,
            executor=SimpleToolExecutor(registry),
        )

    def create_task_manager(self) -> InMemoryTaskManager:
        return InMemoryTaskManager()
