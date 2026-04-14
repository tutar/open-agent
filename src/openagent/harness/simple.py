"""Minimal harness baseline for local testing and spec prototyping."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from openagent.harness.models import ModelAdapter, ModelTurnRequest, ModelTurnResponse
from openagent.object_model import (
    JsonObject,
    RuntimeEvent,
    RuntimeEventType,
    TerminalState,
    TerminalStatus,
    ToolResult,
)
from openagent.session import SessionMessage, SessionRecord, SessionStatus, SessionStore
from openagent.tools import (
    RequiresActionError,
    ToolCall,
    ToolExecutionContext,
    ToolExecutor,
    ToolPermissionDeniedError,
    ToolRegistry,
)


@dataclass(slots=True)
class SimpleHarness:
    """Run a local turn against an injected model adapter."""

    model: ModelAdapter
    sessions: SessionStore
    tools: ToolRegistry
    executor: ToolExecutor
    max_iterations: int = 8

    def run_turn(self, input: str, session_handle: str) -> tuple[list[RuntimeEvent], TerminalState]:
        session = self.sessions.load_session(session_handle)
        if not isinstance(session, SessionRecord):
            raise TypeError("SimpleHarness requires SessionRecord-compatible session state")

        emitted_events: list[RuntimeEvent] = []
        session.status = SessionStatus.RUNNING
        session.messages.append(SessionMessage(role="user", content=input))
        emitted_events.append(
            self._append_event(
                session,
                self._new_event(
                    session_id=session_handle,
                    event_type=RuntimeEventType.TURN_STARTED,
                    payload={"input": input},
                ),
            )
        )
        self.sessions.save_session(session_handle, session)

        for _ in range(self.max_iterations):
            request = self.build_model_input(session, [])
            response = self.model.generate(request)
            handled = self.handle_model_output(response)
            self._ensure_tool_call_ids(handled.tool_calls)

            if handled.assistant_message is not None:
                assistant_message = handled.assistant_message
                session.messages.append(SessionMessage(role="assistant", content=assistant_message))
                event = self._new_event(
                    session_id=session_handle,
                    event_type=RuntimeEventType.ASSISTANT_MESSAGE,
                    payload={"message": assistant_message},
                )
                emitted_events.append(event)
                self.sessions.append_event(event)
                self.sessions.save_session(session_handle, session)

            if not handled.tool_calls:
                terminal = TerminalState(
                    status=TerminalStatus.COMPLETED,
                    reason="assistant_message",
                )
                emitted_events.append(
                    self._append_event(
                        session,
                        self._new_event(
                            session_id=session_handle,
                            event_type=RuntimeEventType.TURN_COMPLETED,
                            payload=terminal.to_dict(),
                        ),
                    )
                )
                session.status = SessionStatus.IDLE
                self.sessions.save_session(session_handle, session)
                return emitted_events, terminal

            for tool_call in handled.tool_calls:
                emitted_events.append(
                    self._append_event(
                        session,
                        self._new_event(
                            session_id=session_handle,
                            event_type=RuntimeEventType.TOOL_STARTED,
                            payload=self._tool_call_payload(tool_call),
                        ),
                    )
                )

            try:
                tool_results = self.executor.run_tools(
                    handled.tool_calls,
                    ToolExecutionContext(session_id=session_handle),
                )
            except RequiresActionError as exc:
                event = self._new_event(
                    session_id=session_handle,
                    event_type=RuntimeEventType.REQUIRES_ACTION,
                    payload=self._requires_action_payload(exc.requires_action),
                )
                emitted_events.append(self._append_event(session, event))
                session.status = SessionStatus.REQUIRES_ACTION
                session.pending_tool_calls = handled.tool_calls
                self.sessions.save_session(session_handle, session)
                return emitted_events, TerminalState(
                    status=TerminalStatus.BLOCKED,
                    reason="requires_action",
                    summary=exc.requires_action.description,
                )
            except ToolPermissionDeniedError as exc:
                terminal = TerminalState(
                    status=TerminalStatus.FAILED,
                    reason="tool_permission_denied",
                    summary=str(exc),
                )
                emitted_events.append(
                    self._append_event(
                        session,
                        self._new_event(
                            session_id=session_handle,
                            event_type=RuntimeEventType.TURN_FAILED,
                            payload=terminal.to_dict(),
                        ),
                    )
                )
                session.status = SessionStatus.IDLE
                self.sessions.save_session(session_handle, session)
                return emitted_events, terminal

            self._append_tool_results(session, tool_results)
            for result in tool_results:
                emitted_events.append(
                    self._append_event(
                        session,
                        self._new_event(
                            session_id=session_handle,
                            event_type=RuntimeEventType.TOOL_RESULT,
                            payload=self._tool_result_payload(result),
                        ),
                    )
                )
            self.sessions.save_session(session_handle, session)

        terminal = TerminalState(
            status=TerminalStatus.FAILED,
            reason="iteration_limit_exceeded",
        )
        emitted_events.append(
            self._append_event(
                session,
                self._new_event(
                    session_id=session_handle,
                    event_type=RuntimeEventType.TURN_FAILED,
                    payload=terminal.to_dict(),
                ),
            )
        )
        session.status = SessionStatus.IDLE
        self.sessions.save_session(session_handle, session)
        return emitted_events, terminal

    def continue_turn(
        self,
        session_handle: str,
        approved: bool,
    ) -> tuple[list[RuntimeEvent], TerminalState]:
        session = self.sessions.load_session(session_handle)
        if not isinstance(session, SessionRecord):
            raise TypeError("SimpleHarness requires SessionRecord-compatible session state")
        if session.status is not SessionStatus.REQUIRES_ACTION or not session.pending_tool_calls:
            raise ValueError("Session has no pending requires_action continuation")

        if not approved:
            session.pending_tool_calls = []
            session.status = SessionStatus.IDLE
            self.sessions.save_session(session_handle, session)
            terminal = TerminalState(status=TerminalStatus.STOPPED, reason="approval_rejected")
            event = self._append_event(
                session,
                self._new_event(
                    session_id=session_handle,
                    event_type=RuntimeEventType.TURN_FAILED,
                    payload=terminal.to_dict(),
                ),
            )
            return [event], terminal

        emitted_events: list[RuntimeEvent] = []
        session.status = SessionStatus.RUNNING
        pending_calls = list(session.pending_tool_calls)
        for tool_call in pending_calls:
            emitted_events.append(
                self._append_event(
                    session,
                    self._new_event(
                        session_id=session_handle,
                        event_type=RuntimeEventType.TOOL_STARTED,
                        payload=self._tool_call_payload(tool_call),
                    ),
                )
            )

        tool_results = self.executor.run_tools(
            pending_calls,
            ToolExecutionContext(
                session_id=session_handle,
                approved_tool_names=[tool_call.tool_name for tool_call in pending_calls],
            ),
        )
        session.pending_tool_calls = []
        self._append_tool_results(session, tool_results)
        for result in tool_results:
            emitted_events.append(
                self._append_event(
                    session,
                    self._new_event(
                        session_id=session_handle,
                        event_type=RuntimeEventType.TOOL_RESULT,
                        payload=self._tool_result_payload(result),
                    ),
                )
            )

        request = self.build_model_input(session, [])
        response = self.model.generate(request)
        handled = self.handle_model_output(response)
        if handled.assistant_message is not None:
            session.messages.append(
                SessionMessage(role="assistant", content=handled.assistant_message)
            )
            emitted_events.append(
                self._append_event(
                    session,
                    self._new_event(
                        session_id=session_handle,
                        event_type=RuntimeEventType.ASSISTANT_MESSAGE,
                        payload={"message": handled.assistant_message},
                    ),
                )
            )

        terminal = TerminalState(status=TerminalStatus.COMPLETED, reason="approval_continuation")
        emitted_events.append(
            self._append_event(
                session,
                self._new_event(
                    session_id=session_handle,
                    event_type=RuntimeEventType.TURN_COMPLETED,
                    payload=terminal.to_dict(),
                ),
            )
        )
        session.status = SessionStatus.IDLE
        self.sessions.save_session(session_handle, session)
        return emitted_events, terminal

    def build_model_input(
        self,
        session_slice: SessionRecord,
        context_providers: list[object],
    ) -> ModelTurnRequest:
        del context_providers
        return ModelTurnRequest(
            session_id=session_slice.session_id,
            messages=[message.to_dict() for message in session_slice.messages],
            available_tools=[tool.name for tool in self.tools.list_tools()],
        )

    def handle_model_output(self, output: ModelTurnResponse) -> ModelTurnResponse:
        return output

    def route_tool_call(self, tool_call: ToolCall) -> ToolResult:
        result = self.executor.run_tools(
            [tool_call],
            ToolExecutionContext(session_id="ad_hoc"),
        )
        return result[0]

    def _append_tool_results(self, session: SessionRecord, tool_results: list[ToolResult]) -> None:
        for result in tool_results:
            session.messages.append(
                SessionMessage(
                    role="tool",
                    content=f"{result.tool_name}: {result.content}",
                )
            )

    def _append_event(self, session: SessionRecord, event: RuntimeEvent) -> RuntimeEvent:
        session.events.append(event)
        return event

    def _ensure_tool_call_ids(self, tool_calls: list[ToolCall]) -> None:
        for index, tool_call in enumerate(tool_calls, start=1):
            if tool_call.call_id is None:
                tool_call.call_id = f"toolu_{index}"

    def _tool_call_payload(self, tool_call: ToolCall) -> JsonObject:
        payload = tool_call.to_dict()
        tool_use_id = payload.pop("call_id", None)
        if tool_use_id is not None:
            payload["tool_use_id"] = tool_use_id
        return payload

    def _tool_result_payload(self, result: ToolResult) -> JsonObject:
        payload = result.to_dict()
        metadata = payload.get("metadata")
        if isinstance(metadata, dict) and "tool_use_id" in metadata:
            payload["tool_use_id"] = metadata["tool_use_id"]
        return payload

    def _requires_action_payload(self, requires_action: object) -> JsonObject:
        if not hasattr(requires_action, "to_dict"):
            raise TypeError("requires_action payload must support to_dict()")
        payload = cast(JsonObject, requires_action.to_dict())
        request_id = payload.get("request_id")
        if request_id is not None:
            payload["tool_use_id"] = request_id
        return payload

    def _new_event(
        self,
        session_id: str,
        event_type: RuntimeEventType,
        payload: JsonObject,
    ) -> RuntimeEvent:
        timestamp = datetime.now(UTC).isoformat()
        event_id = f"{event_type.value}:{len(self.sessions.load_session(session_id).events) + 1}"
        return RuntimeEvent(
            event_type=event_type,
            event_id=event_id,
            timestamp=timestamp,
            session_id=session_id,
            payload=payload,
        )
