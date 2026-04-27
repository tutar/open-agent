from dataclasses import dataclass
from pathlib import Path

from openagent.harness.runtime import ModelTurnRequest, ModelTurnResponse
from openagent.local import create_file_runtime
from openagent.object_model import TerminalStatus


@dataclass(slots=True)
class RecordingModel:
    response: ModelTurnResponse
    last_request: ModelTurnRequest | None = None

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        self.last_request = request
        return self.response


def _write_role(root: Path, role_id: str) -> None:
    role_root = root / "roles" / role_id
    (role_root / "memory").mkdir(parents=True)
    (role_root / "ROLE.md").write_text(
        "---\n"
        f"role_id: {role_id}\n"
        "recommended_models: [gpt-5.4]\n"
        "skills:\n"
        "  - summarize\n"
        "---\n"
        "Role metadata only.\n",
        encoding="utf-8",
    )
    (role_root / "USER.md").write_text(
        "Identity: research role.\nAlways cite repo evidence.\n",
        encoding="utf-8",
    )


def _write_skill_plugin(root: Path, role_id: str) -> None:
    skill_root = root / f"agent_{role_id}" / "local-agent" / "plugins" / "summarize"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\n"
        "name: Summarize\n"
        "description: Summarize a document.\n"
        "---\n"
        "Summarize {topic} for the current user.\n",
        encoding="utf-8",
    )


def test_role_user_markdown_enters_instruction_assembly_and_mounts_skill_tool(
    tmp_path: Path,
) -> None:
    _write_role(tmp_path, "research")
    _write_skill_plugin(tmp_path, "research")
    model = RecordingModel(response=ModelTurnResponse(assistant_message="ok"))

    runtime = create_file_runtime(
        model=model,
        session_root=str(tmp_path / "sessions"),
        openagent_root=str(tmp_path),
        role_id="research",
    )

    events, terminal = runtime.run_turn("hello", "sess-role")

    assert terminal.status is TerminalStatus.COMPLETED
    assert events[-1].event_type.value == "turn_completed"
    assert model.last_request is not None
    assert model.last_request.system_context
    content = str(model.last_request.system_context[0]["payload"]["content"])
    assert "Identity: research role." in content
    assert "Always cite repo evidence." in content
    assert "Skill" in [tool.name for tool in runtime.tools.list_tools()]
    assert model.last_request.request_metadata["role_id"] == "research"
    assert model.last_request.request_metadata["recommended_models"] == ["gpt-5.4"]


def test_role_memory_store_is_bound_to_role_root(tmp_path: Path) -> None:
    _write_role(tmp_path, "research")
    _write_skill_plugin(tmp_path, "research")
    model = RecordingModel(response=ModelTurnResponse(assistant_message="noted"))
    runtime = create_file_runtime(
        model=model,
        session_root=str(tmp_path / "sessions"),
        openagent_root=str(tmp_path),
        role_id="research",
    )

    runtime.run_turn("Remember that the release codename is atlas", "sess-memory")
    assert runtime.last_memory_consolidation_job_id is not None
    result = runtime.memory_store.wait_for_job(runtime.last_memory_consolidation_job_id)

    assert result.new_records
    assert Path(runtime.memory_store._root) == (  # type: ignore[attr-defined]
        tmp_path / "roles" / "research" / "memory" / "records"
    )
