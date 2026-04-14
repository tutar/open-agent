from openagent import (
    SPEC_VERSION,
    CapabilityView,
    RequiresAction,
    RuntimeEvent,
    RuntimeEventType,
    SchemaEnvelope,
    TaskRecord,
    TerminalState,
    TerminalStatus,
    ToolResult,
    __version__,
)
from openagent.harness import Harness
from openagent.orchestration import TaskManager
from openagent.profiles import TuiProfile
from openagent.sandbox import Sandbox
from openagent.session import SessionStore
from openagent.tools import ToolDefinition, ToolExecutor, ToolRegistry


def test_public_exports_are_importable() -> None:
    assert __version__ == "0.1.0"
    assert SPEC_VERSION == "0.1"
    assert TuiProfile().name == "tui"
    assert Harness is not None
    assert SessionStore is not None
    assert ToolDefinition is not None
    assert ToolRegistry is not None
    assert ToolExecutor is not None
    assert Sandbox is not None
    assert TaskManager is not None


def test_object_models_support_dict_serialization() -> None:
    event = RuntimeEvent(
        event_type=RuntimeEventType.ASSISTANT_MESSAGE,
        event_id="evt_1",
        timestamp="2026-04-14T00:00:00Z",
        session_id="sess_1",
        payload={"message": "hello"},
    )
    terminal = TerminalState(status=TerminalStatus.COMPLETED, reason="done")
    action = RequiresAction(
        action_type="approval",
        session_id="sess_1",
        description="Need approval",
    )
    result = ToolResult(tool_name="echo", success=True, content=["ok"])
    capability = CapabilityView(tools=["bash"], skills=["summarize"])
    task = TaskRecord(
        task_id="task_1",
        type="turn",
        status="running",
        description="Run a turn",
        start_time="2026-04-14T00:00:00Z",
    )
    envelope = SchemaEnvelope(
        schema_name="RuntimeEvent",
        schema_version="0.1",
        payload=event.to_dict(),
    )

    assert event.to_dict()["event_type"] == "assistant_message"
    assert terminal.to_dict()["status"] == "completed"
    assert action.to_dict()["action_type"] == "approval"
    assert result.to_dict()["tool_name"] == "echo"
    assert capability.to_dict()["tools"] == ["bash"]
    assert task.to_dict()["task_id"] == "task_1"
    assert envelope.to_dict()["schema_name"] == "RuntimeEvent"
