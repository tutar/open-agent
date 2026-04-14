"""In-memory session storage baseline."""

from __future__ import annotations

import json
from pathlib import Path

from openagent.object_model import RuntimeEvent
from openagent.session.models import SessionRecord


class InMemorySessionStore:
    """Simple session store used for tests and local baseline wiring."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionRecord] = {}

    def append_event(self, event: RuntimeEvent) -> None:
        session = self.load_session(event.session_id)
        session.events.append(event)

    def load_session(self, session_id: str) -> SessionRecord:
        return self._sessions.setdefault(session_id, SessionRecord(session_id=session_id))

    def save_session(self, session_id: str, state: SessionRecord) -> None:
        self._sessions[session_id] = state


class FileSessionStore:
    """Durable JSON-backed session store for resume semantics."""

    def __init__(self, root_dir: str | Path) -> None:
        self._root_dir = Path(root_dir)
        self._root_dir.mkdir(parents=True, exist_ok=True)

    def append_event(self, event: RuntimeEvent) -> None:
        session = self.load_session(event.session_id)
        session.events.append(event)
        self.save_session(event.session_id, session)

    def load_session(self, session_id: str) -> SessionRecord:
        path = self._session_path(session_id)
        if not path.exists():
            return SessionRecord(session_id=session_id)

        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError("Session file must contain a JSON object")
        return SessionRecord.from_dict(data)

    def save_session(self, session_id: str, state: SessionRecord) -> None:
        path = self._session_path(session_id)
        path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")

    def _session_path(self, session_id: str) -> Path:
        return self._root_dir / f"{session_id}.json"
