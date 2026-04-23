import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from openagent.harness.context_engineering.governance.context_governance import (
    ContextGovernance,
)
from openagent.harness.runtime import ModelTurnRequest, ModelTurnResponse, SimpleHarness
from openagent.object_model import RuntimeEvent, RuntimeEventType, ToolResult
from openagent.session import (
    InMemorySessionStore,
    SessionMessage,
    SessionStatus,
)
from openagent.tools import PermissionDecision, SimpleToolExecutor, StaticToolRegistry, ToolCall


def _resolve_golden_dir() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "agent-spec" / "conformance" / "golden"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("Could not locate agent-spec/conformance/golden from test path")


GOLDEN_DIR = _resolve_golden_dir()


def _load_golden(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((GOLDEN_DIR / name).read_text(encoding="utf-8")))


def _event_types(events: list[RuntimeEvent]) -> list[str]:
    mapping = {
        RuntimeEventType.ASSISTANT_MESSAGE: "assistant_output",
    }
    return [mapping.get(event.event_type, event.event_type.value) for event in events]


@dataclass(slots=True)
class FakeTool:
    name: str
    permission: PermissionDecision = PermissionDecision.ALLOW
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})

    def description(self) -> str:
        return self.name

    def call(self, arguments: dict[str, Any]) -> ToolResult:
        text = arguments.get("text", "ok")
        return ToolResult(tool_name=self.name, success=True, content=[str(text)])

    def check_permissions(self, arguments: dict[str, Any]) -> str:
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


def test_basic_turn_matches_golden() -> None:
    golden = _load_golden("basic-turn.events.json")
    store = InMemorySessionStore()
    harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="hello")]),
        sessions=store,
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
    )

    events, terminal = harness.run_turn("hi", "golden_basic")

    assert _event_types(events) == [item["type"] for item in golden["events"]]
    assert terminal.status.value == golden["events"][-1]["terminal_state"]
    assert [
        SessionStatus.IDLE.value,
        SessionStatus.RUNNING.value,
        store.load_session("golden_basic").status.value,
    ] == golden["lifecycle"]


def test_tool_roundtrip_matches_golden() -> None:
    golden = _load_golden("tool-call-roundtrip.events.json")
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

    events, terminal = harness.run_turn("use tool", "golden_tool")
    comparable_events = [events[0], events[1], events[2], events[-1]]

    assert _event_types(comparable_events) == [item["type"] for item in golden["events"]]
    assert terminal.status.value == golden["events"][-1]["terminal_state"]
    assert (
        comparable_events[1].payload["tool_use_id"] == comparable_events[2].payload["tool_use_id"]
    )


def test_requires_action_matches_golden() -> None:
    golden = _load_golden("requires-action-approval.events.json")
    tool = FakeTool(name="admin", permission=PermissionDecision.ASK)
    registry = StaticToolRegistry([tool])
    store = InMemorySessionStore()
    harness = SimpleHarness(
        model=ScriptedModel(
            [
                ModelTurnResponse(
                    tool_calls=[ToolCall(tool_name="admin", arguments={"text": "rotate"})]
                ),
                ModelTurnResponse(assistant_message="done after approval"),
            ]
        ),
        sessions=store,
        tools=registry,
        executor=SimpleToolExecutor(registry),
    )

    first_events, _ = harness.run_turn("rotate", "golden_approval")
    first_session = store.load_session("golden_approval")
    assert [item["type"] for item in golden["phase_1"]["events"]] == _event_types(
        [first_events[0], first_events[-1]]
    )
    assert first_session.status.value == golden["phase_1"]["lifecycle"][-1]
    assert first_events[-1].payload["tool_name"] == "admin"
    assert first_events[-1].payload["tool_use_id"] == "toolu_1"

    second_events, second_terminal = harness.continue_turn("golden_approval", approved=True)
    second_session = store.load_session("golden_approval")
    assert _event_types([second_events[0], second_events[1], second_events[-1]]) == [
        item["type"] for item in golden["phase_2"]["events"]
    ]
    assert second_events[0].payload["tool_use_id"] == second_events[1].payload["tool_use_id"]
    assert second_terminal.status.value == golden["phase_2"]["events"][-1]["terminal_state"]
    assert second_session.status.value == golden["phase_2"]["lifecycle"][-1]


