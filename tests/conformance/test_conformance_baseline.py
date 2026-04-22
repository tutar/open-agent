from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openagent.gateway import ChannelIdentity, Gateway, InboundEnvelope, InProcessSessionAdapter
from openagent.harness.runtime import ModelTurnRequest, ModelTurnResponse, SimpleHarness
from openagent.harness.task import (
    BackgroundTaskContext,
    InMemoryTaskManager,
    LocalBackgroundAgentOrchestrator,
    TaskRetentionPolicy,
)
from openagent.object_model import JsonObject, RuntimeEventType, TerminalStatus, ToolResult
from openagent.sandbox import LocalSandbox, SandboxExecutionRequest
from openagent.session import (
    FileMemoryStore,
    FileSessionStore,
    InMemorySessionStore,
    SessionMessage,
    SessionRecord,
    SessionStatus,
)
from openagent.tools import PermissionDecision, SimpleToolExecutor, StaticToolRegistry, ToolCall


@dataclass(slots=True)
class FakeTool:
    name: str
    permission: PermissionDecision = PermissionDecision.ALLOW
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})

    def description(self) -> str:
        return self.name

    def call(self, arguments: dict[str, object]) -> ToolResult:
        text = arguments.get("text", "ok")
        return ToolResult(tool_name=self.name, success=True, content=[str(text)])

    def check_permissions(self, arguments: dict[str, object]) -> str:
        del arguments
        return self.permission.value

    def is_concurrency_safe(self) -> bool:
        return True


@dataclass(slots=True)
class ScriptedModel:
    responses: list[ModelTurnResponse]

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        del request
        return self.responses.pop(0)


def test_conformance_basic_turn() -> None:
    store = InMemorySessionStore()
    harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="hello")]),
        sessions=store,
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
    )

    events, terminal = harness.run_turn("hi", "case_basic")
    session = store.load_session("case_basic")

    assert terminal.status is TerminalStatus.COMPLETED
    assert [event.event_type for event in events] == [
        RuntimeEventType.TURN_STARTED,
        RuntimeEventType.ASSISTANT_MESSAGE,
        RuntimeEventType.TURN_COMPLETED,
    ]
    assert session.status is SessionStatus.IDLE
    assert [message.role for message in session.messages] == ["user", "assistant"]


def test_conformance_tool_call_roundtrip() -> None:
    tool = FakeTool(name="echo")
    registry = StaticToolRegistry([tool])
    harness = SimpleHarness(
        model=ScriptedModel(
            [
                ModelTurnResponse(tool_calls=[ToolCall(tool_name="echo", arguments={"text": "x"})]),
                ModelTurnResponse(assistant_message="done"),
            ]
        ),
        sessions=InMemorySessionStore(),
        tools=registry,
        executor=SimpleToolExecutor(registry),
    )

    events, terminal = harness.run_turn("use tool", "case_tool")

    assert terminal.status is TerminalStatus.COMPLETED
    assert RuntimeEventType.TURN_STARTED in [event.event_type for event in events]
    assert RuntimeEventType.TOOL_STARTED in [event.event_type for event in events]
    assert RuntimeEventType.TOOL_RESULT in [event.event_type for event in events]
    assert events[-1].event_type is RuntimeEventType.TURN_COMPLETED


def test_conformance_chat_session_binding() -> None:
    runtime = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="hello")]),
        sessions=InMemorySessionStore(),
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
    )
    gateway = Gateway(InProcessSessionAdapter(runtime))
    channel = ChannelIdentity(
        channel_type="terminal",
        user_id="user_binding",
        conversation_id="chat_binding",
    )

    binding = gateway.bind_session(channel, "session_binding")
    first = gateway.process_input(
        InboundEnvelope(
            channel_identity=channel.to_dict(),
            input_kind="user_message",
            payload={"content": "hello"},
        )
    )
    second = gateway.process_input(
        InboundEnvelope(
            channel_identity=channel.to_dict(),
            input_kind="supplement_input",
            payload={"content": "and another detail"},
        )
    )

    assert binding.session_id == "session_binding"
    assert gateway.get_binding("terminal", "chat_binding").session_id == "session_binding"
    assert len(first) >= 2
    assert len(second) >= 2

    try:
        gateway.bind_session(channel, "session_binding_2")
    except ValueError as exc:
        assert "only be bound to one session" in str(exc)
    else:
        raise AssertionError("Expected one chat to reject rebinding to a second session")


