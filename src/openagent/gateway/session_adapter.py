"""In-process gateway session adapter."""

from __future__ import annotations

from datetime import UTC, datetime

from openagent.harness import SimpleHarness
from openagent.object_model import HarnessInstance, RuntimeEvent
from openagent.session import SessionCheckpoint

from .models import LocalSessionHandle


class InProcessSessionAdapter:
    """Expose a local harness as a gateway-managed session runtime."""

    def __init__(
        self,
        runtime: SimpleHarness,
        *,
        agent_id: str = "local-agent",
        gateway_id: str = "local-gateway",
    ) -> None:
        self._runtime = runtime
        self._agent_id = agent_id
        self._gateway_id = gateway_id
        self._handles: dict[str, LocalSessionHandle] = {}

    def spawn(self, session_id: str) -> LocalSessionHandle:
        handle = self._handles.get(session_id)
        if handle is not None:
            return handle
        harness_instance = HarnessInstance(
            harness_instance_id=f"{self._gateway_id}:{session_id}",
            agent_id=self._agent_id,
            gateway_id=self._gateway_id,
            session_id=session_id,
            status="active",
            runtime_state_ref=f"session://{session_id}",
            metadata={"spawned_at": datetime.now(UTC).isoformat()},
        )
        session = self._runtime.sessions.load_session(session_id)
        if getattr(session, "agent_id", None) is None:
            session.agent_id = self._agent_id
            self._runtime.sessions.save_session(session_id, session)
        self._runtime.sessions.acquire_lease(
            session_id,
            harness_instance.harness_instance_id,
            self._agent_id,
        )
        handle = LocalSessionHandle(session_id=session_id, harness_instance=harness_instance)
        self._handles[session_id] = handle
        return handle

    def write_input(self, session_handle: str, input_text: str) -> list[RuntimeEvent]:
        handle = self.spawn(session_handle)
        handle.current_activity = "turn"
        handle.activities.append("turn")
        events, _ = self._runtime.run_turn(input_text, session_handle)
        handle.done = True
        handle.current_activity = None
        return events

    def observe(self, session_handle: str, after: int = 0) -> list[RuntimeEvent]:
        return self._runtime.sessions.read_events(session_handle, after=after)

    def continue_session(self, session_handle: str, approved: bool) -> list[RuntimeEvent]:
        handle = self.spawn(session_handle)
        handle.current_activity = "continuation"
        handle.activities.append("continuation")
        events, _ = self._runtime.continue_turn(session_handle, approved=approved)
        handle.done = True
        handle.current_activity = None
        return events

    def kill(self, session_handle: str) -> None:
        handle = self.spawn(session_handle)
        handle.done = True
        handle.current_activity = "killed"
        if handle.harness_instance is not None:
            self._runtime.sessions.release_lease(
                session_handle,
                handle.harness_instance.harness_instance_id,
            )
            handle.harness_instance.status = "stopped"

    def get_checkpoint(self, session_handle: str) -> SessionCheckpoint:
        return self._runtime.sessions.get_checkpoint(session_handle)

    def get_restore_marker(self, session_handle: str) -> str | None:
        session = self._runtime.sessions.load_session(session_handle)
        return getattr(session, "restore_marker", None)
