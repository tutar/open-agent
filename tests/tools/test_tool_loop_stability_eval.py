from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import pytest

from openagent.harness.providers import load_model_from_env
from openagent.harness.runtime import FileModelIoCapture, SimpleHarness
from openagent.harness.runtime.core.terminal import TurnControl
from openagent.object_model import RuntimeEventType
from openagent.session import FileSessionStore
from openagent.tools import (
    GREP_TOOL_NAME,
    READ_TOOL_NAME,
    SimpleToolExecutor,
    StaticToolRegistry,
    create_local_code_edit_toolset,
)
from tests.tools.tool_eval_support import (
    configured_provider,
    live_tool_selection_eval_enabled,
    live_tool_eval_timeout_seconds,
    latest_provider_row_with_tool_messages,
    provider_tool_messages,
    provider_summary,
    require_live_model_endpoint,
    scenario_filter_names,
)


@dataclass(slots=True)
class ToolLoopScenario:
    name: str
    prompt: str
    expected_min_rounds: int


@dataclass(slots=True)
class ToolLoopRecord:
    scenario_name: str
    status: str
    terminal_status: str
    tool_rounds: int
    tool_sequence: list[str]
    detail: str


def _prepare_read_grep_workspace(root: Path) -> None:
    (root / "sympy" / "core").mkdir(parents=True)
    (root / "sympy" / "printing").mkdir(parents=True)
    (root / "sympy" / "core" / "_print_helpers.py").write_text(
        "\"\"\"\n"
        "Base class to provide str and repr hooks.\n"
        "\"\"\"\n\n"
        "class Printable:\n"
        "    def __str__(self):\n"
        "        return 'printable'\n"
        "    __repr__ = __str__\n",
        encoding="utf-8",
    )
    (root / "sympy" / "printing" / "defaults.py").write_text(
        "from sympy.core._print_helpers import Printable\n\n"
        "# alias for compatibility\n"
        "Printable.__module__ = __name__\n"
        "DefaultPrinting = Printable\n",
        encoding="utf-8",
    )


def _run_runtime_tool_loop(
    tmp_path: Path,
    scenario: ToolLoopScenario,
    *,
    timeout_seconds: float,
) -> ToolLoopRecord:
    _prepare_read_grep_workspace(tmp_path)
    model = load_model_from_env()
    toolset = [
        tool
        for tool in create_local_code_edit_toolset(root=str(tmp_path))
        if tool.name in {READ_TOOL_NAME, GREP_TOOL_NAME}
    ]
    registry = StaticToolRegistry(toolset)
    harness = SimpleHarness(
        model=model,
        sessions=FileSessionStore(tmp_path / "sessions"),
        tools=registry,
        executor=SimpleToolExecutor(registry),
        max_iterations=6,
        model_io_capture=FileModelIoCapture(tmp_path / "model-io"),
        session_root_dir=str(tmp_path / "sessions"),
    )
    session = harness.sessions.load_session(f"tool_loop_{scenario.name}")
    session.metadata["workdir"] = str(tmp_path)
    harness.sessions.save_session(session.session_id, session)

    events, terminal = harness.run_turn(
        scenario.prompt,
        session.session_id,
        control=TurnControl(timeout_seconds=timeout_seconds),
    )
    tool_sequence = [
        str(event.payload["tool_name"])
        for event in events
        if event.event_type is RuntimeEventType.TOOL_STARTED
    ]
    if len(tool_sequence) < scenario.expected_min_rounds:
        return ToolLoopRecord(
            scenario_name=scenario.name,
            status="insufficient_rounds",
            terminal_status=terminal.status.value,
            tool_rounds=len(tool_sequence),
            tool_sequence=tool_sequence,
            detail="runtime completed before the expected number of tool rounds",
        )
    return ToolLoopRecord(
        scenario_name=scenario.name,
        status="completed",
        terminal_status=terminal.status.value,
        tool_rounds=len(tool_sequence),
        tool_sequence=tool_sequence,
        detail="runtime preserved structured tool use through the required rounds",
    )


