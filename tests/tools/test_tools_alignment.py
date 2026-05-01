from pathlib import Path

import openagent
from openagent.local import create_file_runtime
from openagent.object_model import RuntimeEventType
from openagent.tools import (
    AGENT_TOOL_NAME,
    ASK_USER_QUESTION_TOOL_NAME,
    BASH_TOOL_NAME,
    AskUserQuestionTool,
    BashTool,
    CommandKind,
    CommandVisibility,
    DenialTrackingState,
    EDIT_TOOL_NAME,
    GLOB_TOOL_NAME,
    GREP_TOOL_NAME,
    PermissionDecision,
    RequiresActionError,
    READ_TOOL_NAME,
    ReviewCommand,
    ReviewCommandKind,
    RuleBasedToolPolicyEngine,
    SKILL_TOOL_NAME,
    SimpleToolExecutor,
    StaticToolRegistry,
    ToolCall,
    ToolExecutionContext,
    ToolExecutionFailedError,
    ToolPolicyRule,
    ToolSource,
    WEB_FETCH_TOOL_NAME,
    WEB_SEARCH_TOOL_NAME,
    WebFetchTool,
    WebSearchTool,
    ReadTool,
    WRITE_TOOL_NAME,
    WriteTool,
    create_builtin_commands,
    create_builtin_toolset,
)
from openagent.tools.AgentTool import DESCRIPTION as AGENT_DESCRIPTION
from openagent.tools.AskUserQuestionTool import DESCRIPTION as ASK_USER_QUESTION_DESCRIPTION
from openagent.tools.BashTool import DESCRIPTION as BASH_DESCRIPTION
from openagent.tools.FileEditTool import DESCRIPTION as EDIT_DESCRIPTION
from openagent.tools.FileReadTool import DESCRIPTION as READ_DESCRIPTION
from openagent.tools.FileWriteTool import DESCRIPTION as WRITE_DESCRIPTION
from openagent.tools.GlobTool import DESCRIPTION as GLOB_DESCRIPTION
from openagent.tools.GrepTool import DESCRIPTION as GREP_DESCRIPTION
from openagent.tools.SkillTool import DESCRIPTION as SKILL_DESCRIPTION
from openagent.tools.WebFetchTool import DESCRIPTION as WEB_FETCH_DESCRIPTION
from openagent.tools.WebSearchTool import DESCRIPTION as WEB_SEARCH_DESCRIPTION


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
    context = ToolExecutionContext(session_id="sess_roundtrip", working_directory=str(tmp_path))

    write_result = tools["Write"].call({"path": "notes.txt", "content": "alpha\nbeta\n"}, context)
    read_result = tools["Read"].call({"path": "notes.txt"}, context)
    edit_result = tools["Edit"].call(
        {"path": "notes.txt", "old": "beta", "new": "gamma"},
        context,
    )
    glob_result = tools["Glob"].call({"pattern": "*.txt"}, context)
    grep_result = tools["Grep"].call({"pattern": "gamma"}, context)

    assert write_result.content == [str((tmp_path / "notes.txt").resolve())]
    assert read_result.content == ["1\talpha\n2\tbeta"]
    assert read_result.structured_content is not None
    assert read_result.structured_content["returned_lines"] == 2
    assert edit_result.content == [str((tmp_path / "notes.txt").resolve())]
    assert glob_result.content == ["notes.txt"]
    assert grep_result.content == ["notes.txt:2:gamma"]


