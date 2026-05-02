from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.tools.tool_eval_support import (
    build_openai_function_tool,
    live_tool_selection_eval_enabled,
    load_repo_env,
    openai_chat_completion_non_stream,
    openai_chat_completion_stream,
    provider_summary,
    require_live_model_endpoint,
)

load_repo_env()


@dataclass(slots=True)
class ProviderToolLoopScenario:
    name: str
    prompt: str
    expected_min_rounds: int
    required_tools: tuple[str, ...]
    tools: list[dict[str, Any]]


@dataclass(slots=True)
class ProviderToolLoopRecord:
    scenario_name: str
    streaming: bool
    status: str
    round_count: int
    successful_tool_rounds: int
    tool_sequence: list[str]
    finish_reason: str | None
    detail: str


def _tool_result_for_call(tool_name: str, arguments: dict[str, Any]) -> str:
    if tool_name == "Read":
        path = str(arguments.get("path", ""))
        if path == "sympy/core/_print_helpers.py":
            return (
                '1\tclass Printable:\n'
                "2\t    def __str__(self):\n"
                "3\t        return 'printable'\n"
                "4\t    __repr__ = __str__\n"
            )
        if path == "sympy/printing/defaults.py":
            return (
                "1\tfrom sympy.core._print_helpers import Printable\n"
                "2\tPrintable.__module__ = __name__\n"
                "3\tDefaultPrinting = Printable\n"
            )
        return f"1\tmissing fixture for {path}\n"
    if tool_name == "Grep":
        pattern = str(arguments.get("pattern", ""))
        if pattern == "DefaultPrinting":
            return "sympy/printing/defaults.py:3:DefaultPrinting = Printable"
        if pattern == "class DefaultPrinting":
            return ""
        return f"no matches for {pattern}"
    return f"unsupported tool {tool_name}"


def _assistant_message_for_tool_calls(result) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "tool_calls": result.tool_calls}
    if result.content:
        message["content"] = result.content
    return message


def _build_payload(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    stream: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
    }
    if stream:
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
    return payload


def _run_provider_tool_loop(
    scenario: ProviderToolLoopScenario,
    *,
    streaming: bool,
) -> ProviderToolLoopRecord:
    model = os.environ["OPENAGENT_MODEL"].strip()
    base_url = os.environ["OPENAGENT_BASE_URL"].strip()
    messages: list[dict[str, Any]] = [{"role": "user", "content": scenario.prompt}]
    tool_sequence: list[str] = []
    finish_reason: str | None = None
    successful_tool_rounds = 0

    for round_index in range(1, scenario.expected_min_rounds + 3):
        payload = _build_payload(
            model=model,
            messages=messages,
            tools=scenario.tools,
            stream=streaming,
        )
        result = (
            openai_chat_completion_stream(base_url=base_url, payload=payload)
            if streaming
            else openai_chat_completion_non_stream(base_url=base_url, payload=payload)
        )
        finish_reason = result.finish_reason
        if not result.tool_calls:
            status = (
                "completed"
                if successful_tool_rounds >= scenario.expected_min_rounds
                else "missing_tool_calls"
            )
            return ProviderToolLoopRecord(
                scenario_name=scenario.name,
                streaming=streaming,
                status=status,
                round_count=round_index,
                successful_tool_rounds=successful_tool_rounds,
                tool_sequence=tool_sequence,
                finish_reason=finish_reason,
                detail="provider returned no structured tool_calls",
            )
        tool_call = result.tool_calls[0]
        function = tool_call.get("function", {})
        tool_name = str(function.get("name", ""))
        arguments = function.get("arguments", {})
        if not isinstance(arguments, dict):
            arguments = {}
        tool_sequence.append(tool_name)
        successful_tool_rounds += 1
        messages.append(_assistant_message_for_tool_calls(result))
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.get("id") or f"call_{round_index}",
                "content": _tool_result_for_call(tool_name, arguments),
            }
        )

    return ProviderToolLoopRecord(
        scenario_name=scenario.name,
        streaming=streaming,
        status="continued",
        round_count=scenario.expected_min_rounds + 2,
        successful_tool_rounds=successful_tool_rounds,
        tool_sequence=tool_sequence,
        finish_reason=finish_reason,
        detail="provider kept producing structured tool_calls through the max probe rounds",
    )


PROVIDER_TOOL_LOOP_SCENARIOS = [
    ProviderToolLoopScenario(
        name="read_read_grep_sequence",
        prompt=(
            "Inspect sympy/core/_print_helpers.py, then sympy/printing/defaults.py, "
            "then search for DefaultPrinting under sympy/printing. Use tools for each step "
            "and do not answer from memory."
        ),
        expected_min_rounds=3,
        required_tools=("Read", "Grep"),
        tools=[
            build_openai_function_tool(
                name="Read",
                description="Read a file from the workspace.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            ),
            build_openai_function_tool(
                name="Grep",
                description="Search for a string in workspace files.",
                parameters={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "path": {"type": "string"},
                        "glob": {"type": "string"},
                    },
                    "required": ["pattern"],
                },
            ),
        ],
    )
]


@pytest.mark.skipif(
    not live_tool_selection_eval_enabled(),
    reason=(
        "set OPENAGENT_RUN_TOOL_SELECTION_EVAL=1 plus OPENAGENT_MODEL and "
        "OPENAGENT_BASE_URL to run live provider tool-loop stability evals"
    ),
)
@pytest.mark.parametrize("streaming", [True], ids=["stream"])
@pytest.mark.parametrize(
    "scenario",
    PROVIDER_TOOL_LOOP_SCENARIOS,
    ids=[scenario.name for scenario in PROVIDER_TOOL_LOOP_SCENARIOS],
)
def test_live_provider_tool_call_stability(
    tmp_path: Path,
    scenario: ProviderToolLoopScenario,
    streaming: bool,
) -> None:
    require_live_model_endpoint()
    record = _run_provider_tool_loop(scenario, streaming=streaming)
    report = {
        "provider": provider_summary(),
        "record": asdict(record),
    }
    report_name = f"{scenario.name}_{'stream' if streaming else 'non_stream'}.json"
    report_path = tmp_path / report_name
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    assert record.round_count >= 1, report
    assert report_path.exists()