def test_conformance_requires_action_approval() -> None:
    tool = FakeTool(name="admin", permission=PermissionDecision.ASK)
    registry = StaticToolRegistry([tool])
    store = InMemorySessionStore()
    harness = SimpleHarness(
        model=ScriptedModel(
            [
                ModelTurnResponse(
                    tool_calls=[ToolCall(tool_name="admin", arguments={"text": "rotate"})]
                ),
                ModelTurnResponse(assistant_message="approved and finished"),
            ]
        ),
        sessions=store,
        tools=registry,
        executor=SimpleToolExecutor(registry),
    )

    first_events, first_terminal = harness.run_turn("please rotate", "case_approval")
    session = store.load_session("case_approval")

    assert first_terminal.status is TerminalStatus.BLOCKED
    assert session.status is SessionStatus.REQUIRES_ACTION
    assert first_events[-1].event_type is RuntimeEventType.REQUIRES_ACTION
    assert first_events[-1].payload["tool_name"] == "admin"

    second_events, second_terminal = harness.continue_turn("case_approval", approved=True)
    resumed = store.load_session("case_approval")

    assert second_terminal.status is TerminalStatus.COMPLETED
    assert [event.event_type for event in second_events[:2]] == [
        RuntimeEventType.TOOL_STARTED,
        RuntimeEventType.TOOL_RESULT,
    ]
    assert second_events[-1].event_type is RuntimeEventType.TURN_COMPLETED
    assert resumed.status is SessionStatus.IDLE


def test_conformance_policy_ask_deny_allow() -> None:
    allow_tool = FakeTool(name="allow_tool", permission=PermissionDecision.ALLOW)
    ask_tool = FakeTool(name="ask_tool", permission=PermissionDecision.ASK)
    deny_tool = FakeTool(name="deny_tool", permission=PermissionDecision.DENY)
    registry = StaticToolRegistry([allow_tool, ask_tool, deny_tool])
    store = InMemorySessionStore()

    allow_harness = SimpleHarness(
        model=ScriptedModel(
            [
                ModelTurnResponse(
                    tool_calls=[ToolCall(tool_name="allow_tool", arguments={"text": "ok"})]
                ),
                ModelTurnResponse(assistant_message="allow complete"),
            ]
        ),
        sessions=store,
        tools=registry,
        executor=SimpleToolExecutor(registry),
    )
    allow_events, allow_terminal = allow_harness.run_turn("run allow", "case_policy_allow")

    ask_harness = SimpleHarness(
        model=ScriptedModel(
            [
                ModelTurnResponse(
                    tool_calls=[ToolCall(tool_name="ask_tool", arguments={"text": "review"})]
                ),
                ModelTurnResponse(assistant_message="ask complete"),
            ]
        ),
        sessions=store,
        tools=registry,
        executor=SimpleToolExecutor(registry),
    )
    ask_events, ask_terminal = ask_harness.run_turn("run ask", "case_policy_ask")
    resumed_events, resumed_terminal = ask_harness.continue_turn("case_policy_ask", approved=True)

    deny_harness = SimpleHarness(
        model=ScriptedModel(
            [ModelTurnResponse(tool_calls=[ToolCall(tool_name="deny_tool", arguments={})])]
        ),
        sessions=store,
        tools=registry,
        executor=SimpleToolExecutor(registry),
    )
    deny_events, deny_terminal = deny_harness.run_turn("run deny", "case_policy_deny")
    deny_session = store.load_session("case_policy_deny")

    assert allow_terminal.status is TerminalStatus.COMPLETED
    assert RuntimeEventType.TOOL_RESULT in [event.event_type for event in allow_events]
    assert ask_terminal.status is TerminalStatus.BLOCKED
    assert ask_events[-1].payload["tool_name"] == "ask_tool"
    assert ask_events[-1].payload["resumable"] is True
    assert resumed_terminal.status is TerminalStatus.COMPLETED
    assert resumed_events[0].payload["tool_use_id"] == resumed_events[1].payload["tool_use_id"]
    assert deny_terminal.status is TerminalStatus.FAILED
    assert deny_session.status is SessionStatus.IDLE
    assert all(event.event_type is not RuntimeEventType.REQUIRES_ACTION for event in deny_events)


