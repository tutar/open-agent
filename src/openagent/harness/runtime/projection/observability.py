"""Runtime-facing projection helpers over the shared observability layer."""

from __future__ import annotations

from dataclasses import dataclass

from openagent.object_model import JsonObject
from openagent.observability import (
    AgentObservability,
    ProgressUpdate,
    RuntimeMetric,
    SessionStateSignal,
)


@dataclass(slots=True)
class RuntimeObservabilityProjection:
    observability: AgentObservability

    def emit_metric(self, metric: RuntimeMetric) -> None:
        self.observability.emit_runtime_metric(metric)

    def emit_progress(self, progress: ProgressUpdate) -> None:
        self.observability.emit_progress(progress)

    def emit_session_state(
        self,
        *,
        session_id: str,
        state: str,
        reason: str | None = None,
        attributes: JsonObject | None = None,
    ) -> None:
        self.observability.emit_session_state(
            SessionStateSignal(
                session_id=session_id,
                state=state,
                reason=reason,
                attributes=dict(attributes or {}),
            )
        )
