from dataclasses import dataclass
from pathlib import Path

from openagent.capability_surface import (
    CapabilityOrigin,
    CapabilityOriginType,
    CapabilitySurface,
)
from openagent.context_governance import ContextGovernance
from openagent.harness.runtime import ModelTurnRequest, ModelTurnResponse
from openagent.harness.task import (
    BackgroundTaskContext,
    FileTaskManager,
    InMemoryTaskManager,
    LocalBackgroundAgentOrchestrator,
    LocalTaskKind,
    LocalVerificationRuntime,
    TaskRetentionPolicy,
    VerificationRequest,
    VerificationVerdict,
)
from openagent.local import create_file_runtime, create_in_memory_runtime
from openagent.object_model import JsonObject, TerminalStatus, ToolResult
from openagent.sandbox import LocalSandbox, SandboxExecutionRequest
from openagent.session import SessionMessage
from openagent.tools import (
    Command,
    CommandKind,
    CommandVisibility,
    ReviewCommand,
    ReviewCommandKind,
    SkillDefinition,
    StaticCommandRegistry,
)


@dataclass(slots=True)
class StaticModel:
    message: str

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        del request
        return ModelTurnResponse(assistant_message=self.message)


def test_in_memory_task_manager_roundtrip() -> None:
    manager = InMemoryTaskManager()

    record = manager.create_task("bootstrap runtime", metadata={"stage": "init"})
    manager.update_task(record.task_id, TerminalStatus.FAILED.value, metadata={"stage": "done"})

    updated = manager.get_task(record.task_id)
    assert updated.status == TerminalStatus.FAILED.value
    assert updated.metadata == {"stage": "done"}


def test_background_and_verifier_tasks_have_local_lifecycle() -> None:
    manager = InMemoryTaskManager()

    background = manager.create_background_task("index workspace", metadata={"scope": "repo"})
    verifier = manager.create_verifier_task("verify patch", metadata={"source": "user"})
    manager.checkpoint_task(background.task_id, {"step": "crawl"})
    manager.complete_task(background.task_id, output_ref="memory://task/index")
    manager.fail_task(verifier.task_id, "lint_failed", metadata={"tool": "ruff"})

    background_record = manager.get_task(background.task_id)
    verifier_record = manager.get_task(verifier.task_id)
    background_events = manager.read_events(background.task_id)

    assert background.task_kind is LocalTaskKind.BACKGROUND
    assert manager.get_handle(background.task_id).checkpoints == [{"step": "crawl"}]
    assert background_record.output_ref == "memory://task/index"
    assert background_record.status is TerminalStatus.COMPLETED
    assert verifier_record.type == LocalTaskKind.VERIFIER.value
    assert verifier_record.status is TerminalStatus.FAILED
    assert verifier_record.metadata == {"source": "user", "reason": "lint_failed", "tool": "ruff"}
    assert background_events.cursor >= 2
    assert background_events.events[-1]["type"] == "completed"


def test_file_task_manager_persists_tasks_and_handles(tmp_path: Path) -> None:
    root = tmp_path / "tasks"
    manager = FileTaskManager(root)

    background = manager.create_background_task("index workspace", metadata={"scope": "repo"})
    verifier = manager.create_verifier_task("verify patch", metadata={"source": "user"})
    manager.checkpoint_task(background.task_id, {"step": "crawl"})
    manager.complete_task(background.task_id, output_ref="memory://task/index")
    manager.fail_task(verifier.task_id, "lint_failed", metadata={"tool": "ruff"})

    recovered = FileTaskManager(root)
    background_record = recovered.get_task(background.task_id)
    verifier_record = recovered.get_task(verifier.task_id)
    background_handle = recovered.get_handle(background.task_id)
    listed_ids = [record.task_id for record in recovered.list_tasks()]
    output_slice = recovered.read_output(background.task_id)

    assert background_handle.checkpoints == [{"step": "crawl"}]
    assert background_record.output_ref == "memory://task/index"
    assert output_slice.output_ref == "memory://task/index"
    assert background_record.status is TerminalStatus.COMPLETED
    assert verifier_record.status is TerminalStatus.FAILED
    assert verifier_record.metadata == {"source": "user", "reason": "lint_failed", "tool": "ruff"}
    assert listed_ids == [background.task_id, verifier.task_id]