def test_conformance_session_resume(tmp_path: Path) -> None:
    session_root = tmp_path / "sessions"
    store = FileSessionStore(session_root)
    harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="first reply")]),
        sessions=store,
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
    )

    first_events, first_terminal = harness.run_turn("first", "case_resume")

    assert first_terminal.status is TerminalStatus.COMPLETED
    assert first_events[-1].event_type is RuntimeEventType.TURN_COMPLETED

    restored_store = FileSessionStore(session_root)
    resumed_harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="second reply")]),
        sessions=restored_store,
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
    )
    second_events, second_terminal = resumed_harness.run_turn("second", "case_resume")
    restored_session = restored_store.load_session("case_resume")

    assert second_terminal.status is TerminalStatus.COMPLETED
    assert second_events[-1].event_type is RuntimeEventType.TURN_COMPLETED
    assert [message.content for message in restored_session.messages] == [
        "first",
        "first reply",
        "second",
        "second reply",
    ]


def test_conformance_single_active_harness_lease(tmp_path: Path) -> None:
    store = FileSessionStore(tmp_path / "sessions")
    first_runtime = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="first")]),
        sessions=store,
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
    )
    first_adapter = InProcessSessionAdapter(
        first_runtime,
        agent_id="agent_single",
        gateway_id="gateway_a",
    )
    first_handle = first_adapter.spawn("lease_case")
    active_lease = store.get_active_lease("lease_case")

    assert active_lease is not None
    assert active_lease.harness_instance_id == "gateway_a:lease_case"
    assert first_handle.harness_instance is not None

    second_runtime = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="second")]),
        sessions=store,
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
    )
    second_adapter = InProcessSessionAdapter(
        second_runtime,
        agent_id="agent_single",
        gateway_id="gateway_b",
    )

    try:
        second_adapter.spawn("lease_case")
    except ValueError as exc:
        assert "active harness lease" in str(exc)
    else:
        raise AssertionError("Expected second harness to fail while first lease is active")

    first_adapter.kill("lease_case")
    released = store.get_active_lease("lease_case")
    assert released is None

    second_handle = second_adapter.spawn("lease_case")
    reacquired = store.get_active_lease("lease_case")
    assert reacquired is not None
    assert reacquired.harness_instance_id == "gateway_b:lease_case"
    assert second_handle.harness_instance is not None


def test_conformance_sandbox_deny() -> None:
    sandbox = LocalSandbox(allowed_command_prefixes=["python"])
    negotiation = sandbox.negotiate(
        SandboxExecutionRequest(
            command=["bash", "-lc", "echo no"],
            requires_network=True,
        )
    )

    assert negotiation.allowed is False
    assert "Command is not allowed by sandbox policy: bash" in negotiation.reasons
    assert "Network access is not available in this sandbox" in negotiation.reasons

    try:
        sandbox.execute(
            SandboxExecutionRequest(
                command=["bash", "-lc", "echo no"],
                requires_network=True,
            )
        )
    except PermissionError as exc:
        assert "Command is not allowed by sandbox policy: bash" in str(exc)
    else:
        raise AssertionError("Expected PermissionError")


def test_conformance_mcp_tool_adaptation() -> None:
    from openagent.tools import (
        InMemoryMcpClient,
        McpPromptAdapter,
        McpPromptDescriptor,
        McpResourceDescriptor,
        McpServerConnection,
        McpServerDescriptor,
        McpSkillAdapter,
        McpToolAdapter,
        McpToolDescriptor,
    )

    client = InMemoryMcpClient()
    client.connect(
        McpServerConnection(
            descriptor=McpServerDescriptor(server_id="docs", label="Docs Server"),
            tools={
                "echo": (
                    McpToolDescriptor(name="echo", description="Echo text"),
                    lambda args: ToolResult(
                        tool_name="echo",
                        success=True,
                        content=[str(args["text"])],
                    ),
                )
            },
            prompts={
                "review": McpPromptDescriptor(
                    name="review",
                    description="Review prompt",
                    template="Review {topic}",
                )
            },
            resources={
                "skill://summarize": McpResourceDescriptor(
                    uri="skill://summarize",
                    name="Summarize",
                    description="Summarize notes",
                    content="Summarize {topic}",
                )
            },
        )
    )

    adapted_tool = McpToolAdapter().adapt_mcp_tool("docs", client.list_tools("docs")[0])
    adapted_prompt = McpPromptAdapter().adapt_mcp_prompt("docs", client.list_prompts("docs")[0])
    adapted_skill = McpSkillAdapter().adapt_mcp_skill(
        "docs",
        McpSkillAdapter().discover_skills_from_resources("docs", client.list_resources("docs"))[0],
    )
    result = client.call_tool("docs", "echo", {"text": "hello"})

    assert adapted_tool.name == "echo"
    assert adapted_prompt.kind.value == "prompt"
    assert adapted_prompt.source == "mcp_prompt"
    assert adapted_skill.id == "summarize"
    assert adapted_skill.metadata["server_id"] == "docs"
    assert result.content == ["hello"]


