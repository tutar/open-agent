from pathlib import Path

import openagent
from openagent.local import create_file_runtime, create_in_memory_runtime
from openagent.object_model import RuntimeEventType
from openagent.tools import (
    AskUserQuestionTool,
    CommandKind,
    CommandVisibility,
    DenialTrackingState,
    PermissionDecision,
    RequiresActionError,
    ReviewCommand,
    ReviewCommandKind,
    RuleBasedToolPolicyEngine,
    SimpleToolExecutor,
    StaticToolRegistry,
    ToolCall,
    ToolExecutionContext,
    ToolPolicyRule,
    ToolSource,
    create_builtin_commands,
    create_builtin_toolset,
)


class AliasTool:
    name = "echo"
    aliases = ["print"]
    source = ToolSource.BUILTIN
    input_schema = {"type": "object"}

    def description(self) -> str:
        return "Echo text"

    def call(self, arguments: dict[str, object]):
        from openagent.object_model import ToolResult

        return ToolResult(
            tool_name=self.name,
            success=True,
            content=[str(arguments.get("text", ""))],
        )

    def check_permissions(self, arguments: dict[str, object]) -> str:
        del arguments
        return PermissionDecision.ALLOW.value

    def is_concurrency_safe(self, arguments: dict[str, object]) -> bool:
        del arguments
        return True


def test_static_tool_registry_resolves_alias_and_records_source() -> None:
    registry = StaticToolRegistry([AliasTool()])

    assert registry.resolve_tool("print").name == "echo"
    record = registry.list_tool_records()[0]
    assert record.aliases == ["print"]
    assert record.source is ToolSource.BUILTIN


def test_policy_engine_tracks_denials_and_falls_back_to_ask() -> None:
    engine = RuleBasedToolPolicyEngine(
        rules=[
            ToolPolicyRule(
                decision=PermissionDecision.DENY,
                tool_name="echo",
                reason="blocked",
            )
        ],
    )
    tool = AliasTool()
    context = ToolExecutionContext(session_id="sess_policy")

    first = engine.evaluate(tool, ToolCall(tool_name="echo"), context)
    second = engine.evaluate(tool, ToolCall(tool_name="echo"), context)
    third = engine.evaluate(tool, ToolCall(tool_name="echo"), context)

    assert first.decision is PermissionDecision.DENY
    assert second.decision is PermissionDecision.DENY
    assert third.decision is PermissionDecision.ASK
    assert third.audit_metadata["consecutive_denials"] == DenialTrackingState(
        consecutive_denials=3,
        total_denials=3,
    ).consecutive_denials


def test_executor_exposes_summary_after_execution() -> None:
    registry = StaticToolRegistry([AliasTool()])
    executor = SimpleToolExecutor(registry)
    context = ToolExecutionContext(session_id="sess_exec")

    events = list(
        executor.execute_stream(
            [ToolCall(tool_name="echo", arguments={"text": "hello"}, call_id="toolu_1")],
            context,
        )
    )

    started = next(event for event in events if event.event_type is RuntimeEventType.TOOL_STARTED)
    assert started.payload["tool_use_id"] == "toolu_1"
    summaries = list(executor._summaries.values())
    assert len(summaries) == 1
    assert summaries[0].results[0].content == ["hello"]


def test_builtin_file_tools_roundtrip(tmp_path: Path) -> None:
    tools = {
        tool.name: tool
        for tool in create_builtin_toolset(root=str(tmp_path))
        if tool.name in {"Read", "Write", "Edit", "Glob", "Grep"}
    }

    write_result = tools["Write"].call({"path": "notes.txt", "content": "alpha\nbeta\n"})
    read_result = tools["Read"].call({"path": "notes.txt"})
    edit_result = tools["Edit"].call({"path": "notes.txt", "old": "beta", "new": "gamma"})
    glob_result = tools["Glob"].call({"pattern": "*.txt"})
    grep_result = tools["Grep"].call({"pattern": "gamma"})

    assert write_result.content == [str((tmp_path / "notes.txt").resolve())]
    assert read_result.content == ["alpha\nbeta\n"]
    assert edit_result.content == [str((tmp_path / "notes.txt").resolve())]
    assert glob_result.content == ["notes.txt"]
    assert grep_result.content == ["notes.txt:2:gamma"]


def test_ask_user_question_tool_raises_requires_action() -> None:
    tool = AskUserQuestionTool()

    try:
        tool.call({"question": "Which branch?"}, ToolExecutionContext(session_id="sess_ask"))
    except RequiresActionError as exc:
        assert exc.requires_action.action_type == "ask_user_question"
        assert exc.requires_action.description == "Which branch?"
    else:
        raise AssertionError("Expected RequiresActionError")


def test_review_command_and_builtin_review_surface() -> None:
    command = ReviewCommand(
        id="cmd.verify",
        name="verify",
        kind=CommandKind.REVIEW,
        description="Verify the current output",
        visibility=CommandVisibility.BOTH,
        source="builtin_review",
        review_kind=ReviewCommandKind.VERIFICATION,
    )
    builtin = create_builtin_commands()

    assert command.kind is CommandKind.REVIEW
    assert command.review_kind is ReviewCommandKind.VERIFICATION
    assert builtin[0].kind is CommandKind.REVIEW


def test_local_runtime_defaults_to_builtin_tool_baseline(tmp_path: Path) -> None:
    in_memory = create_in_memory_runtime(model=object())
    file_backed = create_file_runtime(model=object(), session_root=str(tmp_path / "sessions"))

    in_memory_names = {record.tool_name for record in in_memory.tools.list_tool_records()}
    file_names = {record.tool_name for record in file_backed.tools.list_tool_records()}

    expected = {
        "Read",
        "Write",
        "Edit",
        "Glob",
        "Grep",
        "Bash",
        "WebFetch",
        "WebSearch",
        "AskUserQuestion",
    }
    assert expected.issubset(in_memory_names)
    assert expected.issubset(file_names)


def test_openagent_root_reexports_tools_surface() -> None:
    assert openagent.WebSearchTool.__name__ == "WebSearchTool"
    assert openagent.ReviewCommandKind.VERIFICATION.value == "verification"
    assert callable(openagent.create_builtin_toolset)
