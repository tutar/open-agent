from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from openagent.harness.providers import load_model_from_env
from openagent.harness.runtime import SimpleHarness
from openagent.object_model import RuntimeEventType, TerminalStatus
from openagent.session import FileSessionStore
from openagent.tools import (
    BASH_TOOL_NAME,
    EDIT_TOOL_NAME,
    GLOB_TOOL_NAME,
    GREP_TOOL_NAME,
    READ_TOOL_NAME,
    SimpleToolExecutor,
    StaticToolRegistry,
    WRITE_TOOL_NAME,
    create_builtin_toolset,
)
from tests.tools.tool_eval_support import (
    live_tool_selection_eval_enabled,
    provider_summary,
    require_live_model_endpoint,
)


def _live_tool_selection_eval_enabled() -> bool:
    return live_tool_selection_eval_enabled()


@dataclass(slots=True)
class ToolSelectionScenario:
    name: str
    prompt: str
    enabled_tools: tuple[str, ...]
    expected_tool: str
    prepare_workspace: Callable[[Path], None]
    assert_effect: Callable[[Path, list[dict[str, object]], list[str]], None]
    forbidden_tools: tuple[str, ...] = field(default_factory=tuple)


def _prepare_read_workspace(root: Path) -> None:
    (root / "sample.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")


def _prepare_write_workspace(root: Path) -> None:
    del root


