"""Minimal harness baseline for local testing and spec prototyping."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import cast

from openagent.harness.bootstrap import (
    BootstrapPromptAssembler,
    InitialUserBootstrap,
    default_workspace_root_from_metadata,
)
from openagent.harness.context import ContextGovernance, ContextReport
from openagent.harness.model_io import ModelIoCapture, NoOpModelIoCapture
from openagent.harness.models import (
    AgentRuntime,
    CancelledTurn,
    ModelProviderAdapter,
    ModelProviderExchange,
    ModelProviderExchangeAdapter,
    ModelProviderStreamingAdapter,
    ModelTurnRequest,
    ModelTurnResponse,
    RetryExhaustedTurn,
    TimedOutTurn,
    TurnControl,
)
from openagent.harness.runtime import RalphLoop
from openagent.object_model import (
    JsonObject,
    RuntimeEvent,
    RuntimeEventType,
    TerminalState,
    TerminalStatus,
    ToolResult,
)
from openagent.observability import (
    AgentObservability,
    ProgressUpdate,
    RuntimeMetric,
    SessionStateSignal,
    SpanHandle,
)
from openagent.session import (
    MemoryStore,
    SessionMessage,
    SessionRecord,
    SessionStatus,
    SessionStore,
    ShortTermMemoryStore,
    ShortTermSessionMemory,
)
from openagent.tools import (
    ToolCall,
    ToolCancelledError,
    ToolExecutionContext,
    ToolExecutionFailedError,
    ToolExecutor,
    ToolRegistry,
)


@dataclass(slots=True)
class SimpleHarness:
    """Run a local turn against an injected model adapter.

    The harness remains local-first and synchronous by default, while exposing a
    stream-oriented turn path so frontends can consume deltas and intermediate
    runtime events in order.
    """

    model: ModelProviderAdapter
    sessions: SessionStore
    tools: ToolRegistry
    executor: ToolExecutor
    max_iterations: int = 8
    context_governance: ContextGovernance | None = None
    last_context_report: ContextReport | None = None
    memory_store: MemoryStore | None = None
    short_term_memory_store: ShortTermMemoryStore | None = None
    last_memory_consolidation_job_id: str | None = None
    observability: AgentObservability | None = None
    model_io_capture: ModelIoCapture = field(default_factory=NoOpModelIoCapture)
    bootstrap_prompts: BootstrapPromptAssembler = field(default_factory=BootstrapPromptAssembler)
    runtime_loop: AgentRuntime = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # Keep the loop explicit and spec-aligned while preserving the existing
        # facade API that tests and frontends already use.
        if self.observability is None:
            self.observability = AgentObservability()
        if hasattr(self.executor, "set_observability"):
            self.executor.set_observability(self.observability)
        self.runtime_loop = RalphLoop(self)

    def run_turn_stream(
        self,
        input: str,
        session_handle: str,
        control: TurnControl | None = None,
    ) -> Iterator[RuntimeEvent]:
        yield from self.runtime_loop.run_turn_stream(input, session_handle, control=control)

    def run_turn(
        self,
        input: str,
        session_handle: str,
        control: TurnControl | None = None,
    ) -> tuple[list[RuntimeEvent], TerminalState]:
        events = list(self.run_turn_stream(input, session_handle, control=control))
        return events, self._terminal_state_from_event(events[-1])

    def continue_turn(
        self,
        session_handle: str,
        approved: bool,
    ) -> tuple[list[RuntimeEvent], TerminalState]:
        return self.runtime_loop.continue_turn(session_handle, approved)

    def build_model_input(
        self,
        session_slice: SessionRecord,
        context_providers: list[object],
    ) -> ModelTurnRequest:
        del context_providers
        compacted = False
        recovered_from_overflow = False
        available_tools = [tool.name for tool in self.tools.list_tools()]
        if self.context_governance is not None and self.context_governance.should_compact(
            session_slice.messages
        ):
            compact_result = self.context_governance.compact(session_slice.messages)
            messages = self._normalize_message_payloads(compact_result.messages)
            compacted = compact_result.compacted_count > 0
            if self.context_governance.analyze(session_slice.messages, available_tools).over_budget:
                recovery_result = self.context_governance.recover_overflow(session_slice.messages)
                if recovery_result.recovered:
                    messages = self._normalize_message_payloads(recovery_result.messages)
                    recovered_from_overflow = True
        else:
            messages = [self._message_payload(message) for message in session_slice.messages]
        if self.context_governance is not None:
            self.last_context_report = self.context_governance.report_for_model_input(
                session_slice.messages,
                available_tools,
                compacted=compacted,
                recovered_from_overflow=recovered_from_overflow,
            )
            if not self.context_governance.should_allow_continuation(
                session_slice.messages,
                available_tools,
            ):
                overflow_result = self.context_governance.recover_overflow(session_slice.messages)
                if overflow_result.recovered:
                    messages = overflow_result.messages
                    messages = self._normalize_message_payloads(messages)
                    recovered_from_overflow = True
                    self.last_context_report = self.context_governance.report_for_model_input(
                        session_slice.messages,
                        available_tools,
                        compacted=compacted,
                        recovered_from_overflow=recovered_from_overflow,
                    )
                    self._emit_progress(
                        ProgressUpdate(
                            scope="turn",
                            session_id=session_slice.session_id,
                            summary="context_governance_report",
                            last_activity="context_report",
                            attributes=self.last_context_report.to_dict(),
                        )
                    )
        memory_context = self._load_agents_memory_context(session_slice)
        if self.memory_store is not None and session_slice.messages:
            latest_query = session_slice.messages[-1].content
            recall_result = self.memory_store.recall(
                session_slice.session_id,
                latest_query,
                agent_id=session_slice.agent_id,
            )
            memory_context.extend(record.to_dict() for record in recall_result.recalled)
        tool_definitions: list[JsonObject] = [
            {
                "name": tool.name,
                "description": tool.description(),
                "input_schema": tool.input_schema,
            }
            for tool in self.tools.list_tools()
        ]
        short_term_memory = self._load_short_term_memory(session_slice)
        prompt_sections = self.bootstrap_prompts.resolve_sections(
            self.bootstrap_prompts.merge_prompt_layers(
                self.bootstrap_prompts.build_default_prompt(
                    runtime_capabilities=available_tools,
                    model_view={
                        "workspace_root": default_workspace_root_from_metadata(
                            session_slice.metadata
                        ),
                        "session_id": session_slice.session_id,
                    },
                )
            )
        )
        prompt_blocks = self.bootstrap_prompts.split_static_dynamic(prompt_sections)
        system_prompt = "\n\n".join(
            [*prompt_blocks.static_blocks, *prompt_blocks.dynamic_blocks]
        ).strip() or None
        initial_user_bootstrap = InitialUserBootstrap()
        return ModelTurnRequest(
            session_id=session_slice.session_id,
            messages=messages,
            system_prompt=system_prompt,
            prompt_sections=[section.to_dict() for section in prompt_sections.sections],
            prompt_blocks=prompt_blocks.to_dict(),
            initial_user_bootstrap=initial_user_bootstrap.to_dict(),
            available_tools=available_tools,
            tool_definitions=tool_definitions,
            short_term_memory=(
                short_term_memory.to_dict() if short_term_memory is not None else None
            ),
            memory_context=memory_context,
        )

    def handle_model_output(self, output: ModelTurnResponse) -> ModelTurnResponse:
        return output

    def route_tool_call(self, tool_call: ToolCall) -> ToolResult:
        result = self.executor.run_tools(
            [tool_call],
            ToolExecutionContext(session_id="ad_hoc"),
        )
        return result[0]

    def _new_session_message(self, role: str, content: str) -> SessionMessage:
        return SessionMessage(role=role, content=content)

    def schedule_memory_maintenance(self, session: SessionRecord) -> None:
        if self.short_term_memory_store is not None:
            current_memory = self.short_term_memory_store.load(session.session_id)
            update = self.short_term_memory_store.update(
                session.session_id,
                list(session.messages),
                current_memory,
            )
            if update.memory is not None:
                session.short_term_memory = update.memory.to_dict()
        if self.memory_store is not None and session.messages:
            job = self.memory_store.schedule(
                session.session_id,
                list(session.messages),
                agent_id=session.agent_id,
            )
            self.last_memory_consolidation_job_id = job.job_id

    def stabilize_short_term_memory(
        self,
        session: SessionRecord,
        timeout_ms: int = 250,
    ) -> None:
        if self.short_term_memory_store is None:
            return
        memory = self.short_term_memory_store.wait_until_stable(session.session_id, timeout_ms)
        if memory is not None:
            session.short_term_memory = memory.to_dict()

    def _load_short_term_memory(
        self,
        session: SessionRecord,
    ) -> ShortTermSessionMemory | None:
        if self.short_term_memory_store is not None:
            stable_memory = self.short_term_memory_store.wait_until_stable(session.session_id, 50)
            if stable_memory is not None:
                session.short_term_memory = stable_memory.to_dict()
                return stable_memory
            loaded = self.short_term_memory_store.load(session.session_id)
            if loaded is not None:
                session.short_term_memory = loaded.to_dict()
                return loaded
        return None

    def _message_payload(self, message: SessionMessage) -> JsonObject:
        payload = message.to_dict()
        if payload.get("metadata") == {}:
            payload.pop("metadata", None)
        return payload

    def _normalize_message_payloads(self, messages: list[JsonObject]) -> list[JsonObject]:
        normalized: list[JsonObject] = []
        for message in messages:
            payload = dict(message)
            if payload.get("metadata") == {}:
                payload.pop("metadata", None)
            normalized.append(payload)
        return normalized

    def _load_agents_memory_context(self, session: SessionRecord) -> list[JsonObject]:
        documents = self._resolve_agents_documents(session)
        if not documents:
            return []
        merged_content = self._merge_agents_documents(documents)
        if not merged_content:
            return []
        return [
            {
                "type": "agents_memory",
                "scope": "agent",
                "title": "AGENTS.md context",
                "content": merged_content,
                "source": "AGENTS.md",
                "metadata": {
                    "paths": [str(path) for path, _ in documents],
                    "session_id": session.session_id,
                },
            }
        ]

    def _resolve_agents_documents(self, session: SessionRecord) -> list[tuple[Path, str]]:
        metadata = session.metadata if isinstance(session.metadata, dict) else {}
        workdir_value = metadata.get("workdir")
        target_path_value = metadata.get("target_path")
        documents: list[tuple[Path, str]] = []
        seen: set[Path] = set()

        def append_if_exists(path: Path) -> None:
            resolved = path.resolve()
            if resolved in seen or not resolved.exists() or not resolved.is_file():
                return
            text = resolved.read_text(encoding="utf-8").strip()
            if not text:
                return
            seen.add(resolved)
            documents.append((resolved, text))

        append_if_exists(Path.home() / ".openagent" / "AGENTS.md")
        workdir = (
            Path(str(workdir_value)).resolve()
            if isinstance(workdir_value, str) and workdir_value
            else None
        )
        if workdir is not None:
            append_if_exists(workdir / "AGENTS.md")
        if workdir is not None and isinstance(target_path_value, str) and target_path_value:
            target_path = Path(target_path_value)
            if not target_path.is_absolute():
                target_path = workdir / target_path
            target_path = target_path.resolve()
            if target_path.is_dir():
                target_dir = target_path
            else:
                target_dir = target_path.parent
            try:
                relative_parts = target_dir.relative_to(workdir).parts
            except ValueError:
                relative_parts = ()
            current = workdir
            for part in relative_parts:
                current = current / part
                append_if_exists(current / "AGENTS.md")
        return documents

    def _merge_agents_documents(self, documents: list[tuple[Path, str]]) -> str:
        ordered_lines: list[str] = []
        keyed_lines: dict[str, str] = {}
        keyed_order: list[str] = []
        for _, content in documents:
            for raw_line in content.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                if ":" in line:
                    key, value = line.split(":", 1)
                    normalized_key = key.strip().lower()
                    keyed_lines[normalized_key] = f"{key.strip()}: {value.strip()}"
                    if normalized_key not in keyed_order:
                        keyed_order.append(normalized_key)
                    continue
                if line not in ordered_lines:
                    ordered_lines.append(line)
        merged = [*ordered_lines, *(keyed_lines[key] for key in keyed_order)]
        return "\n".join(merged)

    def _execute_tool_stream(
        self,
        session: SessionRecord,
        session_handle: str,
        tool_calls: list[ToolCall],
        context: ToolExecutionContext,
    ) -> tuple[
        list[RuntimeEvent],
        list[ToolResult],
        ToolExecutionFailedError | ToolCancelledError | None,
    ]:
        emitted_events: list[RuntimeEvent] = []
        results: list[ToolResult] = []
        for event in self.executor.run_tool_stream(tool_calls, context):
            emitted_events.append(self._append_event(session, event))
            if event.event_type in {
                RuntimeEventType.TOOL_PROGRESS,
                RuntimeEventType.TOOL_RESULT,
                RuntimeEventType.TOOL_FAILED,
                RuntimeEventType.TOOL_CANCELLED,
            }:
                self._persist_session(session_handle, session)
            if event.event_type is RuntimeEventType.TOOL_PROGRESS:
                continue
            if event.event_type is RuntimeEventType.TOOL_RESULT:
                payload = dict(event.payload)
                payload.pop("tool_use_id", None)
                result = ToolResult.from_dict(payload)
                results.append(result)
                continue
            if event.event_type is RuntimeEventType.TOOL_FAILED:
                return emitted_events, results, ToolExecutionFailedError(
                    tool_name=str(event.payload.get("tool_name", "unknown")),
                    reason=str(event.payload.get("reason", "tool_failed")),
                )
            if event.event_type is RuntimeEventType.TOOL_CANCELLED:
                return emitted_events, results, ToolCancelledError(
                    tool_name=str(event.payload.get("tool_name", "unknown")),
                    reason=str(event.payload.get("reason", "cancelled")),
                )
        return emitted_events, results, None

    def _run_model_with_retries(
        self,
        request: ModelTurnRequest,
        session: SessionRecord,
        session_handle: str,
        control: TurnControl,
        parent_span: SpanHandle | None = None,
    ) -> tuple[ModelTurnResponse, list[RuntimeEvent]]:
        observability = self.observability
        assert observability is not None
        last_error: Exception | None = None
        for attempt in range(max(0, control.max_retries) + 1):
            if self._check_cancelled(control):
                raise CancelledTurn()
            llm_span = observability.start_span(
                "llm_request",
                {
                    "provider_adapter": type(self.model).__name__,
                    "model": str(getattr(self.model, "model", type(self.model).__name__)),
                    "retry_index": attempt,
                    "streaming": callable(getattr(self.model, "stream_generate", None)),
                },
                parent=parent_span,
                session_id=session_handle,
            )
            started_at = perf_counter()
            try:
                response, events, ttft_ms, exchange = self._run_model_once(
                    request=request,
                    session=session,
                    session_handle=session_handle,
                    control=control,
                )
                self._capture_model_io_success(
                    request=request,
                    session=session,
                    exchange=exchange,
                    retry_index=attempt,
                    streaming=callable(getattr(self.model, "stream_generate", None)),
                )
                duration_ms = (perf_counter() - started_at) * 1000
                self._emit_metric(
                    RuntimeMetric(
                        name="llm_request.duration_ms",
                        value=duration_ms,
                        unit="ms",
                        session_id=session_handle,
                        attributes={"retry_index": attempt},
                    )
                )
                observability.end_span(
                    llm_span,
                    {"retry_index": attempt},
                    status="completed",
                    duration_ms=duration_ms,
                    ttft_ms=ttft_ms,
                    input_tokens=self._usage_value(response.usage, "input_tokens", "prompt_tokens"),
                    output_tokens=self._usage_value(
                        response.usage,
                        "output_tokens",
                        "completion_tokens",
                    ),
                    cache_tokens=self._usage_value(
                        response.usage,
                        "cache_read_input_tokens",
                        "cached_tokens",
                    ),
                )
                return response, events
            except (CancelledTurn, TimedOutTurn):
                duration_ms = (perf_counter() - started_at) * 1000
                observability.end_span(
                    llm_span,
                    {"retry_index": attempt},
                    status="cancelled",
                    duration_ms=duration_ms,
                )
                raise
            except Exception as exc:
                last_error = exc
                self._capture_model_io_error(
                    request=request,
                    session=session,
                    retry_index=attempt,
                    streaming=callable(getattr(self.model, "stream_generate", None)),
                    error=exc,
                )
                duration_ms = (perf_counter() - started_at) * 1000
                observability.end_span(
                    llm_span,
                    {"retry_index": attempt, "error": str(exc)},
                    status="error",
                    duration_ms=duration_ms,
                )
                if attempt == control.max_retries:
                    raise RetryExhaustedTurn(str(exc)) from exc
        raise RetryExhaustedTurn(str(last_error))

    def _run_model_once(
        self,
        request: ModelTurnRequest,
        session: SessionRecord,
        session_handle: str,
        control: TurnControl,
    ) -> tuple[ModelTurnResponse, list[RuntimeEvent], float | None, ModelProviderExchange | None]:
        stream_generate = getattr(self.model, "stream_generate", None)
        if callable(stream_generate):
            streaming_model = cast(ModelProviderStreamingAdapter, self.model)
            return self._run_streaming_model(
                model=streaming_model,
                request=request,
                session=session,
                session_handle=session_handle,
                control=control,
            )
        return self._run_single_response_model(request=request, control=control)

    def _run_single_response_model(
        self,
        request: ModelTurnRequest,
        control: TurnControl,
    ) -> tuple[ModelTurnResponse, list[RuntimeEvent], float | None, ModelProviderExchange | None]:
        exchange_method = getattr(self.model, "generate_with_exchange", None)
        if callable(exchange_method):
            exchange = cast(
                ModelProviderExchangeAdapter,
                self.model,
            ).generate_with_exchange(request)
            return exchange.response, [], None, exchange
        if control.timeout_seconds is None:
            return self.model.generate(request), [], None, None
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.model.generate, request)
            try:
                return future.result(timeout=control.timeout_seconds), [], None, None
            except FutureTimeoutError as exc:
                raise TimedOutTurn() from exc

    def _run_streaming_model(
        self,
        model: ModelProviderStreamingAdapter,
        request: ModelTurnRequest,
        session: SessionRecord,
        session_handle: str,
        control: TurnControl,
    ) -> tuple[ModelTurnResponse, list[RuntimeEvent], float | None, ModelProviderExchange | None]:
        stream = model.stream_generate(request)
        aggregated_message = ""
        final_message: str | None = None
        final_tool_calls: list[ToolCall] = []
        streamed_events: list[RuntimeEvent] = []
        stream_started_at = perf_counter()
        ttft_ms: float | None = None
        stream_deltas: list[str] = []
        final_usage: JsonObject | None = None
        for stream_event in stream:
            if self._check_cancelled(control):
                raise CancelledTurn()
            if stream_event.assistant_delta:
                if ttft_ms is None:
                    ttft_ms = (perf_counter() - stream_started_at) * 1000
                aggregated_message += stream_event.assistant_delta
                stream_deltas.append(stream_event.assistant_delta)
                runtime_event = self._append_event(
                    session,
                    self._new_event(
                        session_id=session_handle,
                        event_type=RuntimeEventType.ASSISTANT_DELTA,
                        payload={"delta": stream_event.assistant_delta},
                    ),
                )
                self._persist_session(session_handle, session)
                streamed_events.append(runtime_event)
            if stream_event.assistant_message is not None:
                final_message = stream_event.assistant_message
            if stream_event.tool_calls:
                final_tool_calls = stream_event.tool_calls
            if stream_event.usage is not None:
                final_usage = dict(stream_event.usage)
        assistant_message = (
            final_message if final_message is not None else aggregated_message or None
        )
        response = ModelTurnResponse(
            assistant_message=assistant_message,
            tool_calls=final_tool_calls,
            usage=final_usage,
        )
        return (
            response,
            streamed_events,
            ttft_ms,
            ModelProviderExchange(
                response=response,
                transport_metadata={"streaming": True},
                stream_deltas=stream_deltas,
            ),
        )

    def _append_tool_results(self, session: SessionRecord, tool_results: list[ToolResult]) -> None:
        for result in tool_results:
            if self.context_governance is not None:
                result = self.context_governance.externalize_tool_result(result)
                message_content = self.context_governance.tool_result_message_content(result)
            else:
                message_content = f"{result.tool_name}: {result.content}"
            session.messages.append(
                SessionMessage(
                    role="tool",
                    content=message_content,
                    metadata=dict(result.metadata or {}),
                )
            )

    def _append_event(self, session: SessionRecord, event: RuntimeEvent) -> RuntimeEvent:
        session.events.append(event)
        return event

    def _persist_session(self, session_handle: str, session: SessionRecord) -> None:
        self.sessions.save_session(session_handle, session)

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

    def _emit_terminal(
        self,
        session: SessionRecord,
        session_handle: str,
        event_type: RuntimeEventType,
        terminal: TerminalState,
    ) -> RuntimeEvent:
        event = self._append_event(
            session,
            self._new_event(
                session_id=session_handle,
                event_type=event_type,
                payload=terminal.to_dict(),
            ),
        )
        session.status = SessionStatus.IDLE
        self.schedule_memory_maintenance(session)
        self.stabilize_short_term_memory(session)
        self._persist_session(session_handle, session)
        return event

    def _check_cancelled(self, control: TurnControl) -> bool:
        return control.cancellation_check is not None and control.cancellation_check()

    def _emit_metric(self, metric: RuntimeMetric) -> None:
        observability = self.observability
        assert observability is not None
        observability.emit_runtime_metric(metric)

    def _emit_progress(self, progress: ProgressUpdate) -> None:
        observability = self.observability
        assert observability is not None
        observability.emit_progress(progress)

    def _emit_session_state(
        self,
        session_id: str,
        state: str,
        reason: str | None = None,
        attributes: JsonObject | None = None,
    ) -> None:
        observability = self.observability
        assert observability is not None
        observability.emit_session_state(
            SessionStateSignal(
                session_id=session_id,
                state=state,
                reason=reason,
                attributes=dict(attributes or {}),
            )
        )

    def _terminal_state_from_event(self, event: RuntimeEvent) -> TerminalState:
        if event.event_type is RuntimeEventType.TURN_COMPLETED:
            return TerminalState.from_dict(event.payload)
        if event.event_type is RuntimeEventType.TURN_FAILED:
            return TerminalState.from_dict(event.payload)
        if event.event_type is RuntimeEventType.REQUIRES_ACTION:
            summary = str(event.payload.get("description", "requires action"))
            return TerminalState(
                status=TerminalStatus.BLOCKED,
                reason="requires_action",
                summary=summary,
            )
        raise ValueError("Event stream did not terminate with a terminal event")

    def _usage_value(self, usage: JsonObject | None, *keys: str) -> int | None:
        if usage is None:
            return None
        for key in keys:
            value = usage.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, dict):
                nested_cached = value.get("cached_tokens")
                if isinstance(nested_cached, int):
                    return nested_cached
        prompt_details = usage.get("prompt_tokens_details")
        if isinstance(prompt_details, dict):
            cached = prompt_details.get("cached_tokens")
            if isinstance(cached, int):
                return cached
        return None

    def _capture_model_io_success(
        self,
        *,
        request: ModelTurnRequest,
        session: SessionRecord,
        exchange: ModelProviderExchange | None,
        retry_index: int,
        streaming: bool,
    ) -> None:
        lease = self.sessions.get_active_lease(session.session_id)
        self.model_io_capture.capture_success(
            request=request,
            exchange=exchange,
            session_id=session.session_id,
            agent_id=session.agent_id,
            harness_instance_id=lease.harness_instance_id if lease is not None else None,
            provider_adapter=type(self.model).__name__,
            provider_family=self._provider_family(),
            model=str(getattr(self.model, "model", "")) or None,
            retry_index=retry_index,
            streaming=streaming,
        )

    def _capture_model_io_error(
        self,
        *,
        request: ModelTurnRequest,
        session: SessionRecord,
        retry_index: int,
        streaming: bool,
        error: Exception,
    ) -> None:
        lease = self.sessions.get_active_lease(session.session_id)
        self.model_io_capture.capture_error(
            request=request,
            session_id=session.session_id,
            agent_id=session.agent_id,
            harness_instance_id=lease.harness_instance_id if lease is not None else None,
            provider_adapter=type(self.model).__name__,
            provider_family=self._provider_family(),
            model=str(getattr(self.model, "model", "")) or None,
            retry_index=retry_index,
            streaming=streaming,
            error=error,
        )

    def _provider_family(self) -> str | None:
        family = getattr(self.model, "provider_family", None)
        return str(family) if family is not None else None
