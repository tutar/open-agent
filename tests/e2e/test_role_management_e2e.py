from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from openagent.harness.runtime import ModelTurnRequest, ModelTurnResponse
from openagent.local import create_file_runtime
from openagent.object_model import TerminalStatus
from openagent.tools import ToolCall


@dataclass(slots=True)
class RoleWorkflowModel:
    last_request: ModelTurnRequest | None = None

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        self.last_request = request
        transcript = request.messages
        if transcript[-1]["role"] == "user":
            return ModelTurnResponse(
                tool_calls=[ToolCall(tool_name="Skill", arguments={"skill_id": "summarize"})]
            )
        tool_messages = [message for message in transcript if message.get("role") == "tool"]
        if len(tool_messages) == 1:
            return ModelTurnResponse(
                tool_calls=[ToolCall(tool_name="mcp__docs__echo", arguments={"text": "hello"})]
            )
        return ModelTurnResponse(assistant_message="done")


@dataclass(slots=True)
class RoleMemoryModel:
    last_request: ModelTurnRequest | None = None

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        self.last_request = request
        return ModelTurnResponse(assistant_message="ack")


def _write_role_assets(root: Path) -> None:
    role_root = root / "roles" / "research"
    (role_root / "memory").mkdir(parents=True)
    (role_root / "ROLE.md").write_text(
        "---\n"
        "role_id: research\n"
        "recommended_models: [gpt-5.4]\n"
        "skills:\n"
        "  - summarize\n"
        "mcps:\n"
        "  - docs\n"
        "---\n"
        "Role metadata only.\n",
        encoding="utf-8",
    )
    (role_root / "USER.md").write_text(
        "Identity: research role.\nUse mounted role capabilities when they apply.\n",
        encoding="utf-8",
    )


def _write_plugins(root: Path) -> None:
    plugins_root = root / "agent_research" / "local-agent" / "plugins"
    skill_root = plugins_root / "summarize"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\n"
        "name: Summarize\n"
        "description: Summarize a topic.\n"
        "---\n"
        "Summarize the current topic.\n",
        encoding="utf-8",
    )
    docs_root = plugins_root / "docs"
    docs_root.mkdir(parents=True)
    (docs_root / "mcp.json").write_text(
        json.dumps(
            {
                "server_id": "docs",
                "label": "Docs Server",
                "transport": "inmemory",
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo text",
                        "input_schema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                        },
                        "result": ["hello from mcp"],
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_role_provided_skill_and_mcp_are_usable_in_a_real_turn(tmp_path: Path) -> None:
    _write_role_assets(tmp_path)
    _write_plugins(tmp_path)
    model = RoleWorkflowModel()
    runtime = create_file_runtime(
        model=model,
        session_root=str(tmp_path / "sessions"),
        openagent_root=str(tmp_path),
        role_id="research",
    )

    events, terminal = runtime.run_turn("help me", "sess-role-tools")

    assert terminal.status is TerminalStatus.COMPLETED
    assert any(event.event_type.value == "tool_result" for event in events)
    assert any(tool.name == "mcp__docs__echo" for tool in runtime.tools.list_tools())
    assert model.last_request is not None
    assert model.last_request.system_prompt is not None
    assert "Identity: research role." in model.last_request.system_prompt


def test_role_memory_roundtrips_through_durable_memory_pipeline(tmp_path: Path) -> None:
    _write_role_assets(tmp_path)
    _write_plugins(tmp_path)
    model = RoleMemoryModel()
    runtime = create_file_runtime(
        model=model,
        session_root=str(tmp_path / "sessions"),
        openagent_root=str(tmp_path),
        role_id="research",
    )

    runtime.run_turn("Remember the launch codename atlas", "sess-role-memory")
    assert runtime.last_memory_consolidation_job_id is not None
    runtime.memory_store.wait_for_job(runtime.last_memory_consolidation_job_id)

    runtime.run_turn("What is the launch codename?", "sess-role-memory")

    assert model.last_request is not None
    assert model.last_request.memory_context
    assert any("atlas" in str(item.get("content")) for item in model.last_request.memory_context)
