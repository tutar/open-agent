import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from openagent.harness.runtime import ModelTurnRequest, ModelTurnResponse, SimpleHarness
from openagent.object_model import RuntimeEventType, TerminalStatus
from openagent.session import FileSessionStore, SessionMessage, SessionRecord
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


def test_session_resume_matches_golden(tmp_path: Path) -> None:
    golden = _load_golden("session-resume.event-log.json")
    session_root = tmp_path / "sessions"
    initial_store = FileSessionStore(session_root)
    first_harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="first reply")]),
        sessions=initial_store,
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
    )

    first_harness.run_turn("first", "golden_resume")
    before_restore = initial_store.load_session("golden_resume")
    before_event_count = len(before_restore.events)

    restored_store = FileSessionStore(session_root)
    second_harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="second reply")]),
        sessions=restored_store,
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
    )
    second_events, terminal = second_harness.run_turn("second", "golden_resume")
    restored = restored_store.load_session("golden_resume")

    assert terminal.status is TerminalStatus.COMPLETED
    assert restored.session_id == "golden_resume"
    assert len(restored.events) > before_event_count
    assert second_events[-1].event_type is RuntimeEventType.TURN_COMPLETED
    assert restored.messages[-2].content == "second"
    assert restored.messages[-1].content == "second reply"
    assert golden["requirements"][0] == "session id remains stable across restore"


def test_instruction_markdown_loading_precedence_matches_golden(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    golden = _load_golden("instruction-markdown-loading-precedence.json")
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    (home / ".openagent").mkdir(parents=True)
    (home / ".openagent" / "AGENTS.md").write_text("Home guidance\nPriority: home\n", "utf-8")
    workdir = tmp_path / "repo"
    subtree = workdir / "pkg" / "feature"
    sibling = workdir / "pkg" / "other"
    subtree.mkdir(parents=True)
    sibling.mkdir(parents=True)
    (workdir / "AGENTS.md").write_text("Repo guidance\nPriority: repo\n", "utf-8")
    (subtree / "AGENTS.md").write_text("Feature guidance\nPriority: subtree\n", "utf-8")
    (sibling / "AGENTS.md").write_text("Sibling guidance\n", "utf-8")

    harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="ok")]),
        sessions=FileSessionStore(tmp_path / "sessions"),
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
    )
    request = harness.build_model_input(
        SessionRecord(
            session_id="golden_agents_md",
            messages=[SessionMessage(role="user", content="edit pkg/feature/main.py")],
            metadata={"workdir": str(workdir), "target_path": "pkg/feature/main.py"},
        ),
        [],
    )

    assert golden["expected"]["ordered_merge"] is True
    assert request.system_context
    content = str(request.system_context[0]["payload"]["content"])
    assert "Home guidance" in content
    assert "Repo guidance" in content
    assert "Feature guidance" in content
    assert "Sibling guidance" not in content
    assert "Priority: subtree" in content
    assert golden["expected"]["transcript_unchanged"] is True
    assert request.messages[-1] == {"role": "user", "content": "edit pkg/feature/main.py"}
    assert [item["kind"] for item in request.startup_contexts] == ["session_start", "turn_zero"]