def test_policy_ask_deny_allow_matches_golden() -> None:
    golden = _load_golden("policy-ask-deny-allow.json")
    allow_tool = FakeTool(name="allow_tool", permission=PermissionDecision.ALLOW)
    ask_tool = FakeTool(name="ask_tool", permission=PermissionDecision.ASK)
    deny_tool = FakeTool(name="deny_tool", permission=PermissionDecision.DENY)
    registry = StaticToolRegistry([allow_tool, ask_tool, deny_tool])
    store = InMemorySessionStore()

    allow_harness = SimpleHarness(
        model=ScriptedModel(
            [
                ModelTurnResponse(
                    tool_calls=[ToolCall(tool_name="allow_tool", arguments={"text": "run"})]
                ),
                ModelTurnResponse(assistant_message="allow done"),
            ]
        ),
        sessions=store,
        tools=registry,
        executor=SimpleToolExecutor(registry),
    )
    allow_events, _ = allow_harness.run_turn("allow", "golden_policy_allow")

    ask_harness = SimpleHarness(
        model=ScriptedModel(
            [
                ModelTurnResponse(
                    tool_calls=[ToolCall(tool_name="ask_tool", arguments={"text": "run"})]
                ),
                ModelTurnResponse(assistant_message="ask done"),
            ]
        ),
        sessions=store,
        tools=registry,
        executor=SimpleToolExecutor(registry),
    )
    ask_events, _ = ask_harness.run_turn("ask", "golden_policy_ask")
    resumed_events, _ = ask_harness.continue_turn("golden_policy_ask", approved=True)

    deny_harness = SimpleHarness(
        model=ScriptedModel(
            [ModelTurnResponse(tool_calls=[ToolCall(tool_name="deny_tool", arguments={})])]
        ),
        sessions=store,
        tools=registry,
        executor=SimpleToolExecutor(registry),
    )
    deny_events, _ = deny_harness.run_turn("deny", "golden_policy_deny")

    requires_action_payload = ask_events[-1].payload
    assert golden["constraints"][0] == "allow must continue into normal tool execution"
    assert RuntimeEventType.TOOL_RESULT in [event.event_type for event in allow_events]
    assert golden["constraints"][1] == "ask must project to a structured requires_action object"
    assert requires_action_payload["tool_name"] == "ask_tool"
    assert requires_action_payload["request_id"] is not None
    assert requires_action_payload["resumable"] is True
    assert golden["constraints"][2] == "deny must not create a resumable pending action"
    assert all(event.event_type is not RuntimeEventType.REQUIRES_ACTION for event in deny_events)
    assert golden["constraints"][3] == "approval resume must bind back to the original tool_use_id"
    assert resumed_events[0].payload["tool_use_id"] == resumed_events[1].payload["tool_use_id"]


def test_prompt_cache_stable_prefix_matches_golden() -> None:
    golden = _load_golden("prompt-cache-stable-prefix.json")
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
    assert golden["constraints"][0] == (
        "stable prefix remains unchanged across turns that only modify dynamic suffix"
    )


def test_prompt_cache_dynamic_suffix_matches_golden() -> None:
    golden = _load_golden("prompt-cache-dynamic-suffix.json")
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

    assert first.dynamic_suffix_key != second.dynamic_suffix_key
    assert golden["constraints"][1] == "dynamic context must not be modeled as transcript rewrite"


def test_prompt_cache_break_detection_matches_golden() -> None:
    golden = _load_golden("prompt-cache-break-detection.json")
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
    assert break_result.reason in golden["valid_reasons"]
    assert golden["constraints"][0] == "cache break remains structurally identifiable"


def test_prompt_cache_fork_sharing_matches_golden() -> None:
    golden = _load_golden("prompt-cache-fork-sharing.json")
    governance = ContextGovernance()
    parent = governance.snapshot_prompt_cache(
        [
            SessionMessage(role="system", content="stay concise"),
            SessionMessage(role="user", content="static topic"),
            SessionMessage(role="assistant", content="parent dynamic"),
        ],
        ["echo"],
        ttl_bucket="1h",
        model_identity="gpt-local",
    )
    child = governance.fork_prompt_cache(
        parent,
        [SessionMessage(role="user", content="child dynamic suffix")],
        skip_cache_write=True,
    )

    assert child.stable_prefix_key == parent.stable_prefix_key
    assert child.tool_surface_key == parent.tool_surface_key
    assert child.ttl_bucket == parent.ttl_bucket
    assert child.model_identity == parent.model_identity
    assert child.skip_cache_write is True
    assert golden["constraints"][0] == "fork child inherits cache-critical parameters from parent"


def test_prompt_cache_strategy_equivalence_matches_golden() -> None:
    golden = _load_golden("prompt-cache-strategy-equivalence.json")
    governance = ContextGovernance()
    messages = [
        SessionMessage(role="system", content="stay concise"),
        SessionMessage(role="user", content="static topic"),
        SessionMessage(role="assistant", content="dynamic one"),
        SessionMessage(role="user", content="dynamic two"),
    ]
    strategies = ["anthropic_native", "openclaw_mediated", "fallback"]
    snapshots = [
        governance.snapshot_prompt_cache_with_strategy(messages, ["echo"], strategy)
        for strategy in strategies
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
    assert (
        golden["constraints"][1]
        == "strategy changes do not force upper-layer behavior drift"
    )