def test_local_sandbox_executes_allowed_command() -> None:
    sandbox = LocalSandbox(allowed_command_prefixes=["python"])

    result = sandbox.execute(
        SandboxExecutionRequest(command=["python", "-c", "print('sandbox-ok')"])
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "sandbox-ok"


def test_local_sandbox_rejects_disallowed_command() -> None:
    sandbox = LocalSandbox(allowed_command_prefixes=["python"])

    try:
        sandbox.execute(SandboxExecutionRequest(command=["bash", "-lc", "echo no"]))
    except PermissionError as exc:
        assert "not allowed" in str(exc)
    else:
        raise AssertionError("Expected PermissionError")


def test_local_sandbox_negotiates_capabilities_and_denies_missing_access() -> None:
    sandbox = LocalSandbox(
        allowed_command_prefixes=["python"],
        supports_network=False,
        supports_filesystem_write=False,
        available_credentials=["repo-token"],
    )

    negotiation = sandbox.negotiate(
        SandboxExecutionRequest(
            command=["bash", "-lc", "echo no"],
            requires_network=True,
            requires_filesystem_write=True,
            required_credentials=["repo-token", "cloud-token"],
        )
    )

    assert negotiation.allowed is False
    assert "Command is not allowed by sandbox policy: bash" in negotiation.reasons
    assert "Network access is not available in this sandbox" in negotiation.reasons
    assert "Filesystem write access is not available in this sandbox" in negotiation.reasons
    assert "Missing sandbox credentials: cloud-token" in negotiation.reasons


def test_in_memory_runtime_creates_working_runtime() -> None:
    runtime = create_in_memory_runtime(model=StaticModel(message="profile-ready"))

    events, terminal = runtime.run_turn("hello", "sess_profile")

    assert terminal.status is TerminalStatus.COMPLETED
    assert events[1].payload["message"] == "profile-ready"


def test_local_runtimes_use_in_process_binding(tmp_path: Path) -> None:
    tui_runtime = create_in_memory_runtime(StaticModel(message="tui"))
    file_runtime = create_file_runtime(StaticModel(message="file"), str(tmp_path / "file"))

    tui_events, tui_terminal = tui_runtime.run_turn("hello", "sess_tui")
    file_events, file_terminal = file_runtime.run_turn("hello", "sess_file")

    assert tui_terminal.status is TerminalStatus.COMPLETED
    assert file_terminal.status is TerminalStatus.COMPLETED
    assert tui_events[1].payload["message"] == "tui"
    assert file_events[1].payload["message"] == "file"


def test_file_task_manager_can_be_created_directly(tmp_path: Path) -> None:
    manager = FileTaskManager(root=str(tmp_path / "tui_tasks"))

    assert isinstance(manager, FileTaskManager)


def test_local_background_agent_orchestrator_runs_without_blocking() -> None:
    manager = InMemoryTaskManager()
    orchestrator = LocalBackgroundAgentOrchestrator(manager)

    def worker(context: BackgroundTaskContext) -> JsonObject:
        context.checkpoint({"step": "scan"})
        context.progress({"message": "halfway"})
        return {"output_ref": "memory://tasks/index", "summary": "done"}

    handle = orchestrator.start_background_task(
        "index repo",
        worker,
        metadata={"source": "user"},
    )

    task = orchestrator.get_task(handle.task_id)
    events = orchestrator.list_events(handle.task_id)

    assert handle.task_kind is LocalTaskKind.BACKGROUND
    assert task.task_id == handle.task_id
    assert events[0].event_type.value == "task_started"

    for _ in range(20):
        task = orchestrator.get_task(handle.task_id)
        if task.status is TerminalStatus.COMPLETED:
            break

    final_events = orchestrator.list_events(handle.task_id)
    final_task = orchestrator.get_task(handle.task_id)

    assert any(event.event_type.value == "task_progress" for event in final_events)
    assert final_events[-1].event_type.value == "task_completed"
    assert final_task.output_ref == "memory://tasks/index"
    assert final_task.status is TerminalStatus.COMPLETED


def test_task_output_cursor_and_resume_with_file_manager(tmp_path: Path) -> None:
    manager = FileTaskManager(str(tmp_path / "tasks"))
    task = manager.create_background_task("stream output")

    first_cursor = manager.append_output(task.task_id, "first")
    second_cursor = manager.append_output(task.task_id, "second")
    manager.complete_task(task.task_id, output_ref=f"memory://tasks/{task.task_id}/output")

    first_slice = manager.read_output(task.task_id)
    second_slice = manager.read_output(task.task_id, cursor=first_cursor)
    recovered = FileTaskManager(str(tmp_path / "tasks"))
    recovered_slice = recovered.read_output(task.task_id, cursor=first_cursor)

    assert first_cursor == 1
    assert second_cursor == 2
    assert first_slice.items == ["first", "second"]
    assert second_slice.items == ["second"]
    assert recovered_slice.items == ["second"]
    assert recovered_slice.cursor == 2


def test_task_retention_keeps_terminal_task_while_chat_attached(tmp_path: Path) -> None:
    manager = FileTaskManager(
        str(tmp_path / "tasks"),
        retention_policy=TaskRetentionPolicy(grace_period_seconds=1, evict_output_with_state=True),
    )
    task = manager.create_background_task("notify me")
    manager.complete_task(task.task_id, output_ref=f"memory://tasks/{task.task_id}/output")
    manager.mark_notified(task.task_id)
    manager.attach_observer(task.task_id, "feishu:chat:p2p_1")

    kept = manager.evict_expired(now_timestamp="2030-01-01T00:00:05+00:00")
    manager.detach_observer(task.task_id, "feishu:chat:p2p_1")
    removed = manager.evict_expired(now_timestamp="2030-01-01T00:00:05+00:00")

    assert kept == []
    assert removed == [task.task_id]


def test_local_verification_runtime_registers_review_command() -> None:
    manager = InMemoryTaskManager()
    orchestrator = LocalBackgroundAgentOrchestrator(manager)
    runtime = LocalVerificationRuntime(orchestrator)
    registry = StaticCommandRegistry()
    command = ReviewCommand(
        id="cmd.review",
        name="review",
        kind=CommandKind.REVIEW,
        description="Run verification",
        visibility=CommandVisibility.BOTH,
        source="builtin_review",
        review_kind=ReviewCommandKind.VERIFICATION,
    )
    registry.register(command, lambda args: args)
    runtime.register_verification_command(registry)

    result = registry.invoke_command(
        "cmd.review",
        {
            "target_session": "sess_review",
            "original_task": "task_patch",
            "prompt": "Verify the patch",
            "changed_artifacts": ["src/app.py"],
        },
    )

    assert result["kind"] == "verification"
    assert result["verdict"] == VerificationVerdict.PARTIAL.value
    assert any("artifact:src/app.py" == item for item in result["evidence"])


def test_verifier_task_roundtrip() -> None:
    manager = InMemoryTaskManager()
    orchestrator = LocalBackgroundAgentOrchestrator(manager)
    runtime = LocalVerificationRuntime(orchestrator)

    handle = runtime.spawn_verifier(
        VerificationRequest(
            target_session="sess_verify",
            original_task="task_42",
            prompt="Verify correctness",
            changed_artifacts=["src/main.py"],
        )
    )
    result = runtime.await_verifier(handle, timeout=1)
    task = manager.get(handle.task_id)

    assert result.verdict is VerificationVerdict.PARTIAL
    assert task.type == LocalTaskKind.VERIFIER.value
    assert task.status is TerminalStatus.COMPLETED
    assert task.output_ref is not None


def test_context_governance_compacts_and_externalizes(tmp_path: Path) -> None:
    governance = ContextGovernance(
        max_tokens=5,
        warning_tokens=4,
        compact_to_messages=2,
        overflow_compact_to_messages=1,
        storage_dir=str(tmp_path),
    )
    runtime = create_in_memory_runtime(model=StaticModel(message="governed"))
    runtime.context_governance = governance

    runtime.run_turn("a" * 120, "sess_compact")
    session = runtime.sessions.load_session("sess_compact")
    request = runtime.build_model_input(session, [])
    report = runtime.last_context_report
    cache_plan = governance.build_prompt_cache_plan(session.messages)

    assert isinstance(request.messages, list)
    assert len(request.messages) <= 1
    assert report is not None
    assert report.warning_threshold_reached is True
    assert report.recovered_from_overflow is True
    assert report.cache_stable_prefix_messages >= 0
    assert len(cache_plan.cache_breakpoints) <= 1


def test_context_governance_externalizes_long_tool_result(tmp_path: Path) -> None:
    governance = ContextGovernance(
        externalize_threshold_chars=10,
        storage_dir=str(tmp_path),
    )
    result = governance.externalize_tool_result(
        ToolResult(tool_name="echo", success=True, content=["0123456789abcdef"])
    )

    assert result.truncated is True
    assert result.persisted_ref is not None
    assert Path(result.persisted_ref).exists()
    assert result.metadata is not None
    assert result.metadata["externalized"] is True


def test_context_governance_formats_externalized_tool_result_without_path_leak(
    tmp_path: Path,
) -> None:
    governance = ContextGovernance(
        externalize_threshold_chars=10,
        storage_dir=str(tmp_path),
    )
    result = governance.externalize_tool_result(
        ToolResult(tool_name="WebFetch", success=True, content=["0123456789abcdef"])
    )

    message_content = governance.tool_result_message_content(result)

    assert result.persisted_ref is not None
    assert result.persisted_ref not in message_content
    assert "not a workspace file path" in message_content


def test_context_governance_builds_budget_plan_and_provider_cache_key() -> None:
    governance = ContextGovernance(
        max_tokens=40,
        warning_tokens=20,
        reserve_output_tokens=10,
        minimum_continuation_tokens=8,
    )
    messages = [
        SessionMessage(role="system", content="stay concise"),
        SessionMessage(role="user", content="hello world" * 3),
        SessionMessage(role="assistant", content="reply" * 3),
        SessionMessage(role="user", content="follow up question"),
    ]

    plan = governance.build_continuation_budget_plan(messages, ["echo"])
    cache_plan = governance.build_prompt_cache_plan(messages)
    report = governance.report_for_model_input(
        messages,
        ["echo"],
        compacted=False,
        recovered_from_overflow=False,
    )

    assert plan.reserved_output_tokens == 10
    assert plan.available_context_tokens == 30
    assert report.recommended_max_output_tokens == 10
    assert report.provider_cache_key == cache_plan.provider_cache_key
    assert cache_plan.provider_cache_key is not None


def test_context_governance_blocks_continuation_when_budget_too_small() -> None:
    governance = ContextGovernance(
        max_tokens=16,
        warning_tokens=8,
        reserve_output_tokens=8,
        minimum_continuation_tokens=6,
    )
    messages = [
        SessionMessage(role="user", content="x" * 80),
        SessionMessage(role="assistant", content="y" * 40),
    ]

    assert governance.should_allow_continuation(messages, []) is False


def test_prompt_cache_stable_prefix_and_dynamic_suffix() -> None:
    governance = ContextGovernance()
    first_messages = [
        SessionMessage(role="system", content="stay concise"),
        SessionMessage(role="user", content="static topic"),
        SessionMessage(role="assistant", content="dynamic one"),
        SessionMessage(role="user", content="dynamic two"),
    ]
    second_messages = [
        SessionMessage(role="system", content="stay concise"),
        SessionMessage(role="user", content="static topic"),
        SessionMessage(role="assistant", content="dynamic one updated"),
        SessionMessage(role="user", content="dynamic two updated"),
    ]

    first = governance.snapshot_prompt_cache(first_messages, ["echo"])
    second = governance.snapshot_prompt_cache(second_messages, ["echo"])

    assert first.stable_prefix_key == second.stable_prefix_key
    assert first.dynamic_suffix_key != second.dynamic_suffix_key


def test_prompt_cache_break_detection_is_structured() -> None:
    governance = ContextGovernance()
    messages = [
        SessionMessage(role="system", content="stay concise"),
        SessionMessage(role="user", content="topic"),
        SessionMessage(role="assistant", content="reply"),
    ]

    previous = governance.snapshot_prompt_cache(messages, ["echo"], ttl_bucket="1h")
    current = governance.snapshot_prompt_cache(messages, ["echo"], ttl_bucket="5m")
    break_result = governance.detect_cache_break(previous, current)

    assert break_result.break_detected is True
    assert break_result.reason == "ttl_changed"
    assert break_result.expected_miss is True


def test_prompt_cache_fork_sharing_inherits_parent_prefix() -> None:
    governance = ContextGovernance()
    parent_messages = [
        SessionMessage(role="system", content="stay concise"),
        SessionMessage(role="user", content="static topic"),
        SessionMessage(role="assistant", content="parent dynamic"),
    ]
    parent = governance.snapshot_prompt_cache(parent_messages, ["echo"], ttl_bucket="1h")
    child = governance.fork_prompt_cache(
        parent,
        [SessionMessage(role="user", content="child dynamic suffix")],
        skip_cache_write=True,
    )

    assert child.stable_prefix_key == parent.stable_prefix_key
    assert child.tool_surface_key == parent.tool_surface_key
    assert child.ttl_bucket == parent.ttl_bucket
    assert child.dynamic_suffix_key != parent.dynamic_suffix_key
    assert child.skip_cache_write is True


def test_prompt_cache_strategy_equivalence_baseline() -> None:
    governance = ContextGovernance()
    messages = [
        SessionMessage(role="system", content="stay concise"),
        SessionMessage(role="user", content="static topic"),
        SessionMessage(role="assistant", content="dynamic one"),
        SessionMessage(role="user", content="dynamic two"),
    ]
    snapshots = [
        governance.snapshot_prompt_cache_with_strategy(messages, ["echo"], strategy)
        for strategy in (
            "anthropic_native",
            "openclaw_mediated",
            "fallback",
        )
    ]

    assert (
        snapshots[0].stable_prefix_key
        == snapshots[1].stable_prefix_key
        == snapshots[2].stable_prefix_key
    )
    assert (
        snapshots[0].tool_surface_key
        == snapshots[1].tool_surface_key
        == snapshots[2].tool_surface_key
    )


def test_capability_surface_projects_unified_entries() -> None:
    surface = CapabilitySurface(
        tools=[],
        commands=[
            (
                Command(
                    id="cmd.review",
                    name="review",
                    kind=CommandKind.PROMPT,
                    description="Review input",
                    visibility=CommandVisibility.BOTH,
                    source="builtin",
                ),
                CapabilityOrigin(origin_type=CapabilityOriginType.BUILTIN),
            ),
            (
                Command(
                    id="cmd.local.panel",
                    name="local-panel",
                    kind=CommandKind.LOCAL_UI,
                    description="Open local panel",
                    visibility=CommandVisibility.USER,
                    source="local_extension",
                ),
                CapabilityOrigin(origin_type=CapabilityOriginType.BUNDLED),
            ),
        ],
        skills=[
            (
                SkillDefinition(
                    id="skill.summarize",
                    name="Summarize",
                    description="Summarize content",
                    content="Summarize {text}",
                    scope="project",
                    trust_level="trusted",
                    disclosure="catalog",
                    listed_resources=["scripts"],
                    frontmatter_mode="stripped",
                    skill_root="/tmp/skills/summarize",
                    skill_file="/tmp/skills/summarize/SKILL.md",
                ),
                CapabilityOrigin(origin_type=CapabilityOriginType.BUNDLED),
            )
        ],
    )

    terminal_projected = surface.project_for_host("terminal")
    feishu_projected = surface.project_for_host("feishu")
    cloud_projected = surface.project_for_host("cloud")
    model_only = surface.list_command_surface(filters={"visibility": "model"})

    assert terminal_projected["host_profile"] == "terminal"
    command_surface = terminal_projected["command_surface"]
    assert isinstance(command_surface, list)
    assert len(command_surface) == 3
    assert terminal_projected["capability_count"] == 3
    assert feishu_projected["capability_count"] == 2
    assert cloud_projected["capability_count"] == 2
    assert len(model_only) == 2
    assert [entry.entry_id for entry in model_only] == ["cmd.review", "skill.summarize"]
    skill_descriptor = next(
        descriptor
        for descriptor in terminal_projected["capabilities"]
        if descriptor["capability_id"] == "skill.summarize"
    )
    assert skill_descriptor["metadata"]["disclosure"] == "catalog"
    assert skill_descriptor["metadata"]["listed_resources"] == ["scripts"]
