"""Session interface definitions."""

from __future__ import annotations

from typing import Any, Protocol

from openagent.object_model import RuntimeEvent, SessionHarnessLease
from openagent.session.models import ResumeSnapshot, SessionCheckpoint, SessionCursor, WakeRequest


class SessionStore(Protocol):
    def append_event(self, event: RuntimeEvent) -> None:
        """Persist a runtime event."""

    def append_events(self, session_id: str, events: list[RuntimeEvent]) -> SessionCheckpoint:
        """Persist a batch of runtime events and return the next checkpoint."""

    def read_events(
        self,
        session_id: str,
        after: int = 0,
        cursor: SessionCursor | None = None,
    ) -> list[RuntimeEvent]:
        """Read events after the given checkpoint offset."""

    def load_session(self, session_id: str) -> Any:
        """Load a session snapshot or handle."""

    def save_session(self, session_id: str, state: Any) -> None:
        """Persist session state."""

    def save_session_state_only(self, session_id: str, state: Any) -> None:
        """Persist side-state without appending transcript or event history."""

    def get_checkpoint(self, session_id: str) -> SessionCheckpoint:
        """Return the current event-log checkpoint."""

    def mark_restored(self, session_id: str, cursor: SessionCursor | None = None) -> None:
        """Persist a restore marker for later recovery."""

    def get_resume_snapshot(self, wake_request: WakeRequest) -> ResumeSnapshot:
        """Build a runtime-facing resume snapshot from durable session state."""

    def acquire_lease(
        self,
        session_id: str,
        harness_instance_id: str,
        agent_id: str,
    ) -> SessionHarnessLease:
        """Acquire the single active harness lease for the session."""

    def release_lease(self, session_id: str, harness_instance_id: str) -> bool:
        """Release an active harness lease held by the given harness instance."""

    def get_active_lease(self, session_id: str) -> SessionHarnessLease | None:
        """Return the current active harness lease for the session, if any."""
