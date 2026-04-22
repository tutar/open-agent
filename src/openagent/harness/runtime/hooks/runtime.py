"""Minimal runtime hook plane for lifecycle extension points."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from openagent.object_model import JsonObject


@dataclass(slots=True)
class HookResult:
    outcome: str = "success"
    message: str | None = None
    blocking_error: str | None = None
    system_message: str | None = None
    additional_context: JsonObject | None = None
    metadata: JsonObject = field(default_factory=dict)


class Hook(Protocol):
    def matches(self, *, scope: str, event: str) -> bool:
        """Return whether the hook should run for the scope/event pair."""

    def run(self, *, scope: str, event: str, payload: JsonObject) -> HookResult:
        """Execute the hook and return a structured result."""


@dataclass(slots=True)
class HookRegistry:
    hooks: list[Hook] = field(default_factory=list)

    def register_hook(self, hook: Hook) -> None:
        self.hooks.append(hook)

    def resolve_matching_hooks(self, *, scope: str, event: str) -> list[Hook]:
        return [hook for hook in self.hooks if hook.matches(scope=scope, event=event)]


@dataclass(slots=True)
class HookRuntime:
    registry: HookRegistry = field(default_factory=HookRegistry)

    def execute_hooks(self, *, scope: str, event: str, payload: JsonObject) -> list[HookResult]:
        results: list[HookResult] = []
        for hook in self.registry.resolve_matching_hooks(scope=scope, event=event):
            results.append(hook.run(scope=scope, event=event, payload=payload))
        return results
