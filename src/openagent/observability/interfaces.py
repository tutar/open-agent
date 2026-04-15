"""Observability interfaces."""

from __future__ import annotations

from typing import Protocol

from openagent.observability.models import ExternalObservabilityEvent


class ObservabilitySink(Protocol):
    """Receive normalized observability events."""

    def emit(self, event: ExternalObservabilityEvent) -> None:
        """Emit a normalized observability event."""
