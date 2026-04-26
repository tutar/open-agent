"""File-backed session storage baseline."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from openagent.object_model import RuntimeEvent, RuntimeEventType, SessionHarnessLease
from openagent.session.models import (
    ResumeSnapshot,
    SessionCheckpoint,
    SessionCursor,
    SessionMessage,
    SessionRecord,
    WakeRequest,
)
from openagent.shared import (
    DEFAULT_RUNTIME_AGENT_ID,
    resolve_agent_root,
    resolve_agent_transcript_path,
    resolve_session_root,
)


class FileSessionStore:
    """Durable file-backed session store with agent-owned transcript refs."""

    def __init__(self, root_dir: str | Path) -> None:
        self._root_dir = Path(root_dir)
        self._root_dir.mkdir(parents=True, exist_ok=True)

    @property
    def root_dir(self) -> Path:
        return self._root_dir

    def append_event(self, event: RuntimeEvent) -> None:
        self.append_events(event.session_id, [event])

    def append_events(self, session_id: str, events: list[RuntimeEvent]) -> SessionCheckpoint:
        session = self.load_session(session_id)
        session.events.extend(events)
        self.save_session(session_id, session)
        return self.get_checkpoint(session_id)

    def read_events(
        self,
        session_id: str,
        after: int = 0,
        cursor: SessionCursor | None = None,
    ) -> list[RuntimeEvent]:
        log_path = self._event_log_path(session_id)
        if not log_path.exists():
            return []
        events: list[RuntimeEvent] = []
        start = cursor.event_offset if cursor is not None else after
        for line in log_path.read_text(encoding="utf-8").splitlines()[start:]:
            raw = json.loads(line)
            if isinstance(raw, dict):
                events.append(RuntimeEvent.from_dict(raw))
        return events

    def load_session(self, session_id: str) -> SessionRecord:
        state_payload = self._load_state_payload(session_id)
        transcript = self._read_transcript(session_id)
        events = self.read_events(session_id)
        if state_payload is None:
            return SessionRecord(session_id=session_id, messages=transcript, events=events)
        record = SessionRecord.from_dict(state_payload)
        record.messages = transcript
        record.events = events
        return record

    def save_session(self, session_id: str, state: SessionRecord) -> None:
        existing_state = self._load_state_payload(session_id)
        transcript_count = self._persisted_count(existing_state, "transcript_message_count")
        event_count = self._persisted_count(existing_state, "event_count")
        transcript_path = self._ensure_transcript_ref(session_id, state)
        self._append_transcript_suffix(session_id, state, transcript_count, transcript_path)
        self._append_event_suffix(session_id, state, event_count)
        payload = self._session_state_payload(
            state,
            transcript_message_count=len(state.messages),
            event_count=len(state.events),
        )
        self._session_path(session_id).write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )

    def get_checkpoint(self, session_id: str) -> SessionCheckpoint:
        all_events = self.read_events(session_id)
        last_event_id = all_events[-1].event_id if all_events else None
        cursor = SessionCursor(
            session_id=session_id,
            event_offset=len(all_events),
            last_event_id=last_event_id,
        )
        return SessionCheckpoint(
            session_id=session_id,
            event_offset=len(all_events),
            last_event_id=last_event_id,
            cursor=cursor,
            committed_at=datetime.now(UTC).isoformat(),
        )

    def mark_restored(self, session_id: str, cursor: SessionCursor | None = None) -> None:
        session = self.load_session(session_id)
        checkpoint = self.get_checkpoint(session_id)
        marker = (
            cursor.last_event_id
            if cursor is not None and cursor.last_event_id is not None
            else checkpoint.last_event_id
        )
        session.restore_marker = marker
        self.save_session(session_id, session)

    def get_resume_snapshot(self, wake_request: WakeRequest) -> ResumeSnapshot:
        session = self.load_session(wake_request.session_id)
        events = self.read_events(
            wake_request.session_id,
            cursor=wake_request.cursor,
        )
        return ResumeSnapshot(
            session_id=wake_request.session_id,
            runtime_state={
                "status": session.status.value,
                "restore_marker": session.restore_marker,
            },
            transcript_slice=[message.to_dict() for message in session.messages],
            working_state={
                "pending_tool_calls": [
                    tool_call.to_dict() for tool_call in session.pending_tool_calls
                ],
                "event_count": len(events),
            },
            short_term_memory=(
                dict(session.short_term_memory)
                if isinstance(session.short_term_memory, dict)
                else None
            ),
        )

    def acquire_lease(
        self,
        session_id: str,
        harness_instance_id: str,
        agent_id: str,
    ) -> SessionHarnessLease:
        existing = self.get_active_lease(session_id)
        if existing is not None and existing.harness_instance_id != harness_instance_id:
            raise ValueError("Session already has an active harness lease")
        lease = SessionHarnessLease(
            session_id=session_id,
            harness_instance_id=harness_instance_id,
            agent_id=agent_id,
            acquired_at=datetime.now(UTC).isoformat(),
        )
        self._lease_path(session_id).write_text(
            json.dumps(lease.to_dict(), indent=2),
            encoding="utf-8",
        )
        return lease

    def release_lease(self, session_id: str, harness_instance_id: str) -> bool:
        existing = self.get_active_lease(session_id)
        if existing is None or existing.harness_instance_id != harness_instance_id:
            return False
        path = self._lease_path(session_id)
        if path.exists():
            path.unlink()
        return True

    def get_active_lease(self, session_id: str) -> SessionHarnessLease | None:
        path = self._lease_path(session_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError("Lease file must contain a JSON object")
        return SessionHarnessLease.from_dict(data)

    def _load_state_payload(self, session_id: str) -> dict[str, object] | None:
        path = self._session_path(session_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError("Session file must contain a JSON object")
        return data

    def _read_transcript(self, session_id: str) -> list[SessionMessage]:
        transcript_path = self._resolve_transcript_path(session_id)
        if transcript_path is None or not transcript_path.exists():
            return []
        messages: list[SessionMessage] = []
        for line in transcript_path.read_text(encoding="utf-8").splitlines():
            raw = json.loads(line)
            if not isinstance(raw, dict):
                continue
            if raw.get("session_id") != session_id:
                continue
            role = raw.get("role")
            content = raw.get("content")
            metadata = raw.get("metadata")
            if not isinstance(role, str) or not isinstance(content, str):
                continue
            messages.append(
                SessionMessage(
                    role=role,
                    content=content,
                    metadata=dict(metadata) if isinstance(metadata, dict) else {},
                )
            )
        return messages

    def _persisted_count(self, state_payload: dict[str, object] | None, key: str) -> int:
        if state_payload is None:
            return 0
        value = state_payload.get(key)
        return value if isinstance(value, int) and value >= 0 else 0

    def _append_transcript_suffix(
        self,
        session_id: str,
        state: SessionRecord,
        transcript_count: int,
        transcript_path: Path,
    ) -> None:
        if transcript_count > len(state.messages):
            raise ValueError("Cannot truncate append-only transcript")
        new_messages = state.messages[transcript_count:]
        if not new_messages:
            return
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        with transcript_path.open("a", encoding="utf-8") as handle:
            for message in new_messages:
                handle.write(
                    json.dumps(self._transcript_entry(session_id, state, message)) + "\n"
                )

    def _append_event_suffix(
        self,
        session_id: str,
        state: SessionRecord,
        event_count: int,
    ) -> None:
        if event_count > len(state.events):
            raise ValueError("Cannot truncate append-only event log")
        new_events = state.events[event_count:]
        if not new_events:
            return
        log_path = self._event_log_path(session_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            for event in new_events:
                handle.write(json.dumps(event.to_dict()) + "\n")

    def _transcript_entry(
        self,
        session_id: str,
        state: SessionRecord,
        message: SessionMessage,
    ) -> dict[str, object]:
        turn_id, timestamp = self._transcript_entry_context(state, message.role)
        return {
            "session_id": session_id,
            "turn_id": turn_id,
            "timestamp": timestamp,
            "role": message.role,
            "content": message.content,
            "metadata": dict(message.metadata),
        }

    def _transcript_entry_context(
        self,
        state: SessionRecord,
        role: str,
    ) -> tuple[str | None, str]:
        event_type = {
            "user": RuntimeEventType.TURN_STARTED,
            "assistant": RuntimeEventType.ASSISTANT_MESSAGE,
            "tool": RuntimeEventType.TOOL_RESULT,
        }.get(role)
        if event_type is not None:
            for event in reversed(state.events):
                if event.event_type is event_type:
                    return event.task_id, event.timestamp
        for event in reversed(state.events):
            return event.task_id, event.timestamp
        return None, datetime.now(UTC).isoformat()

    def _session_state_payload(
        self,
        state: SessionRecord,
        *,
        transcript_message_count: int,
        event_count: int,
    ) -> dict[str, object]:
        return {
            "session_id": state.session_id,
            "agent_id": state.agent_id,
            "status": state.status.value,
            "pending_tool_calls": [tool_call.to_dict() for tool_call in state.pending_tool_calls],
            "restore_marker": state.restore_marker,
            "short_term_memory": state.short_term_memory,
            "metadata": state.metadata,
            "transcript_message_count": transcript_message_count,
            "event_count": event_count,
        }

    def _ensure_transcript_ref(self, session_id: str, state: SessionRecord) -> Path:
        path = self._resolve_transcript_path(session_id)
        if path is None:
            path = self._default_transcript_path(state)
            self._transcript_ref_path(session_id).parent.mkdir(parents=True, exist_ok=True)
            self._transcript_ref_path(session_id).write_text(
                str(path.resolve()),
                encoding="utf-8",
            )
        return path

    def _resolve_transcript_path(self, session_id: str) -> Path | None:
        ref_path = self._transcript_ref_path(session_id)
        if not ref_path.exists():
            return None
        target = ref_path.read_text(encoding="utf-8").strip()
        if not target:
            return None
        return Path(target).expanduser().resolve()

    def _default_transcript_path(self, state: SessionRecord) -> Path:
        metadata = dict(state.metadata) if isinstance(state.metadata, dict) else {}
        explicit_root = metadata.get("agent_root_dir")
        if isinstance(explicit_root, str) and explicit_root.strip():
            agent_root = Path(explicit_root).expanduser().resolve()
        else:
            openagent_root = self._default_openagent_root()
            role_id = metadata.get("role_id")
            agent_root = Path(
                resolve_agent_root(
                    str(openagent_root),
                    role_id if isinstance(role_id, str) else None,
                )
            )
        agent_id = state.agent_id or metadata.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id.strip():
            agent_id = DEFAULT_RUNTIME_AGENT_ID
        return resolve_agent_transcript_path(str(agent_root), agent_id)

    def _default_openagent_root(self) -> Path:
        if self._root_dir.name == "sessions":
            return self._root_dir.parent.resolve()
        return self._root_dir.resolve().parent

    def _session_dir(self, session_id: str) -> Path:
        path = resolve_session_root(str(self._root_dir), session_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _session_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "state.json"

    def _event_log_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "events.jsonl"

    def _transcript_ref_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "transcript.ref"

    def _lease_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "lease.json"
