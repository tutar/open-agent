"""Concrete local harness runtime facade."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import cast

from openagent.durable_memory import DurableWritePath, MemoryConsolidationJob, MemoryStore
from openagent.durable_memory.dreaming import DreamingConfig, DreamingScheduler
from openagent.harness.context_engineering import (
    BootstrapPromptAssembler,
    ContextAssemblyInput,
    ContextAssemblyPipeline,
    ContextGovernance,
    ContextReport,
    InstructionMarkdownLoader,
    StructuredContext,
    build_startup_contexts,
    default_workspace_root_from_metadata,
)
from openagent.harness.interfaces import Harness
from openagent.harness.runtime.core.pipeline import (
    append_event,
    new_event,
    requires_action_payload,
    tool_call_payload,
    tool_result_payload,
)
from openagent.harness.runtime.core.terminal import (
    AgentRuntime,
    CancelledTurn,
    RetryExhaustedTurn,
    TimedOutTurn,
    TurnControl,
)
from openagent.harness.runtime.hooks.runtime import HookRuntime
from openagent.harness.runtime.io import (
    FileModelIoCapture,
    ModelIoCapture,
    ModelProviderAdapter,
    ModelProviderExchange,
    ModelProviderExchangeAdapter,
    ModelProviderStreamingAdapter,
    ModelTurnRequest,
    ModelTurnResponse,
    NoOpModelIoCapture,
)
from openagent.harness.runtime.post_turn.processing import (
    MemoryMaintenanceProcessor,
    PostTurnContext,
    PostTurnRegistry,
)
from openagent.harness.runtime.projection.observability import RuntimeObservabilityProjection
from openagent.harness.runtime.projection.state_projection import terminal_state_from_event
from openagent.object_model import (
    JsonObject,
    JsonValue,
    RuntimeEvent,
    RuntimeEventType,
    TerminalState,
    ToolResult,
)
from openagent.observability import (
    AgentObservability,
    ProgressUpdate,
    RuntimeMetric,
    SpanHandle,
)
from openagent.observability.metrics import (
    normalized_duration_metrics,
    normalized_token_usage_metrics,
)
from openagent.session import (
    SessionMessage,
    SessionRecord,
    SessionStatus,
    SessionStore,
)
from openagent.session.short_term_memory import ShortTermMemoryStore, ShortTermSessionMemory
from openagent.shared import (
    DEFAULT_AGENT_DIRECTORY,
    DEFAULT_RUNTIME_AGENT_ID,
    ensure_agent_workspace,
    ensure_subagent_workspace,
    write_subagent_ref,
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
class SimpleHarness(Harness):
    """Run a local turn against an injected model adapter."""

    model: ModelProviderAdapter
    sessions: SessionStore
    tools: ToolRegistry
    executor: ToolExecutor
    max_iterations: int = 8
    context_governance: ContextGovernance | None = None
    last_context_report: ContextReport | None = None
    memory_store: MemoryStore | None = None
    short_term_memory_store: ShortTermMemoryStore | None = None
    dreaming_config: DreamingConfig = field(default_factory=DreamingConfig)
    last_memory_consolidation_job_id: str | None = None
    last_dreaming_job_id: str | None = None
    observability: AgentObservability | None = None
    model_io_capture: ModelIoCapture = field(default_factory=NoOpModelIoCapture)
    bootstrap_prompts: BootstrapPromptAssembler = field(default_factory=BootstrapPromptAssembler)
    context_pipeline: ContextAssemblyPipeline = field(default_factory=ContextAssemblyPipeline)
    instruction_markdown_loader: InstructionMarkdownLoader = field(
        default_factory=InstructionMarkdownLoader
    )
    session_root_dir: str | None = None
    openagent_root: str | None = None
    agent_root_dir: str | None = None
    role_id: str | None = None
    runtime_loop: AgentRuntime = field(init=False, repr=False)
    dreaming_scheduler: DreamingScheduler = field(init=False, repr=False)
    _last_memory_session: SessionRecord | None = field(default=None, init=False, repr=False)
    hook_runtime: HookRuntime = field(default_factory=HookRuntime)
    post_turn_registry: PostTurnRegistry = field(default_factory=PostTurnRegistry)
    projection: RuntimeObservabilityProjection = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.observability is None:
            self.observability = AgentObservability()
        if hasattr(self.executor, "set_observability"):
            self.executor.set_observability(self.observability)
        from openagent.harness.runtime.core.ralph_loop import RalphLoop

        self.projection = RuntimeObservabilityProjection(self.observability)
        self.post_turn_registry.register(MemoryMaintenanceProcessor())
        self.dreaming_scheduler = DreamingScheduler(self.dreaming_config)
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
        return events, terminal_state_from_event(events[-1])

    def continue_turn(
        self,
        session_handle: str,
        approved: bool,
    ) -> tuple[list[RuntimeEvent], TerminalState]:
        return self.runtime_loop.continue_turn(session_handle, approved)

    @property
    def parent_agent_ref(self) -> str:
        if isinstance(self.role_id, str) and self.role_id.strip():
            return f"agent_{self.role_id.strip()}"
        return DEFAULT_AGENT_DIRECTORY

    def build_model_input(
        self,
        session_slice: SessionRecord,
        context_providers: list[object],
    ) -> ModelTurnRequest:
        compacted = False
        recovered_from_overflow = False
        available_tools = [tool.name for tool in self.tools.list_tools()]
        if self.context_governance is not None and self.context_governance.should_compact(
            session_slice.messages
        ):
            compact_result = self.context_governance.compact(session_slice.messages)
            messages = self._normalize_message_payloads(compact_result.messages)
            compacted = compact_result.compacted_count > 0
            self.hook_runtime.execute_hooks(
                scope="runtime",
                event="post_compact",
                payload={"session_id": session_slice.session_id},
            )
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
                self.hook_runtime.execute_hooks(
                    scope="runtime",
                    event="pre_compact",
                    payload={"session_id": session_slice.session_id},
                )
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
                            task_id=self._current_task_id(),
                            summary="context_governance_report",
                            last_activity="context_report",
                            attributes=self.last_context_report.to_dict(),
                        )
                    )
        memory_context: list[JsonObject] = []
        if (
            self.memory_store is not None
            and self.memory_store.is_enabled()
            and session_slice.messages
        ):
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
        runtime_state: JsonObject = {
            "session_id": session_slice.session_id,
            "agent_id": session_slice.agent_id,
            "target_path": (
                session_slice.metadata.get("target_path")
                if isinstance(session_slice.metadata, dict)
                else None
            ),
        }
        instruction_documents = self.instruction_markdown_loader.load(
            workspace_root=default_workspace_root_from_metadata(session_slice.metadata),
            runtime_state=runtime_state,
        )
        system_context = self._instruction_system_context(instruction_documents)
        user_context = self._context_provider_fragments(
            context_providers=context_providers,
            method_name="user_context",
        )
        attachments = self._context_provider_fragments(
            context_providers=context_providers,
            method_name="attachments",
        )
        evidence_refs = self._context_provider_fragments(
            context_providers=context_providers,
            method_name="evidence_refs",
        )
        startup_contexts = [
            context.to_dict()
            for context in build_startup_contexts(
                session_id=session_slice.session_id,
                has_prior_messages=len(session_slice.messages) > 1,
                has_pending_action=bool(session_slice.pending_tool_calls),
                agent_id=session_slice.agent_id,
            )
        ]
        capability_surface = self._capability_surface_payload(available_tools, context_providers)
        assembly_result = self.context_pipeline.assemble(
            ContextAssemblyInput(
                transcript=messages,
                bootstrap_prompt_sections=[
                    section.to_dict() for section in prompt_sections.sections
                ],
                system_context=system_context,
                user_context=user_context,
                attachments=attachments,
                capability_surface=capability_surface,
                evidence_refs=evidence_refs,
                request_metadata={
                    "session_id": session_slice.session_id,
                    "agent_id": session_slice.agent_id,
                    "compacted": compacted,
                    "recovered_from_overflow": recovered_from_overflow,
                },
                startup_contexts=startup_contexts,
            )
        )
        return ModelTurnRequest(
            session_id=session_slice.session_id,
            messages=assembly_result.message_stream,
            system_prompt=assembly_result.system_prompt,
            prompt_sections=assembly_result.prompt_sections,
            prompt_blocks=assembly_result.prompt_blocks,
            startup_contexts=assembly_result.startup_contexts,
            available_tools=available_tools,
            tool_definitions=tool_definitions,
            short_term_memory=(
                short_term_memory.to_dict() if short_term_memory is not None else None
            ),
            memory_context=memory_context,
            system_context=assembly_result.system_context,
            user_context=assembly_result.user_context,
            attachments=assembly_result.attachment_stream,
            capability_surface=assembly_result.capability_surface,
            evidence_refs=assembly_result.evidence_refs,
            request_metadata=assembly_result.request_metadata,
        )

    def handle_model_output(self, output: ModelTurnResponse) -> ModelTurnResponse:
        return output

    def route_tool_call(self, tool_call: ToolCall) -> ToolResult:
        del tool_call
        raise RuntimeError(
            "ad-hoc tool routing without a session workspace is not supported; "
            "use a session-backed turn"
        )

    def ensure_session_workspace(self, session_handle: str, session: SessionRecord) -> str:
        metadata = dict(session.metadata or {})
        existing = metadata.get("workdir")
        if isinstance(existing, str) and existing:
            workdir = str(Path(existing).resolve())
            Path(workdir).mkdir(parents=True, exist_ok=True)
        elif self.agent_root_dir is not None:
            agent_id = session.agent_id or DEFAULT_RUNTIME_AGENT_ID
            if session.agent_id is None:
                session.agent_id = agent_id
            workdir = ensure_agent_workspace(self.agent_root_dir, agent_id)
            metadata["workdir"] = workdir
            metadata.setdefault("agent_root_dir", str(Path(self.agent_root_dir).resolve()))
            if self.role_id is not None:
                metadata.setdefault("role_id", self.role_id)
            session.metadata = metadata
        else:
            store_root = getattr(self.sessions, "root_dir", None)
            if isinstance(store_root, Path):
                workdir = str((store_root / session_handle / "workspace").resolve())
                Path(workdir).mkdir(parents=True, exist_ok=True)
                metadata["workdir"] = workdir
                session.metadata = metadata
            else:
                raise RuntimeError("agent_root_dir is required to resolve an agent workspace")
        return workdir

    def prepare_delegated_agent_workspace(
        self,
        delegated_agent_id: str,
        parent_session_id: str | None,
        metadata: JsonObject | None = None,
    ) -> str:
        if self.agent_root_dir is None:
            raise RuntimeError("agent_root_dir is required to resolve a delegated workspace")
        parent_workspace: str | None = None
        parent_session: SessionRecord | None = None
        if parent_session_id is not None:
            parent_session = self.sessions.load_session(parent_session_id)
            if isinstance(parent_session, SessionRecord):
                parent_workspace = self.ensure_session_workspace(parent_session_id, parent_session)
                self._persist_session(parent_session_id, parent_session)
        workspace = ensure_subagent_workspace(
            self.agent_root_dir,
            delegated_agent_id,
            parent_workspace=parent_workspace,
        )
        write_subagent_ref(
            self.agent_root_dir,
            delegated_agent_id,
            parent_session_id=parent_session_id,
            workspace=workspace,
            metadata=dict(metadata) if metadata is not None else None,
            parent_agent_id=(
                parent_session.agent_id
                if parent_session_id is not None
                and isinstance(parent_session, SessionRecord)
                and parent_session.agent_id is not None
                else DEFAULT_RUNTIME_AGENT_ID
            ),
        )
        return workspace

    def _new_session_message(self, role: str, content: str) -> SessionMessage:
        return SessionMessage(role=role, content=content)

    def schedule_memory_maintenance(self, session: SessionRecord) -> None:
        self._last_memory_session = session
        if self.short_term_memory_store is not None:
            current_memory = self.short_term_memory_store.load(session.session_id)
            update = self.short_term_memory_store.update(
                session.session_id,
                list(session.messages),
                current_memory,
            )
            if update.memory is not None:
                session.short_term_memory = update.memory.to_dict()
        if self.memory_store is not None and self.memory_store.is_enabled() and session.messages:
            job = self.memory_store.schedule(
                session.session_id,
                list(session.messages),
                agent_id=session.agent_id,
            )
            self.last_memory_consolidation_job_id = job.job_id

    def maybe_schedule_dreaming(self) -> MemoryConsolidationJob | None:
        session = self._last_memory_session
        if (
            session is None
            or self.memory_store is None
            or not self.memory_store.is_enabled()
            or not session.messages
            or not self.dreaming_scheduler.should_run()
        ):
            return None
        job = self.memory_store.schedule(
            session.session_id,
            list(session.messages),
            agent_id=session.agent_id,
            write_path=DurableWritePath.DREAM,
            dreaming_config=self.dreaming_config,
        )
        self.dreaming_scheduler.mark_scheduled()
        self.last_dreaming_job_id = job.job_id
        return job

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

    def _run_post_turn_processors(self, session_handle: str, session: SessionRecord) -> None:
        self.post_turn_registry.execute_all(
            PostTurnContext(
                session_handle=session_handle,
                session=session,
                runtime=self,
            )
        )

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

    def _instruction_system_context(
        self,
        documents: object,
    ) -> list[JsonObject]:
        merged_lines: list[str] = []
        keyed_lines: dict[str, str] = {}
        keyed_order: list[str] = []
        source_paths: list[str] = []
        if not isinstance(documents, list):
            return []
        for document in documents:
            source_path = getattr(document, "source_path", None)
            if isinstance(source_path, str):
                source_paths.append(source_path)
            rules = getattr(document, "rules", None)
            if not isinstance(rules, list):
                continue
            for rule in rules:
                text = getattr(rule, "text", None)
                if not isinstance(text, str):
                    continue
                for raw_line in text.splitlines():
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
                    if line not in merged_lines:
                        merged_lines.append(line)
        merged_content = "\n".join([*merged_lines, *(keyed_lines[key] for key in keyed_order)])
        if not merged_content:
            return []
        structured = StructuredContext(
            scope="system",
            lifecycle="startup",
            payload={
                "content": merged_content,
                "source_paths": [path for path in source_paths],
            },
            provenance="instruction_markdown",
        )
        return [structured.to_dict()]

    def _context_provider_fragments(
        self,
        *,
        context_providers: list[object],
        method_name: str,
    ) -> list[JsonObject]:
        fragments: list[JsonObject] = []
        for provider in context_providers:
            method = getattr(provider, method_name, None)
            if not callable(method):
                continue
            produced = method()
            if not isinstance(produced, list):
                continue
            for item in produced:
                if isinstance(item, dict):
                    fragments.append(item)
                elif hasattr(item, "to_dict"):
                    fragments.append(cast(JsonObject, item.to_dict()))
        return fragments

    def _capability_surface_payload(
        self,
        available_tools: list[str],
        context_providers: list[object],
    ) -> JsonObject:
        always_loaded = list(available_tools)
        for provider in context_providers:
            method = getattr(provider, "capability_exposure", None)
            if not callable(method):
                continue
            produced = method()
            if hasattr(produced, "to_dict"):
                payload = cast(JsonObject, produced.to_dict())
            elif isinstance(produced, dict):
                payload = produced
            else:
                continue
            provider_always = payload.get("always_loaded")
            if isinstance(provider_always, list):
                always_loaded.extend(str(item) for item in provider_always)
        return {
            "always_loaded": [item for item in sorted(set(always_loaded))],
        }

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
        self.hook_runtime.execute_hooks(
            scope="runtime",
            event="pre_tool",
            payload={"session_id": session_handle, "tool_count": len(tool_calls)},
        )
        for event in self.executor.run_tool_stream(tool_calls, context):
            emitted = self._append_event(session, event)
            emitted_events.append(emitted)
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
                self.hook_runtime.execute_hooks(
                    scope="runtime",
                    event="post_tool_failure",
                    payload={"session_id": session_handle, **event.payload},
                )
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
                        task_id=self._current_task_id(),
                        attributes={"retry_index": attempt},
                    )
                )
                self._record_turn_api_duration(duration_ms)
                for metric in normalized_duration_metrics(
                    scope="llm_request",
                    total_duration_ms=duration_ms,
                    total_api_duration_ms=duration_ms,
                    session_id=session_handle,
                    task_id=self._current_task_id(),
                    callsite=(
                        "turn.continuation_model_request"
                        if getattr(self.runtime_loop, "state", None) is not None
                        and getattr(self.runtime_loop.state, "requires_action", False)
                        else "turn.model_request"
                    ),
                    model=str(getattr(self.model, "model", type(self.model).__name__)),
                    provider_adapter=type(self.model).__name__,
                    api_kind="llm_provider",
                    api_target=str(getattr(self.model, "model", type(self.model).__name__)),
                    extra_attributes={"retry_index": attempt},
                ):
                    self._emit_metric(metric)
                for metric in normalized_token_usage_metrics(
                    scope="llm_request",
                    session_id=session_handle,
                    task_id=self._current_task_id(),
                    callsite=(
                        "turn.continuation_model_request"
                        if getattr(self.runtime_loop, "state", None) is not None
                        and getattr(self.runtime_loop.state, "requires_action", False)
                        else "turn.model_request"
                    ),
                    model=str(getattr(self.model, "model", type(self.model).__name__)),
                    provider_adapter=type(self.model).__name__,
                    input_tokens=self._usage_value(response.usage, "input_tokens", "prompt_tokens"),
                    output_tokens=self._usage_value(
                        response.usage, "output_tokens", "completion_tokens"
                    ),
                    cache_creation_input_tokens=self._usage_value(
                        response.usage,
                        "cache_creation_input_tokens",
                    ),
                    cache_read_input_tokens=self._usage_value(
                        response.usage,
                        "cache_read_input_tokens",
                        "cached_tokens",
                    ),
                    extra_attributes={"retry_index": attempt},
                ):
                    self._emit_metric(metric)
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
                    cache_creation_input_tokens=self._usage_value(
                        response.usage,
                        "cache_creation_input_tokens",
                    ),
                    cache_read_input_tokens=self._usage_value(
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
                print(
                    "openagent-runtime> provider request failed "
                    f"session_id={session_handle} "
                    f"provider_adapter={type(self.model).__name__} "
                    f"retry_index={attempt} "
                    f"error={type(exc).__name__}: {exc}",
                    flush=True,
                )
                if isinstance(self.model_io_capture, FileModelIoCapture):
                    print(
                        "openagent-runtime> provider failure captured under "
                        f"{self.model_io_capture.root_dir}",
                        flush=True,
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
        final_provider_payload: JsonObject | None = None
        raw_provider_events: list[JsonObject] = []
        final_reasoning: JsonValue | None = None
        final_transport_metadata: JsonObject = {"streaming": True}
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
            if stream_event.provider_payload is not None:
                final_provider_payload = dict(stream_event.provider_payload)
            if stream_event.raw_provider_events:
                raw_provider_events = [dict(event) for event in stream_event.raw_provider_events]
            if stream_event.reasoning is not None:
                final_reasoning = stream_event.reasoning
            if stream_event.transport_metadata:
                final_transport_metadata = dict(stream_event.transport_metadata)
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
                provider_payload=final_provider_payload,
                raw_response=(
                    {"events": raw_provider_events, "usage": dict(final_usage)}
                    if raw_provider_events and final_usage is not None
                    else {"events": raw_provider_events}
                    if raw_provider_events
                    else None
                ),
                reasoning=final_reasoning,
                transport_metadata=final_transport_metadata,
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
        return append_event(session, event)

    def _persist_session(self, session_handle: str, session: SessionRecord) -> None:
        self.sessions.save_session(session_handle, session)

    def _ensure_tool_call_ids(self, tool_calls: list[ToolCall]) -> None:
        for index, tool_call in enumerate(tool_calls, start=1):
            if tool_call.call_id is None:
                tool_call.call_id = f"toolu_{index}"

    def _tool_call_payload(self, tool_call: ToolCall) -> JsonObject:
        return tool_call_payload(tool_call)

    def _tool_result_payload(self, result: ToolResult) -> JsonObject:
        return tool_result_payload(result)

    def _requires_action_payload(self, requires_action: object) -> JsonObject:
        return requires_action_payload(requires_action)

    def _new_event(
        self,
        session_id: str,
        event_type: RuntimeEventType,
        payload: JsonObject,
    ) -> RuntimeEvent:
        event_index = len(self.sessions.load_session(session_id).events) + 1
        event = new_event(
            session_id=session_id,
            event_type=event_type,
            payload=payload,
            event_index=event_index,
        )
        event.task_id = self._current_task_id()
        return event

    def _current_task_id(self) -> str | None:
        runtime_state = getattr(self.runtime_loop, "state", None)
        task_id = getattr(runtime_state, "task_id", None)
        return task_id if isinstance(task_id, str) and task_id else None

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
        self._run_post_turn_processors(session_handle, session)
        self._persist_session(session_handle, session)
        return event

    def _check_cancelled(self, control: TurnControl) -> bool:
        return control.cancellation_check is not None and control.cancellation_check()

    def _emit_metric(self, metric: RuntimeMetric) -> None:
        if metric.task_id is None:
            metric.task_id = self._current_task_id()
        self.projection.emit_metric(metric)
        if metric.name == "turn.duration_ms" and isinstance(metric.value, (int, float)):
            state = getattr(self.runtime_loop, "state", None)
            api_duration_ms = (
                state.api_duration_ms
                if state is not None and state.task_id == metric.task_id
                else 0.0
            )
            for normalized in normalized_duration_metrics(
                scope="turn",
                total_duration_ms=float(metric.value),
                total_api_duration_ms=api_duration_ms,
                session_id=metric.session_id,
                task_id=metric.task_id,
                agent_id=metric.agent_id,
                aggregation="terminal",
                extra_attributes=dict(metric.attributes),
            ):
                self.projection.emit_metric(normalized)

    def _emit_progress(self, progress: ProgressUpdate) -> None:
        if progress.task_id is None:
            progress.task_id = self._current_task_id()
        self.projection.emit_progress(progress)

    def _emit_session_state(
        self,
        session_id: str,
        state: str,
        reason: str | None = None,
        attributes: JsonObject | None = None,
    ) -> None:
        self.projection.emit_session_state(
            session_id=session_id,
            state=state,
            reason=reason,
            attributes=attributes,
        )

    def _record_turn_api_duration(self, duration_ms: float) -> None:
        state = getattr(self.runtime_loop, "state", None)
        if state is None:
            return
        state.api_duration_ms += duration_ms

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


__all__ = [
    "FileModelIoCapture",
    "ModelIoCapture",
    "NoOpModelIoCapture",
    "SimpleHarness",
]
