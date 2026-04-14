"""Session interface definitions."""

from __future__ import annotations

from typing import Any, Protocol

from openagent.object_model import RuntimeEvent


class SessionStore(Protocol):
    def append_event(self, event: RuntimeEvent) -> None:
        """Persist a runtime event."""

    def load_session(self, session_id: str) -> Any:
        """Load a session snapshot or handle."""

    def save_session(self, session_id: str, state: Any) -> None:
        """Persist session state."""