def _prepare_edit_workspace(root: Path) -> None:
    (root / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")


def _prepare_glob_workspace(root: Path) -> None:
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("print('a')\n", encoding="utf-8")
    (root / "src" / "b.py").write_text("print('b')\n", encoding="utf-8")
    (root / "src" / "ignore.txt").write_text("skip\n", encoding="utf-8")


def _prepare_grep_workspace(root: Path) -> None:
    (root / "src").mkdir()
    (root / "src" / "service.py").write_text(
        "status = 'validation_failed'\nraise RuntimeError(status)\n",
        encoding="utf-8",
    )
    (root / "src" / "other.py").write_text("ok = True\n", encoding="utf-8")


def _prepare_bash_workspace(root: Path) -> None:
    del root


def _assert_read_effect(
    root: Path,
    result_payloads: list[dict[str, object]],
    used_tools: list[str],
) -> None:
    del root, used_tools
    assert any(
        payload["tool_name"] == READ_TOOL_NAME
        and payload["success"] is True
        and "2\tbeta" in "\n".join(str(item) for item in payload["content"])
        for payload in result_payloads
    )


def _assert_write_effect(
    root: Path,
    result_payloads: list[dict[str, object]],
    used_tools: list[str],
) -> None:
    del result_payloads, used_tools
    assert (root / "notes" / "todo.txt").read_text(encoding="utf-8") == "buy milk\n"


def _assert_edit_effect(
    root: Path,
    result_payloads: list[dict[str, object]],
    used_tools: list[str],
) -> None:
    del result_payloads, used_tools
    assert (root / "notes.txt").read_text(encoding="utf-8") == "alpha\ngamma\n"


def _assert_glob_effect(
    root: Path,
    result_payloads: list[dict[str, object]],
    used_tools: list[str],
) -> None:
    del root, used_tools
    assert any(
        payload["tool_name"] == GLOB_TOOL_NAME
        and payload["success"] is True
        and set(payload["content"]) >= {"src/a.py", "src/b.py"}
        for payload in result_payloads
    )


def _assert_grep_effect(
    root: Path,
    result_payloads: list[dict[str, object]],
    used_tools: list[str],
) -> None:
    del root, used_tools
    assert any(
        payload["tool_name"] == GREP_TOOL_NAME
        and payload["success"] is True
        and any("validation_failed" in str(item) for item in payload["content"])
        for payload in result_payloads
    )


def _assert_bash_effect(
    root: Path,
    result_payloads: list[dict[str, object]],
    used_tools: list[str],
) -> None:
    del used_tools
    assert any(
        payload["tool_name"] == BASH_TOOL_NAME
        and payload["success"] is True
        and payload["content"] == [str(root)]
        for payload in result_payloads
    )


CORE_TOOL_SELECTION_SCENARIOS = [
    ToolSelectionScenario(
        name="read_prefers_read_over_bash",
        prompt="Read sample.txt and tell me what the second line says.",
        enabled_tools=(READ_TOOL_NAME, BASH_TOOL_NAME),
        expected_tool=READ_TOOL_NAME,
        forbidden_tools=(BASH_TOOL_NAME,),
        prepare_workspace=_prepare_read_workspace,
        assert_effect=_assert_read_effect,
    ),
    ToolSelectionScenario(
        name="write_creates_file",
        prompt="Create a file notes/todo.txt with exactly this content: buy milk",
        enabled_tools=(WRITE_TOOL_NAME, BASH_TOOL_NAME),
        expected_tool=WRITE_TOOL_NAME,
        forbidden_tools=(BASH_TOOL_NAME,),
        prepare_workspace=_prepare_write_workspace,
        assert_effect=_assert_write_effect,
    ),
    ToolSelectionScenario(
        name="edit_updates_existing_file",
        prompt="In notes.txt, replace beta with gamma.",
        enabled_tools=(READ_TOOL_NAME, EDIT_TOOL_NAME, WRITE_TOOL_NAME, BASH_TOOL_NAME),
        expected_tool=EDIT_TOOL_NAME,
        forbidden_tools=(BASH_TOOL_NAME,),
        prepare_workspace=_prepare_edit_workspace,
        assert_effect=_assert_edit_effect,
    ),
    ToolSelectionScenario(
        name="glob_finds_python_files",
        prompt="Find all Python files under src.",
        enabled_tools=(GLOB_TOOL_NAME, GREP_TOOL_NAME, BASH_TOOL_NAME),
        expected_tool=GLOB_TOOL_NAME,
        forbidden_tools=(BASH_TOOL_NAME,),
        prepare_workspace=_prepare_glob_workspace,
        assert_effect=_assert_glob_effect,
    ),
    ToolSelectionScenario(
        name="grep_finds_symbol_usage",
        prompt="Find where the text validation_failed appears under src and show the matching lines.",
        enabled_tools=(GREP_TOOL_NAME, GLOB_TOOL_NAME, BASH_TOOL_NAME),
        expected_tool=GREP_TOOL_NAME,
        forbidden_tools=(BASH_TOOL_NAME,),
        prepare_workspace=_prepare_grep_workspace,
        assert_effect=_assert_grep_effect,
    ),
    ToolSelectionScenario(
        name="bash_runs_shell_command",
        prompt="Run pwd and tell me the current working directory.",
        enabled_tools=(READ_TOOL_NAME, GLOB_TOOL_NAME, BASH_TOOL_NAME),
        expected_tool=BASH_TOOL_NAME,
        prepare_workspace=_prepare_bash_workspace,
        assert_effect=_assert_bash_effect,
    ),
]


@pytest.mark.skipif(
    not _live_tool_selection_eval_enabled(),
    reason=(
        "set OPENAGENT_RUN_TOOL_SELECTION_EVAL=1 plus OPENAGENT_MODEL and "
        "OPENAGENT_BASE_URL to run live core-tool selection evals"
    ),
)
@pytest.mark.parametrize(
    "scenario",
    CORE_TOOL_SELECTION_SCENARIOS,
    ids=[scenario.name for scenario in CORE_TOOL_SELECTION_SCENARIOS],
)
def test_live_model_selects_and_executes_core_local_tools(
    tmp_path: Path,
    scenario: ToolSelectionScenario,
) -> None:
    require_live_model_endpoint()
    scenario.prepare_workspace(tmp_path)
    model = load_model_from_env()
    toolset = [
        tool
        for tool in create_builtin_toolset(root=str(tmp_path))
        if tool.name in set(scenario.enabled_tools)
    ]
    registry = StaticToolRegistry(toolset)
    harness = SimpleHarness(
        model=model,
        sessions=FileSessionStore(tmp_path / "sessions"),
        tools=registry,
        executor=SimpleToolExecutor(registry),
        session_root_dir=str(tmp_path / "sessions"),
    )
    session = harness.sessions.load_session(f"live_{scenario.name}")
    session.metadata["workdir"] = str(tmp_path)
    harness.sessions.save_session(session.session_id, session)

    events, terminal = harness.run_turn(scenario.prompt, session.session_id)

    used_tools = [
        str(event.payload["tool_name"])
        for event in events
        if event.event_type is RuntimeEventType.TOOL_STARTED
    ]
    result_payloads = [
        event.payload
        for event in events
        if event.event_type is RuntimeEventType.TOOL_RESULT
    ]

    assert terminal.status is TerminalStatus.COMPLETED
    assert used_tools, (
        "model did not use any tool; " + provider_summary()
    )
    assert scenario.expected_tool in used_tools, (
        f"expected tool {scenario.expected_tool}, got {used_tools}; {provider_summary()}"
    )
    for forbidden in scenario.forbidden_tools:
        assert forbidden not in used_tools, (
            f"unexpected tool {forbidden} used in scenario {scenario.name}; "
            f"used_tools={used_tools}"
        )
    scenario.assert_effect(tmp_path, result_payloads, used_tools)