def test_conformance_memory_recall_and_consolidation(tmp_path: Path) -> None:
    store = InMemorySessionStore()
    memory_store = FileMemoryStore(tmp_path / "memory")
    harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="stored")]),
        sessions=store,
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
        memory_store=memory_store,
    )

    harness.run_turn("Remember that the launch code is sunrise", "case_memory")
    session = store.load_session("case_memory")
    consolidation = memory_store.consolidate("case_memory", session.messages)
    existing_records = memory_store.list()

    request = harness.build_model_input(
        SessionRecord(
            session_id="case_memory",
            messages=[SessionMessage(role="user", content="What is the launch code?")],
        ),
        [],
    )

    restored_memory_store = FileMemoryStore(tmp_path / "memory")
    restored_harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="restored")]),
        sessions=store,
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
        memory_store=restored_memory_store,
    )
    restored_request = restored_harness.build_model_input(
        SessionRecord(
            session_id="case_memory",
            messages=[SessionMessage(role="user", content="launch code?")],
        ),
        [],
    )

    assert consolidation.new_records or existing_records
    assert request.memory_context
    assert "sunrise" in str(request.memory_context[0]["content"])
    assert request.messages == [{"role": "user", "content": "What is the launch code?"}]
    assert restored_request.memory_context
    assert "sunrise" in str(restored_request.memory_context[0]["content"])


def test_conformance_agents_memory_loading_precedence(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    (home / ".openagent").mkdir(parents=True)
    (home / ".openagent" / "AGENTS.md").write_text(
        "Global guidance\nOwner: global\nTone: calm\n",
        encoding="utf-8",
    )
    workdir = tmp_path / "repo"
    subtree = workdir / "src" / "feature"
    sibling = workdir / "src" / "other"
    subtree.mkdir(parents=True)
    sibling.mkdir(parents=True)
    (workdir / "AGENTS.md").write_text(
        "Project guidance\nOwner: project\nConstraint: local-only\n",
        encoding="utf-8",
    )
    (subtree / "AGENTS.md").write_text(
        "Subtree guidance\nOwner: subtree\n",
        encoding="utf-8",
    )
    (sibling / "AGENTS.md").write_text("Sibling guidance\n", encoding="utf-8")

    harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="ok")]),
        sessions=InMemorySessionStore(),
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
    )
    session = SessionRecord(
        session_id="case_agents_md",
        messages=[SessionMessage(role="user", content="edit src/feature/file.py")],
        metadata={"workdir": str(workdir), "target_path": "src/feature/file.py"},
    )

    request = harness.build_model_input(session, [])

    assert request.memory_context
    content = str(request.memory_context[0]["content"])
    assert "Global guidance" in content
    assert "Project guidance" in content
    assert "Subtree guidance" in content
    assert "Sibling guidance" not in content
    assert "Owner: subtree" in content
    assert "Project guidance" not in "\n".join(message["content"] for message in request.messages)


def test_conformance_agent_global_long_memory(tmp_path: Path) -> None:
    memory_store = FileMemoryStore(tmp_path / "memory")
    store = InMemorySessionStore()
    harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="ok")]),
        sessions=store,
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
        memory_store=memory_store,
    )
    session_a = SessionRecord(
        session_id="session_a",
        agent_id="agent_shared",
        messages=[
            SessionMessage(role="user", content="Remember agent preference: codename atlas"),
            SessionMessage(role="assistant", content="Noted"),
        ],
    )
    memory_store.consolidate("session_a", session_a.messages, agent_id="agent_shared")

    request = harness.build_model_input(
        SessionRecord(
            session_id="session_b",
            agent_id="agent_shared",
            messages=[SessionMessage(role="user", content="What is the codename?")],
        ),
        [],
    )

    assert request.memory_context
    assert any("atlas" in str(item.get("content")) for item in request.memory_context)


