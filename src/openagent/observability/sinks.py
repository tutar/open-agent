"""Observability sink implementations."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import TextIO

from openagent.object_model import JsonValue
from openagent.observability.interfaces import ObservabilitySink
from openagent.observability.models import ExternalObservabilityEvent


class NoOpObservabilitySink:
    """Discard all observability events."""

    def emit(self, event: ExternalObservabilityEvent) -> None:
        del event


@dataclass(slots=True)
class CompositeObservabilitySink:
    """Fan observability events out to multiple sinks."""

    sinks: list[ObservabilitySink] = field(default_factory=list)

    def emit(self, event: ExternalObservabilityEvent) -> None:
        for sink in self.sinks:
            sink.emit(event)


@dataclass(slots=True)
class InMemoryObservabilitySink:
    """Collect observability events for tests and local debugging."""

    events: list[ExternalObservabilityEvent] = field(default_factory=list)

    def emit(self, event: ExternalObservabilityEvent) -> None:
        self.events.append(event)

    def list_by_kind(self, kind: str) -> list[ExternalObservabilityEvent]:
        return [event for event in self.events if event.kind == kind]


@dataclass(slots=True)
class StdoutObservabilitySink:
    """Write structured observability events to stdout."""

    stream: TextIO = field(default_factory=lambda: sys.stdout)
    max_string_chars: int = 160
    max_items: int = 6

    def emit(self, event: ExternalObservabilityEvent) -> None:
        summarized = {
            "kind": event.kind,
            "timestamp": event.timestamp,
            "payload": self._summarize_value(event.payload),
        }
        self.stream.write(json.dumps(summarized, ensure_ascii=False) + "\n")
        self.stream.flush()

    def _summarize_value(self, value: JsonValue) -> JsonValue:
        if isinstance(value, str):
            if len(value) <= self.max_string_chars:
                return value
            return value[: self.max_string_chars] + "...[truncated]"
        if isinstance(value, list):
            summarized_items = [self._summarize_value(item) for item in value[: self.max_items]]
            if len(value) > self.max_items:
                summarized_items.append(f"...[{len(value) - self.max_items} more items]")
            return summarized_items
        if isinstance(value, dict):
            items = list(value.items())
            summarized: dict[str, JsonValue] = {}
            for key, item in items[: self.max_items]:
                summarized[key] = self._summarize_value(item)
            if len(items) > self.max_items:
                summarized["_truncated_items"] = len(items) - self.max_items
            return summarized
        return value


def create_development_sink(stdout_enabled: bool | None = None) -> ObservabilitySink:
    """Return the default sink used for local development profiles."""

    if stdout_enabled is None:
        if os.getenv("PYTEST_CURRENT_TEST") is not None:
            stdout_enabled = False
        else:
            stdout_enabled = os.getenv("OPENAGENT_OBSERVABILITY_STDOUT", "true").lower() != "false"
    if stdout_enabled:
        return StdoutObservabilitySink()
    return NoOpObservabilitySink()
