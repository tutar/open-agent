from dataclasses import dataclass

from openagent.harness import ModelTurnRequest, ModelTurnResponse
from openagent.object_model import TerminalStatus
from openagent.orchestration import InMemoryTaskManager
from openagent.profiles import TuiProfile
from openagent.sandbox import LocalSandbox, SandboxExecutionRequest


@dataclass(slots=True)
class StaticModel:
    message: str

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        del request
        return ModelTurnResponse(assistant_message=self.message)


def test_in_memory_task_manager_roundtrip() -> None:
    manager = InMemoryTaskManager()

    record = manager.create_task("bootstrap runtime", metadata={"stage": "init"})
    manager.update_task(record.task_id, TerminalStatus.FAILED.value, metadata={"stage": "done"})

    updated = manager.get_task(record.task_id)
    assert updated.status == TerminalStatus.FAILED.value
    assert updated.metadata == {"stage": "done"}


def test_local_sandbox_executes_allowed_command() -> None:
    sandbox = LocalSandbox(allowed_command_prefixes=["python"])

    result = sandbox.execute(
        SandboxExecutionRequest(command=["python", "-c", "print('sandbox-ok')"])
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "sandbox-ok"


def test_local_sandbox_rejects_disallowed_command() -> None:
    sandbox = LocalSandbox(allowed_command_prefixes=["python"])

    try:
        sandbox.execute(SandboxExecutionRequest(command=["bash", "-lc", "echo no"]))
    except PermissionError as exc:
        assert "not allowed" in str(exc)
    else:
        raise AssertionError("Expected PermissionError")


def test_tui_profile_creates_working_runtime() -> None:
    runtime = TuiProfile().create_runtime(model=StaticModel(message="profile-ready"))

    events, terminal = runtime.run_turn("hello", "sess_profile")

    assert terminal.status is TerminalStatus.COMPLETED
    assert events[1].payload["message"] == "profile-ready"
