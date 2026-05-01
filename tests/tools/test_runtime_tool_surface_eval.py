from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import pytest

from openagent.harness.providers import load_model_from_env
from openagent.harness.runtime import SimpleHarness
from openagent.host.config import OpenAgentHostConfig
from openagent.local import create_file_runtime
from openagent.object_model import RuntimeEventType, TerminalStatus
from openagent.session import FileSessionStore
from openagent.tools import SimpleToolExecutor, StaticToolRegistry
from tests.tools.test_tool_selection_eval import (
    CORE_TOOL_SELECTION_SCENARIOS,
    ToolSelectionScenario,
)
from tests.tools.tool_eval_support import (
    live_tool_selection_eval_enabled,
    load_repo_env,
    provider_summary,
    require_live_model_endpoint,
)

load_repo_env()


@dataclass(slots=True)
class RuntimeToolEvalRecord:
    tool_name: str
    source: str
    scenario_name: str | None
    status: str
    detail: str


def _build_runtime(tmp_path: Path):
    config = OpenAgentHostConfig.from_env()
    return create_file_runtime(
        model=load_model_from_env(),
        session_root=str(tmp_path / "sessions"),
        openagent_root=config.openagent_root,
        role_id=config.role_id,
    )


def _run_scenario_against_runtime(
    tmp_path: Path,
    runtime,
    scenario: ToolSelectionScenario,
) -> RuntimeToolEvalRecord:
    scenario_root = tmp_path / scenario.name
    scenario_root.mkdir(parents=True, exist_ok=True)
    scenario.prepare_workspace(scenario_root)
    toolset = runtime.tools.list_tools()
    registry = StaticToolRegistry(toolset)
    harness = SimpleHarness(
        model=runtime.model,
        sessions=FileSessionStore(scenario_root / "sessions"),
        tools=registry,
        executor=SimpleToolExecutor(registry),
        openagent_root=runtime.openagent_root,
        role_id=runtime.role_id,
        role_definition=runtime.role_definition,
    )
    session = harness.sessions.load_session(f"runtime_eval_{scenario.name}")
    session.metadata["workdir"] = str(scenario_root)
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
    if terminal.status is not TerminalStatus.COMPLETED:
        return RuntimeToolEvalRecord(
            tool_name=scenario.expected_tool,
            source="runtime",
            scenario_name=scenario.name,
            status="failed",
            detail=f"terminal={terminal.status.value} used_tools={used_tools}",
        )
    if scenario.expected_tool not in used_tools:
        return RuntimeToolEvalRecord(
            tool_name=scenario.expected_tool,
            source="runtime",
            scenario_name=scenario.name,
            status="wrong_tool",
            detail=f"used_tools={used_tools}",
        )
    if any(forbidden in used_tools for forbidden in scenario.forbidden_tools):
        return RuntimeToolEvalRecord(
            tool_name=scenario.expected_tool,
            source="runtime",
            scenario_name=scenario.name,
            status="forbidden_tool_used",
            detail=f"used_tools={used_tools}",
        )
    scenario.assert_effect(scenario_root, result_payloads, used_tools)
    return RuntimeToolEvalRecord(
        tool_name=scenario.expected_tool,
        source="runtime",
        scenario_name=scenario.name,
        status="completed",
        detail=f"used_tools={used_tools}",
    )


def test_runtime_tool_surface_exposes_actual_registry(tmp_path: Path) -> None:
    runtime = _build_runtime(tmp_path)
    records = runtime.tools.list_tool_records()
    tool_names = [record.tool_name for record in records]
    report = {
        "provider": provider_summary(),
        "tool_count": len(records),
        "tool_names": tool_names,
        "sources": {record.tool_name: record.source.value for record in records},
    }
    report_path = tmp_path / "runtime_tool_surface.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    assert report["tool_count"] >= 6
    assert {"Read", "Write", "Edit", "Glob", "Grep", "Bash"}.issubset(set(tool_names))
    assert report_path.exists()


@pytest.mark.skipif(
    not live_tool_selection_eval_enabled(),
    reason=(
        "set OPENAGENT_RUN_TOOL_SELECTION_EVAL=1 plus OPENAGENT_MODEL and "
        "OPENAGENT_BASE_URL to run runtime tool-surface evals"
    ),
)
def test_live_runtime_tool_surface_report(tmp_path: Path) -> None:
    require_live_model_endpoint()
    runtime = _build_runtime(tmp_path)
    records = runtime.tools.list_tool_records()
    tool_names = {record.tool_name for record in records}
    scenario_by_tool = {
        scenario.expected_tool: scenario
        for scenario in CORE_TOOL_SELECTION_SCENARIOS
        if scenario.expected_tool in tool_names
    }

    eval_records: list[RuntimeToolEvalRecord] = []
    for record in records:
        scenario = scenario_by_tool.get(record.tool_name)
        if scenario is None:
            eval_records.append(
                RuntimeToolEvalRecord(
                    tool_name=record.tool_name,
                    source=record.source.value,
                    scenario_name=None,
                    status="disclosed_only",
                    detail="no live scenario registered for this tool",
                )
            )
            continue
        eval_records.append(_run_scenario_against_runtime(tmp_path, runtime, scenario))

    report = {
        "provider": provider_summary(),
        "records": [asdict(record) for record in eval_records],
    }
    report_path = tmp_path / "runtime_tool_surface_eval.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    scenario_records = [record for record in eval_records if record.scenario_name is not None]
    assert scenario_records, "no live scenarios matched the current runtime tool surface"
    assert all(record.status == "completed" for record in scenario_records), report
    assert report_path.exists()