def test_read_tool_supports_offset_and_limit(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("a\nb\nc\nd\n", encoding="utf-8")
    tool = ReadTool(str(tmp_path))
    context = ToolExecutionContext(session_id="sess_read_slice", working_directory=str(tmp_path))

    result = tool.call({"path": "notes.txt", "offset": 2, "limit": 2}, context)

    assert result.content == ["2\tb\n3\tc"]
    assert result.structured_content is not None
    assert result.structured_content["offset"] == 2
    assert result.structured_content["limit"] == 2
    assert result.structured_content["truncated"] is True


def test_edit_tool_requires_specific_match_or_replace_all(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("alpha\nbeta\nbeta\n", encoding="utf-8")
    tools = {tool.name: tool for tool in create_builtin_toolset(root=str(tmp_path))}
    context = ToolExecutionContext(session_id="sess_edit", working_directory=str(tmp_path))

    try:
        tools["Edit"].call({"path": "notes.txt", "old": "beta", "new": "gamma"}, context)
    except ValueError as exc:
        assert "matched multiple locations" in str(exc)
    else:
        raise AssertionError("Expected ValueError for ambiguous edit")

    result = tools["Edit"].call(
        {"path": "notes.txt", "old": "beta", "new": "gamma", "replace_all": True},
        context,
    )

    assert result.structured_content is not None
    assert result.structured_content["replacements"] == 2
    assert path.read_text(encoding="utf-8") == "alpha\ngamma\ngamma\n"


def test_glob_and_grep_support_scoped_search_and_limits(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "src" / "a.py").write_text("match\nignore\n", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("match\nmatch\n", encoding="utf-8")
    (tmp_path / "docs" / "readme.md").write_text("match\n", encoding="utf-8")
    tools = {tool.name: tool for tool in create_builtin_toolset(root=str(tmp_path))}
    context = ToolExecutionContext(session_id="sess_search", working_directory=str(tmp_path))

    glob_result = tools["Glob"].call({"pattern": "*.py", "path": "src", "limit": 1}, context)
    grep_result = tools["Grep"].call(
        {"pattern": "match", "path": "src", "glob": "*.py", "limit": 2},
        context,
    )

    assert glob_result.content == ["src/a.py"]
    assert glob_result.structured_content is not None
    assert glob_result.structured_content["count"] == 1
    assert grep_result.content == ["src/a.py:1:match", "src/b.py:1:match"]
    assert grep_result.structured_content is not None
    assert grep_result.structured_content["truncated"] is True


def test_builtin_tools_validate_required_arguments() -> None:
    executor = SimpleToolExecutor(
        StaticToolRegistry(
            [
                BashTool("."),
                WebFetchTool(),
                WebSearchTool(),
                next(tool for tool in create_builtin_toolset() if tool.name == "Glob"),
            ]
        )
    )
    context = ToolExecutionContext(session_id="sess_required")

    cases = [
        ("Bash", {}, "missing required field command"),
        ("Glob", {}, "missing required field pattern"),
        ("WebFetch", {}, "missing required field url"),
        ("WebSearch", {}, "missing required field query"),
    ]

    for tool_name, arguments, reason in cases:
        try:
            executor.execute([ToolCall(tool_name=tool_name, arguments=arguments)], context)
        except ToolExecutionFailedError as exc:
            assert exc.tool_name == tool_name
            assert exc.reason == reason
        else:
            raise AssertionError(f"Expected ToolExecutionFailedError for {tool_name}")


def test_builtin_tools_validate_optional_argument_types() -> None:
    executor = SimpleToolExecutor(
        StaticToolRegistry(
            [
                ReadTool("."),
                BashTool("."),
                next(tool for tool in create_builtin_toolset() if tool.name == "Edit"),
            ]
        )
    )
    context = ToolExecutionContext(session_id="sess_optional_types", working_directory=".")

    cases = [
        ("Read", {"path": "README.md", "offset": "1"}, "offset must be an integer"),
        ("Edit", {"path": "x", "old": "", "new": "y"}, "old must be a non-empty string"),
        ("Bash", {"command": "pwd", "timeout_ms": 0}, "timeout_ms must be >= 1"),
    ]

    for tool_name, arguments, reason in cases:
        try:
            executor.execute([ToolCall(tool_name=tool_name, arguments=arguments)], context)
        except ToolExecutionFailedError as exc:
            assert exc.tool_name == tool_name
            assert exc.reason == reason
        else:
            raise AssertionError(f"Expected ToolExecutionFailedError for {tool_name}")


def test_builtin_tools_expose_complete_json_schema() -> None:
    toolset = {tool.name: tool for tool in create_builtin_toolset()}

    for tool_name, field_name in [
        ("Read", "path"),
        ("Write", "path"),
        ("Edit", "path"),
        ("Glob", "pattern"),
        ("Grep", "pattern"),
        ("Bash", "command"),
        ("WebFetch", "url"),
        ("WebSearch", "query"),
        ("AskUserQuestion", "question"),
    ]:
        schema = toolset[tool_name].input_schema
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        properties = schema["properties"]
        assert isinstance(properties, dict)
        assert field_name in properties
        field_schema = properties[field_name]
        assert isinstance(field_schema, dict)
        assert field_schema["type"] == "string"
        assert isinstance(field_schema["description"], str)
        assert field_schema["description"]
        assert isinstance(field_schema["examples"], list)
        assert field_name in schema["required"]

    assert "offset" in toolset["Read"].input_schema["properties"]
    assert "replace_all" in toolset["Edit"].input_schema["properties"]
    assert "path" in toolset["Glob"].input_schema["properties"]
    assert "glob" in toolset["Grep"].input_schema["properties"]
    assert "timeout_ms" in toolset["Bash"].input_schema["properties"]


def test_builtin_tool_name_constants_and_prompt_descriptions_align() -> None:
    toolset = {tool.name: tool for tool in create_builtin_toolset(agent_handler=lambda *_: {})}
    expected = {
        READ_TOOL_NAME: READ_DESCRIPTION,
        WRITE_TOOL_NAME: WRITE_DESCRIPTION,
        EDIT_TOOL_NAME: EDIT_DESCRIPTION,
        GLOB_TOOL_NAME: GLOB_DESCRIPTION,
        GREP_TOOL_NAME: GREP_DESCRIPTION,
        BASH_TOOL_NAME: BASH_DESCRIPTION,
        WEB_FETCH_TOOL_NAME: WEB_FETCH_DESCRIPTION,
        WEB_SEARCH_TOOL_NAME: WEB_SEARCH_DESCRIPTION,
        ASK_USER_QUESTION_TOOL_NAME: ASK_USER_QUESTION_DESCRIPTION,
        AGENT_TOOL_NAME: AGENT_DESCRIPTION,
    }

    for tool_name, description in expected.items():
        assert tool_name in toolset
        assert toolset[tool_name].name == tool_name
        assert toolset[tool_name].description() == description


def test_skill_tool_name_constant_and_prompt_description_align() -> None:
    class FakeSkillBridge:
        def invoke_skill(
            self,
            skill_id: str,
            args: dict[str, object],
            runtime_context: dict[str, object],
        ) -> str:
            del args, runtime_context
            return skill_id

    skill_tool = openagent.SkillTool(FakeSkillBridge())

    assert skill_tool.name == SKILL_TOOL_NAME
    assert skill_tool.description() == SKILL_DESCRIPTION


def test_agent_tool_schema_and_background_flag() -> None:
    def _handler(
        arguments: dict[str, object],
        context: ToolExecutionContext | None,
    ) -> dict[str, object]:
        return {
            "task": arguments.get("task"),
            "background": bool(arguments.get("run_in_background")),
            "session_id": context.session_id if context is not None else None,
        }

    toolset = {tool.name: tool for tool in create_builtin_toolset(agent_handler=_handler)}
    agent_tool = toolset["Agent"]
    result = agent_tool.call(
        {"task": "review", "run_in_background": True},
        ToolExecutionContext(session_id="sess_agent"),
    )

    assert "run_in_background" in agent_tool.input_schema["properties"]
    assert result.structured_content is not None
    linkage = result.structured_content["agent_linkage"]
    assert linkage["background"] is True
    assert linkage["session_id"] == "sess_agent"


def test_bash_tool_executes_successfully(tmp_path: Path) -> None:
    tool = BashTool(str(tmp_path))
    context = ToolExecutionContext(session_id="sess_bash_ok", working_directory=str(tmp_path))

    result = tool.call({"command": "pwd"}, context)

    assert result.success is True
    assert result.content == [str(tmp_path)]


def test_bash_tool_reports_non_zero_exit(tmp_path: Path) -> None:
    tool = BashTool(str(tmp_path))
    context = ToolExecutionContext(session_id="sess_bash_fail", working_directory=str(tmp_path))

    try:
        tool.call({"command": "bash -lc 'echo nope >&2; exit 7'"}, context)
    except RuntimeError as exc:
        assert str(exc) == "nope"
    else:
        raise AssertionError("Expected RuntimeError for non-zero bash command")


def test_bash_tool_enforces_timeout(tmp_path: Path) -> None:
    tool = BashTool(str(tmp_path))
    context = ToolExecutionContext(session_id="sess_bash_timeout", working_directory=str(tmp_path))

    try:
        tool.call({"command": "python -c 'import time; time.sleep(0.05)'", "timeout_ms": 1}, context)
    except RuntimeError as exc:
        assert str(exc) == "command timed out after 1ms"
    else:
        raise AssertionError("Expected RuntimeError for command timeout")


def test_builtin_tools_prefer_execution_context_workdir(tmp_path: Path) -> None:
    fallback_root = tmp_path / "fallback"
    fallback_root.mkdir()
    workspace = tmp_path / "session-workspace"
    workspace.mkdir()
    tool = WriteTool(str(fallback_root))

    result = tool.call(
        {"path": "notes.txt", "content": "hello\n"},
        ToolExecutionContext(session_id="sess_workspace", working_directory=str(workspace)),
    )

    assert result.success is True
    assert (workspace / "notes.txt").read_text(encoding="utf-8") == "hello\n"
    assert not (fallback_root / "notes.txt").exists()


def test_bash_tool_allows_workspace_local_commands_without_approval(tmp_path: Path) -> None:
    tool = BashTool(str(tmp_path))
    decision = tool.check_permissions(
        {"command": "mkdir -p logs && touch logs/output.txt"},
        ToolExecutionContext(session_id="sess_ok", working_directory=str(tmp_path)),
    )

    assert decision == PermissionDecision.ALLOW.value


def test_bash_tool_requires_approval_for_workspace_escape(tmp_path: Path) -> None:
    tool = BashTool(str(tmp_path))
    decision = tool.check_permissions(
        {"command": "cat ../secret.txt"},
        ToolExecutionContext(session_id="sess_escape", working_directory=str(tmp_path)),
    )

    assert decision == PermissionDecision.ASK.value


def test_bash_tool_denies_modifying_workspace_root_itself(tmp_path: Path) -> None:
    tool = BashTool(str(tmp_path))
    decision = tool.check_permissions(
        {"command": "rm -rf $PWD"},
        ToolExecutionContext(session_id="sess_deny", working_directory=str(tmp_path)),
    )

    assert decision == PermissionDecision.DENY.value


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
    assert isinstance(builtin[0], ReviewCommand)
    assert builtin[0].review_kind is ReviewCommandKind.VERIFICATION


def test_local_runtime_defaults_to_builtin_tool_baseline(tmp_path: Path) -> None:
    file_backed = create_file_runtime(model=object(), session_root=str(tmp_path / "sessions"))

    file_names = {record.tool_name for record in file_backed.tools.list_tool_records()}

    expected = {
        "Agent",
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
    assert expected.issubset(file_names)


def test_builtin_file_tools_require_explicit_working_directory(tmp_path: Path) -> None:
    tool = WriteTool(str(tmp_path))

    try:
        tool.call({"path": "notes.txt", "content": "hello\n"})
    except RuntimeError as exc:
        assert "working_directory" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError when working_directory is missing")


def test_openagent_root_reexports_tools_surface() -> None:
    assert openagent.WebSearchTool.__name__ == "WebSearchTool"
    assert openagent.FileReadTool.__name__ == "FileReadTool"
    assert openagent.ReadTool is openagent.FileReadTool
    assert openagent.ReviewCommandKind.VERIFICATION.value == "verification"
    assert callable(openagent.create_builtin_toolset)