def _tool_loop_projection_failure_details(model_io_root: Path) -> str:
    row = latest_provider_row_with_tool_messages(model_io_root)
    if row is None:
        return "no provider request with tool messages was captured"
    return (
        f"provider_projected_messages={json.dumps(row.get('provider_projected_messages'), ensure_ascii=False)} "
        f"assembled_request_messages={json.dumps(row.get('assembled_request', {}).get('messages', []), ensure_ascii=False)}"
    )


def _assert_openai_tool_result_projection(model_io_root: Path) -> None:
    if configured_provider() != "openai":
        pytest.skip("tool result wire-format assertion is only defined for OpenAI-compatible providers")
    row = latest_provider_row_with_tool_messages(model_io_root)
    assert row is not None, (
        "tool result never appeared in a follow-up provider request; "
        f"{provider_summary()} {_tool_loop_projection_failure_details(model_io_root)}"
    )
    tool_messages = provider_tool_messages(row)
    assert tool_messages, (
        "expected at least one provider-facing tool message; "
        f"{provider_summary()} {_tool_loop_projection_failure_details(model_io_root)}"
    )
    for message in tool_messages:
        assert "tool_call_id" in message, (
            "provider-facing tool message is missing tool_call_id; "
            f"{provider_summary()} {_tool_loop_projection_failure_details(model_io_root)}"
        )
        assert "metadata" not in message, (
            "provider-facing tool message leaked canonical metadata instead of wire fields; "
            f"{provider_summary()} {_tool_loop_projection_failure_details(model_io_root)}"
        )
        assert isinstance(message.get("content"), str), (
            "provider-facing tool message content must be a string for OpenAI-compatible payloads; "
            f"{provider_summary()} {_tool_loop_projection_failure_details(model_io_root)}"
        )


TOOL_LOOP_SCENARIOS = [
    ToolLoopScenario(
        name="read_read_grep_runtime_loop",
        prompt=(
            "Inspect sympy/core/_print_helpers.py, then sympy/printing/defaults.py, "
            "then search for DefaultPrinting under sympy/printing. Use tools for each step "
            "and do not answer from memory."
        ),
        expected_min_rounds=3,
    )
]


@pytest.mark.skipif(
    not live_tool_selection_eval_enabled(),
    reason=(
        "set OPENAGENT_RUN_TOOL_SELECTION_EVAL=1 plus OPENAGENT_MODEL and "
        "OPENAGENT_BASE_URL to run live runtime tool-loop stability evals"
    ),
)
@pytest.mark.parametrize(
    "scenario",
    [
        scenario
        for scenario in TOOL_LOOP_SCENARIOS
        if (
            not scenario_filter_names("OPENAGENT_TOOL_LOOP_SCENARIOS")
            or scenario.name in scenario_filter_names("OPENAGENT_TOOL_LOOP_SCENARIOS")
        )
    ],
    ids=[
        scenario.name
        for scenario in TOOL_LOOP_SCENARIOS
        if (
            not scenario_filter_names("OPENAGENT_TOOL_LOOP_SCENARIOS")
            or scenario.name in scenario_filter_names("OPENAGENT_TOOL_LOOP_SCENARIOS")
        )
    ],
)
def test_live_runtime_tool_loop_stability(
    tmp_path: Path,
    scenario: ToolLoopScenario,
) -> None:
    require_live_model_endpoint()
    record = _run_runtime_tool_loop(
        tmp_path,
        scenario,
        timeout_seconds=live_tool_eval_timeout_seconds(),
    )
    report = {
        "provider": provider_summary(),
        "record": asdict(record),
    }
    report_path = tmp_path / f"{scenario.name}.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    assert record.status == "completed", report
    _assert_openai_tool_result_projection(tmp_path / "model-io")
    assert report_path.exists()
