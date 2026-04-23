import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from openagent.durable_memory import FileMemoryStore
from openagent.harness.runtime import ModelTurnRequest, ModelTurnResponse, SimpleHarness
from openagent.session import InMemorySessionStore, SessionMessage, SessionRecord
from openagent.tools import SimpleToolExecutor, StaticToolRegistry


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


@dataclass(slots=True)
class ScriptedModel:
    responses: list[ModelTurnResponse]

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        del request
        return self.responses.pop(0)


def test_memory_recall_and_consolidation_matches_golden(tmp_path: Path) -> None:
    golden = _load_golden("memory-recall-and-consolidation.json")
    memory_store = FileMemoryStore(tmp_path / "memory")
    consolidation = memory_store.consolidate(
        "golden_memory",
        [
            SessionMessage(role="user", content="Remember my favorite city is Hangzhou"),
            SessionMessage(
                role="assistant",
                content="I'll remember that your favorite city is Hangzhou.",
            ),
        ],
    )
    harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="ok")]),
        sessions=InMemorySessionStore(),
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
        memory_store=memory_store,
    )

    request = harness.build_model_input(
        SessionRecord(
            session_id="golden_memory",
            messages=[SessionMessage(role="user", content="What is my favorite city?")],
        ),
        [],
    )
    restored_store = FileMemoryStore(tmp_path / "memory")
    restored_harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="ok")]),
        sessions=InMemorySessionStore(),
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
        memory_store=restored_store,
    )
    restored_request = restored_harness.build_model_input(
        SessionRecord(
            session_id="golden_memory",
            messages=[SessionMessage(role="user", content="favorite city?")],
        ),
        [],
    )

    assert consolidation.new_records
    assert request.memory_context
    assert request.messages[-1] == {"role": "user", "content": "What is my favorite city?"}
    assert [item["kind"] for item in request.startup_contexts] == ["session_start", "turn_zero"]
    assert restored_request.memory_context
    assert (
        golden["constraints"][0]
        == "recalled memory enters context assembly rather than transcript rewrite"
    )
