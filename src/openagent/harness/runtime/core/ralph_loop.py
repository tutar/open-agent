"""Explicit turn-local runtime loop implementations."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from time import perf_counter
from typing import TYPE_CHECKING

from openagent.harness.runtime.core.state import TurnState
from openagent.harness.runtime.core.terminal import (
    CancelledTurn,
    RetryExhaustedTurn,
    TimedOutTurn,
    TurnControl,
)
from openagent.object_model import (
    JsonValue,
    RuntimeEvent,
    RuntimeEventType,
    TerminalState,
    TerminalStatus,
)
from openagent.observability import ProgressUpdate, RuntimeMetric
from openagent.session import SessionRecord, SessionStatus
from openagent.tools import (
    RequiresActionError,
    ToolCancelledError,
    ToolExecutionContext,
    ToolExecutionFailedError,
    ToolPermissionDeniedError,
)

if TYPE_CHECKING:
    from openagent.harness.runtime.core.agent_runtime import SimpleHarness


@dataclass(slots=True)
class RalphLoop:
    """Concrete local runtime loop used by `SimpleHarness`."""

    harness: SimpleHarness
    state: TurnState = field(default_factory=TurnState)
    _SEARCH_TOOL_NAMES = frozenset({"WebSearch", "WebFetch"})
    _FILE_TOOL_NAMES = frozenset({"Read", "Write", "Edit", "Glob", "Grep", "Bash"})

    def _new_turn_task_id(self, session_handle: str) -> str:
        session = self.harness.sessions.load_session(session_handle)
        event_index = len(session.events) + 1
        return f"turn:{session_handle}:{event_index}"

    def run_turn_stream(
        self,
        input: str,
        session_handle: str,
        control: TurnControl | None = None,
    ) -> Iterator[RuntimeEvent]:
        yield from self._execute_turn_stream(
            input=input,
            session_handle=session_handle,
            control=control or TurnControl(),
        )

    def continue_turn(
        self,
        session_handle: str,
        approved: bool,
    ) -> tuple[list[RuntimeEvent], TerminalState]:
        observability = self.harness.observability
        assert observability is not None
        session = self.harness.sessions.load_session(session_handle)
        if not isinstance(session, SessionRecord):
            raise TypeError("SimpleHarness requires SessionRecord-compatible session state")
        if session.status is not SessionStatus.REQUIRES_ACTION or not session.pending_tool_calls:
            raise ValueError("Session has no pending requires_action continuation")
        working_directory = self.harness.ensure_session_workspace(session_handle, session)
        turn_task_id = self._new_turn_task_id(session_handle)
        self.state.task_id = turn_task_id
        self.state.api_duration_ms = 0.0
        interaction_span = observability.start_span(
            "interaction",
            {"continuation": True, "approved": approved},
            session_id=session_handle,
            task_id=turn_task_id,
        )
        interaction_started_at = perf_counter()
        self.harness._emit_session_state(session_handle, "running", reason="continuation_started")

        if not approved:
            session.pending_tool_calls = []
            session.status = SessionStatus.IDLE
            self.harness._run_post_turn_processors(session_handle, session)
            self.harness._persist_session(session_handle, session)
            terminal = TerminalState(status=TerminalStatus.STOPPED, reason="approval_rejected")
            event = self.harness._append_event(
                session,
                self.harness._new_event(
                    session_id=session_handle,
                    event_type=RuntimeEventType.TURN_FAILED,
                    payload=terminal.to_dict(),
                ),
            )
            duration_ms = (perf_counter() - interaction_started_at) * 1000
            self.harness._emit_metric(
                RuntimeMetric(
                    name="turn.duration_ms",
                    value=duration_ms,
                    unit="ms",
                    session_id=session_handle,
                    task_id=turn_task_id,
                    attributes={"reason": "approval_rejected"},
                )
            )
            self.harness._emit_session_state(session_handle, "idle", reason="approval_rejected")
            observability.end_span(
                interaction_span,
                {"reason": "approval_rejected"},
                status="cancelled",
                duration_ms=duration_ms,
            )
            return [event], terminal

        emitted_events: list[RuntimeEvent] = []
        session.status = SessionStatus.RUNNING
        pending_calls = list(session.pending_tool_calls)
        tool_events, tool_results, tool_error = self.harness._execute_tool_stream(
            session=session,
            session_handle=session_handle,
            tool_calls=pending_calls,
            context=ToolExecutionContext(
                session_id=session_handle,
                approved_tool_names=[tool_call.tool_name for tool_call in pending_calls],
                working_directory=working_directory,
                agent_id=session.agent_id,
                task_id=turn_task_id,
                parent_span=interaction_span,
            ),
        )
        emitted_events.extend(tool_events)
        if isinstance(tool_error, ToolExecutionFailedError):
            terminal = TerminalState(
                status=TerminalStatus.FAILED,
                reason="tool_execution_failed",
                summary=str(tool_error),
            )
            emitted_events.append(
                self.harness._append_event(
                    session,
                    self.harness._new_event(
                        session_id=session_handle,
                        event_type=RuntimeEventType.TURN_FAILED,
                        payload=terminal.to_dict(),
                    ),
                )
            )
            session.pending_tool_calls = []
            session.status = SessionStatus.IDLE
            self.harness._run_post_turn_processors(session_handle, session)
            self.harness._persist_session(session_handle, session)
            return emitted_events, terminal
        if isinstance(tool_error, ToolCancelledError):
            terminal = TerminalState(
                status=TerminalStatus.STOPPED,
                reason="tool_cancelled",
                summary=str(tool_error),
            )
            emitted_events.append(
                self.harness._append_event(
                    session,
                    self.harness._new_event(
                        session_id=session_handle,
                        event_type=RuntimeEventType.TURN_FAILED,
                        payload=terminal.to_dict(),
                    ),
                )
            )
            session.pending_tool_calls = []
            session.status = SessionStatus.IDLE
            self.harness._run_post_turn_processors(session_handle, session)
            self.harness._persist_session(session_handle, session)
            return emitted_events, terminal
        session.pending_tool_calls = []
        self.harness._append_tool_results(session, tool_results)

        request = self.harness.build_model_input(session, [])
        response, _, _, _ = self.harness._run_model_once(
            request=request,
            session=session,
            session_handle=session_handle,
            control=TurnControl(),
        )
        handled = self.harness.handle_model_output(response)
        if handled.assistant_message is not None and not handled.tool_calls:
            session.messages.append(
                self.harness._new_session_message(
                    role="assistant",
                    content=handled.assistant_message,
                )
            )
            emitted_events.append(
                self.harness._append_event(
                    session,
                    self.harness._new_event(
                        session_id=session_handle,
                        event_type=RuntimeEventType.ASSISTANT_MESSAGE,
                        payload={"message": handled.assistant_message},
                    ),
                )
            )

        terminal = TerminalState(status=TerminalStatus.COMPLETED, reason="approval_continuation")
        emitted_events.append(
            self.harness._append_event(
                session,
                self.harness._new_event(
                    session_id=session_handle,
                    event_type=RuntimeEventType.TURN_COMPLETED,
                    payload=terminal.to_dict(),
                ),
            )
        )
        session.status = SessionStatus.IDLE
        self.harness._run_post_turn_processors(session_handle, session)
        self.harness._persist_session(session_handle, session)
        duration_ms = (perf_counter() - interaction_started_at) * 1000
        self.harness._emit_metric(
            RuntimeMetric(
                name="turn.duration_ms",
                value=duration_ms,
                unit="ms",
                session_id=session_handle,
                task_id=turn_task_id,
                attributes={"reason": "approval_continuation"},
            )
        )
        self.harness._emit_progress(
            ProgressUpdate(
                scope="turn",
                session_id=session_handle,
                task_id=turn_task_id,
                summary="approval_continuation",
                last_activity="turn_completed",
                duration_ms=duration_ms,
                tool_use_count=len(pending_calls),
            )
        )
        self.harness._emit_session_state(session_handle, "idle", reason="approval_continuation")
        observability.end_span(
            interaction_span,
            {"reason": "approval_continuation"},
            status="completed",
            duration_ms=duration_ms,
        )
        return emitted_events, terminal

    def _execute_turn_stream(
        self,
        input: str,
        session_handle: str,
        control: TurnControl,
    ) -> Iterator[RuntimeEvent]:
        observability = self.harness.observability
        assert observability is not None
        session = self.harness.sessions.load_session(session_handle)
        if not isinstance(session, SessionRecord):
            raise TypeError("SimpleHarness requires SessionRecord-compatible session state")
        working_directory = self.harness.ensure_session_workspace(session_handle, session)
        turn_task_id = self._new_turn_task_id(session_handle)
        interaction_span = observability.start_span(
            "interaction",
            {"input_preview": input[:80]},
            session_id=session_handle,
            task_id=turn_task_id,
        )
        interaction_started_at = perf_counter()
        tool_use_count = 0

        self.state = TurnState(
            messages=[message.to_dict() for message in session.messages],
            turn_count=0,
            transition="turn_started",
            requires_action=False,
            task_id=turn_task_id,
        )
        session.status = SessionStatus.RUNNING
        self.harness._emit_session_state(session_handle, "running", reason="turn_started")
        self.harness._emit_progress(
            ProgressUpdate(
                scope="turn",
                session_id=session_handle,
                task_id=turn_task_id,
                summary="turn_started",
                last_activity="turn_started",
            )
        )
        session.messages.append(self.harness._new_session_message(role="user", content=input))
        self.state.messages = [message.to_dict() for message in session.messages]
        yield self.harness._append_event(
            session,
            self.harness._new_event(
                session_id=session_handle,
                event_type=RuntimeEventType.TURN_STARTED,
                payload={"input": input},
            ),
        )
        self.harness._persist_session(session_handle, session)

        for iteration in range(self.harness.max_iterations):
            self.state.turn_count = iteration + 1
            self.state.transition = "model_request"
            cancelled = self.harness._check_cancelled(control)
            if cancelled:
                self.state.transition = "aborted"
                duration_ms = (perf_counter() - interaction_started_at) * 1000
                self.harness._emit_metric(
                    RuntimeMetric(
                        name="turn.duration_ms",
                        value=duration_ms,
                        unit="ms",
                        session_id=session_handle,
                        task_id=turn_task_id,
                        attributes={"reason": "cancelled"},
                    )
                )
                self.harness._emit_session_state(session_handle, "idle", reason="cancelled")
                observability.end_span(
                    interaction_span,
                    {"reason": "cancelled"},
                    status="cancelled",
                    duration_ms=duration_ms,
                )
                yield self.harness._emit_terminal(
                    session,
                    session_handle,
                    RuntimeEventType.TURN_FAILED,
                    TerminalState(status=TerminalStatus.STOPPED, reason="cancelled"),
                )
                return

            request = self.harness.build_model_input(session, [])
            try:
                handled, streamed_events = self.harness._run_model_with_retries(
                    request=request,
                    session=session,
                    session_handle=session_handle,
                    control=control,
                    parent_span=interaction_span,
                )
            except CancelledTurn:
                self.state.transition = "aborted"
                duration_ms = (perf_counter() - interaction_started_at) * 1000
                self.harness._emit_metric(
                    RuntimeMetric(
                        name="turn.duration_ms",
                        value=duration_ms,
                        unit="ms",
                        session_id=session_handle,
                        task_id=turn_task_id,
                        attributes={"reason": "cancelled"},
                    )
                )
                self.harness._emit_session_state(session_handle, "idle", reason="cancelled")
                observability.end_span(
                    interaction_span,
                    {"reason": "cancelled"},
                    status="cancelled",
                    duration_ms=duration_ms,
                )
                yield self.harness._emit_terminal(
                    session,
                    session_handle,
                    RuntimeEventType.TURN_FAILED,
                    TerminalState(status=TerminalStatus.STOPPED, reason="cancelled"),
                )
                return
            except TimedOutTurn:
                self.state.transition = "failed"
                duration_ms = (perf_counter() - interaction_started_at) * 1000
                self.harness._emit_metric(
                    RuntimeMetric(
                        name="turn.duration_ms",
                        value=duration_ms,
                        unit="ms",
                        session_id=session_handle,
                        task_id=turn_task_id,
                        attributes={"reason": "timeout"},
                    )
                )
                self.harness._emit_session_state(session_handle, "idle", reason="timeout")
                observability.end_span(
                    interaction_span,
                    {"reason": "timeout"},
                    status="error",
                    duration_ms=duration_ms,
                )
                yield self.harness._emit_terminal(
                    session,
                    session_handle,
                    RuntimeEventType.TURN_FAILED,
                    TerminalState(
                        status=TerminalStatus.FAILED,
                        reason="timeout",
                        retryable=True,
                    ),
                )
                return
            except RetryExhaustedTurn as exc:
                self.state.transition = "failed"
                duration_ms = (perf_counter() - interaction_started_at) * 1000
                self.harness._emit_metric(
                    RuntimeMetric(
                        name="turn.duration_ms",
                        value=duration_ms,
                        unit="ms",
                        session_id=session_handle,
                        task_id=turn_task_id,
                        attributes={"reason": "retry_exhausted"},
                    )
                )
                self.harness._emit_session_state(
                    session_handle,
                    "idle",
                    reason="retry_exhausted",
                )
                observability.end_span(
                    interaction_span,
                    {"reason": "retry_exhausted", "summary": str(exc)},
                    status="error",
                    duration_ms=duration_ms,
                )
                yield self.harness._emit_terminal(
                    session,
                    session_handle,
                    RuntimeEventType.TURN_FAILED,
                    TerminalState(
                        status=TerminalStatus.FAILED,
                        reason="retry_exhausted",
                        retryable=False,
                        summary=str(exc),
                    ),
                )
                return

            yield from streamed_events

            if handled.assistant_message is not None and not handled.tool_calls:
                session.messages.append(
                    self.harness._new_session_message(
                        role="assistant",
                        content=handled.assistant_message,
                    )
                )
                self.state.messages = [message.to_dict() for message in session.messages]
                yield self.harness._append_event(
                    session,
                    self.harness._new_event(
                        session_id=session_handle,
                        event_type=RuntimeEventType.ASSISTANT_MESSAGE,
                        payload={"message": handled.assistant_message},
                    ),
                )
                self.harness._persist_session(session_handle, session)

            if not handled.tool_calls:
                self.state.transition = "completed"
                duration_ms = (perf_counter() - interaction_started_at) * 1000
                self.harness._emit_metric(
                    RuntimeMetric(
                        name="turn.duration_ms",
                        value=duration_ms,
                        unit="ms",
                        session_id=session_handle,
                        task_id=turn_task_id,
                        attributes={"reason": "assistant_message"},
                    )
                )
                self.harness._emit_progress(
                    ProgressUpdate(
                        scope="turn",
                        session_id=session_handle,
                        task_id=turn_task_id,
                        summary="assistant_message",
                        last_activity="turn_completed",
                        duration_ms=duration_ms,
                        tool_use_count=tool_use_count,
                    )
                )
                self.harness._emit_session_state(
                    session_handle,
                    "idle",
                    reason="assistant_message",
                )
                observability.end_span(
                    interaction_span,
                    {"reason": "assistant_message"},
                    status="completed",
                    duration_ms=duration_ms,
                )
                yield self.harness._emit_terminal(
                    session,
                    session_handle,
                    RuntimeEventType.TURN_COMPLETED,
                    TerminalState(status=TerminalStatus.COMPLETED, reason="assistant_message"),
                )
                return

            self.harness._ensure_tool_call_ids(handled.tool_calls)
            self.state.transition = "tool_execution"
            tool_use_count += len(handled.tool_calls)

            try:
                tool_events, tool_results, tool_error = self.harness._execute_tool_stream(
                    session=session,
                    session_handle=session_handle,
                    tool_calls=handled.tool_calls,
                    context=ToolExecutionContext(
                        session_id=session_handle,
                        agent_id=session.agent_id,
                        working_directory=working_directory,
                        task_id=turn_task_id,
                        parent_span=interaction_span,
                    ),
                )
                yield from tool_events
            except RequiresActionError as exc:
                self.state.transition = "requires_action"
                self.state.requires_action = True
                self.harness.hook_runtime.execute_hooks(
                    scope="runtime",
                    event="requires_action",
                    payload={"session_id": session_handle},
                )
                event = self.harness._append_event(
                    session,
                    self.harness._new_event(
                        session_id=session_handle,
                        event_type=RuntimeEventType.REQUIRES_ACTION,
                        payload=self.harness._requires_action_payload(exc.requires_action),
                    ),
                )
                yield event
                session.status = SessionStatus.REQUIRES_ACTION
                session.pending_tool_calls = handled.tool_calls
                self.harness._run_post_turn_processors(session_handle, session)
                self.harness._persist_session(session_handle, session)
                duration_ms = (perf_counter() - interaction_started_at) * 1000
                self.harness._emit_metric(
                    RuntimeMetric(
                        name="turn.duration_ms",
                        value=duration_ms,
                        unit="ms",
                        session_id=session_handle,
                        task_id=turn_task_id,
                        attributes={"reason": "requires_action"},
                    )
                )
                self.harness._emit_progress(
                    ProgressUpdate(
                        scope="turn",
                        session_id=session_handle,
                        task_id=turn_task_id,
                        summary="requires_action",
                        last_activity="requires_action",
                        duration_ms=duration_ms,
                        tool_use_count=tool_use_count,
                    )
                )
                self.harness._emit_session_state(
                    session_handle,
                    "requires_action",
                    reason="tool_permission",
                )
                observability.end_span(
                    interaction_span,
                    {"reason": "requires_action"},
                    status="blocked",
                    duration_ms=duration_ms,
                )
                return
            except ToolPermissionDeniedError as exc:
                self.state.transition = "failed"
                duration_ms = (perf_counter() - interaction_started_at) * 1000
                self.harness._emit_metric(
                    RuntimeMetric(
                        name="turn.duration_ms",
                        value=duration_ms,
                        unit="ms",
                        session_id=session_handle,
                        task_id=turn_task_id,
                        attributes={"reason": "tool_permission_denied"},
                    )
                )
                self.harness._emit_session_state(
                    session_handle,
                    "idle",
                    reason="tool_permission_denied",
                )
                observability.end_span(
                    interaction_span,
                    {"reason": "tool_permission_denied", "summary": str(exc)},
                    status="error",
                    duration_ms=duration_ms,
                )
                yield self.harness._emit_terminal(
                    session,
                    session_handle,
                    RuntimeEventType.TURN_FAILED,
                    TerminalState(
                        status=TerminalStatus.FAILED,
                        reason="tool_permission_denied",
                        summary=str(exc),
                    ),
                )
                return
            except ToolExecutionFailedError as exc:
                self.state.transition = "failed"
                duration_ms = (perf_counter() - interaction_started_at) * 1000
                self.harness._emit_metric(
                    RuntimeMetric(
                        name="turn.duration_ms",
                        value=duration_ms,
                        unit="ms",
                        session_id=session_handle,
                        task_id=turn_task_id,
                        attributes={"reason": "tool_execution_failed"},
                    )
                )
                self.harness._emit_session_state(
                    session_handle,
                    "idle",
                    reason="tool_execution_failed",
                )
                observability.end_span(
                    interaction_span,
                    {"reason": "tool_execution_failed", "summary": str(exc)},
                    status="error",
                    duration_ms=duration_ms,
                )
                yield self.harness._emit_terminal(
                    session,
                    session_handle,
                    RuntimeEventType.TURN_FAILED,
                    TerminalState(
                        status=TerminalStatus.FAILED,
                        reason="tool_execution_failed",
                        summary=str(exc),
                    ),
                )
                return
            except ToolCancelledError as exc:
                self.state.transition = "aborted"
                duration_ms = (perf_counter() - interaction_started_at) * 1000
                self.harness._emit_metric(
                    RuntimeMetric(
                        name="turn.duration_ms",
                        value=duration_ms,
                        unit="ms",
                        session_id=session_handle,
                        task_id=turn_task_id,
                        attributes={"reason": "tool_cancelled"},
                    )
                )
                self.harness._emit_session_state(session_handle, "idle", reason="tool_cancelled")
                observability.end_span(
                    interaction_span,
                    {"reason": "tool_cancelled", "summary": str(exc)},
                    status="cancelled",
                    duration_ms=duration_ms,
                )
                yield self.harness._emit_terminal(
                    session,
                    session_handle,
                    RuntimeEventType.TURN_FAILED,
                    TerminalState(
                        status=TerminalStatus.STOPPED,
                        reason="tool_cancelled",
                        summary=str(exc),
                    ),
                )
                return

            if isinstance(tool_error, ToolExecutionFailedError):
                self.state.transition = "failed"
                duration_ms = (perf_counter() - interaction_started_at) * 1000
                self.harness._emit_metric(
                    RuntimeMetric(
                        name="turn.duration_ms",
                        value=duration_ms,
                        unit="ms",
                        session_id=session_handle,
                        task_id=turn_task_id,
                        attributes={"reason": "tool_execution_failed"},
                    )
                )
                self.harness._emit_session_state(
                    session_handle,
                    "idle",
                    reason="tool_execution_failed",
                )
                observability.end_span(
                    interaction_span,
                    {"reason": "tool_execution_failed", "summary": str(tool_error)},
                    status="error",
                    duration_ms=duration_ms,
                )
                yield self.harness._emit_terminal(
                    session,
                    session_handle,
                    RuntimeEventType.TURN_FAILED,
                    TerminalState(
                        status=TerminalStatus.FAILED,
                        reason="tool_execution_failed",
                        summary=str(tool_error),
                    ),
                )
                return
            if isinstance(tool_error, ToolCancelledError):
                self.state.transition = "aborted"
                duration_ms = (perf_counter() - interaction_started_at) * 1000
                self.harness._emit_metric(
                    RuntimeMetric(
                        name="turn.duration_ms",
                        value=duration_ms,
                        unit="ms",
                        session_id=session_handle,
                        task_id=turn_task_id,
                        attributes={"reason": "tool_cancelled"},
                    )
                )
                self.harness._emit_session_state(session_handle, "idle", reason="tool_cancelled")
                observability.end_span(
                    interaction_span,
                    {"reason": "tool_cancelled", "summary": str(tool_error)},
                    status="cancelled",
                    duration_ms=duration_ms,
                )
                yield self.harness._emit_terminal(
                    session,
                    session_handle,
                    RuntimeEventType.TURN_FAILED,
                    TerminalState(
                        status=TerminalStatus.STOPPED,
                        reason="tool_cancelled",
                        summary=str(tool_error),
                    ),
                )
                return

            self.harness._append_tool_results(session, tool_results)
            self.state.messages = [message.to_dict() for message in session.messages]
            self.harness._persist_session(session_handle, session)

        self.state.transition = "failed"
        failure_summary = self._iteration_limit_summary(session)
        failure_attributes: dict[str, JsonValue] = {
            "reason": "iteration_limit_exceeded",
            "summary": failure_summary,
            "failure_category": self._iteration_limit_category(session),
        }
        duration_ms = (perf_counter() - interaction_started_at) * 1000
        self.harness._emit_metric(
            RuntimeMetric(
                name="turn.duration_ms",
                value=duration_ms,
                unit="ms",
                session_id=session_handle,
                task_id=turn_task_id,
                attributes=failure_attributes,
            )
        )
        self.harness._emit_session_state(
            session_handle,
            "idle",
            reason="iteration_limit_exceeded",
        )
        observability.end_span(
            interaction_span,
            failure_attributes,
            status="error",
            duration_ms=duration_ms,
        )
        yield self.harness._emit_terminal(
            session,
            session_handle,
            RuntimeEventType.TURN_FAILED,
            TerminalState(
                status=TerminalStatus.FAILED,
                reason="iteration_limit_exceeded",
                summary=failure_summary,
            ),
        )

    def _iteration_limit_summary(self, session: SessionRecord) -> str:
        category = self._iteration_limit_category(session)
        prefix = f"Iteration limit exceeded after {self.harness.max_iterations} iterations"
        if category == "repeated_search_loop":
            return f"{prefix} of repeated search or fetch calls without a final answer."
        if category == "repeated_file_ops_loop":
            return f"{prefix} of repeated file operations without a final answer."
        if category == "repeated_tool_loop":
            tool_name = self._last_started_tool_name(session) or "tool"
            return f"{prefix} of repeated {tool_name} calls without a final answer."
        return (
            f"{prefix} because the model kept calling tools and never produced a final response."
        )

    def _iteration_limit_category(self, session: SessionRecord) -> str:
        tool_names = self._tool_started_names(session)
        if not tool_names:
            return "generic_iteration_limit"
        unique_names = set(tool_names)
        if unique_names.issubset(self._SEARCH_TOOL_NAMES):
            return "repeated_search_loop"
        if unique_names.issubset(self._FILE_TOOL_NAMES):
            return "repeated_file_ops_loop"
        if len(unique_names) == 1:
            return "repeated_tool_loop"
        return "tool_chain_no_final_answer"

    def _tool_started_names(self, session: SessionRecord) -> list[str]:
        names: list[str] = []
        for event in session.events:
            if event.event_type is not RuntimeEventType.TOOL_STARTED:
                continue
            tool_name = event.payload.get("tool_name")
            if isinstance(tool_name, str) and tool_name:
                names.append(tool_name)
        return names

    def _last_started_tool_name(self, session: SessionRecord) -> str | None:
        for event in reversed(session.events):
            if event.event_type is not RuntimeEventType.TOOL_STARTED:
                continue
            tool_name = event.payload.get("tool_name")
            if isinstance(tool_name, str) and tool_name:
                return tool_name
        return None