def test_conformance_background_agent() -> None:
    manager = InMemoryTaskManager()
    orchestrator = LocalBackgroundAgentOrchestrator(manager)

    def worker(context: BackgroundTaskContext) -> JsonObject:
        context.progress({"message": "started"})
        context.checkpoint({"step": "summary"})
        return {"output_ref": "memory://tasks/summary"}

    handle = orchestrator.start_background_task(
        "summarize repo",
        worker,
    )

    initial_events = orchestrator.list_events(handle.task_id)
    assert initial_events[0].event_type.value == "task_started"

    for _ in range(20):
        task = orchestrator.get_task(handle.task_id)
        if task.status is TerminalStatus.COMPLETED:
            break

    events = orchestrator.list_events(handle.task_id)
    task = orchestrator.get_task(handle.task_id)

    assert any(event.event_type.value == "task_progress" for event in events)
    assert events[-1].event_type.value == "task_completed"
    assert task.output_ref == "memory://tasks/summary"
    assert task.status is TerminalStatus.COMPLETED


def test_conformance_runtime_task_lifecycle() -> None:
    manager = InMemoryTaskManager()
    orchestrator = LocalBackgroundAgentOrchestrator(manager)

    completed = orchestrator.start_background_task(
        "complete",
        lambda context: (context.progress({"summary": "running"}) or {"output_ref": "memory://done"}),
    )
    failed = orchestrator.start_background_task(
        "fail",
        lambda context: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    killed = orchestrator.start_background_task("kill", lambda context: {"output_ref": "noop"})
    orchestrator.kill_task(killed.task_id)

    for _ in range(20):
        if manager.get(completed.task_id).status is TerminalStatus.COMPLETED:
            break

    try:
        orchestrator._futures[failed.task_id].result(timeout=1)
    except RuntimeError:
        pass

    completed_task = manager.get(completed.task_id)
    failed_task = manager.get(failed.task_id)
    killed_task = manager.get(killed.task_id)

    assert completed_task.status is TerminalStatus.COMPLETED
    assert failed_task.status is TerminalStatus.FAILED
    assert str(killed_task.status) == TerminalStatus.KILLED.value
    assert completed_task.end_time is not None
    assert failed_task.metadata is not None and failed_task.metadata["reason"] == "boom"


def test_conformance_task_output_cursor_and_resume(tmp_path: Path) -> None:
    from openagent.harness.task import FileTaskManager

    manager = FileTaskManager(str(tmp_path / "tasks"))
    task = manager.create_background_task("stream")
    manager.append_output(task.task_id, "line-1")
    cursor = manager.append_output(task.task_id, "line-2")
    manager.complete_task(task.task_id, output_ref=f"memory://tasks/{task.task_id}/output")

    recovered = FileTaskManager(str(tmp_path / "tasks"))
    slice_ = recovered.read_output(task.task_id, cursor=1)
    events = recovered.read_events(task.task_id)

    assert cursor == 2
    assert slice_.items == ["line-2"]
    assert slice_.cursor == 2
    assert events.cursor >= 2
    assert recovered.get(task.task_id).output_ref == f"memory://tasks/{task.task_id}/output"


def test_conformance_task_retention_and_eviction(tmp_path: Path) -> None:
    from openagent.harness.task import FileTaskManager

    manager = FileTaskManager(
        str(tmp_path / "tasks"),
        retention_policy=TaskRetentionPolicy(grace_period_seconds=0),
    )
    task = manager.create_background_task("notify")
    manager.complete_task(task.task_id, output_ref=f"memory://tasks/{task.task_id}/output")
    manager.mark_notified(task.task_id)
    manager.attach_observer(task.task_id, "feishu:chat:group_1")

    assert manager.evict_expired(now_timestamp="2030-01-01T00:00:00+00:00") == []

    manager.detach_observer(task.task_id, "feishu:chat:group_1")

    assert manager.evict_expired(now_timestamp="2030-01-01T00:00:00+00:00") == [task.task_id]
