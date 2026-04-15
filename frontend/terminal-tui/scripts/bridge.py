from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


def configure_sys_path() -> None:
    sdk_root = Path(__file__).resolve().parents[2]
    sdk_src = sdk_root / "src"
    sdk_src_text = str(sdk_src)
    if sdk_src_text not in sys.path:
        sys.path.insert(0, sdk_src_text)


configure_sys_path()

from openagent.gateway import (  # noqa: E402
    ChannelIdentity,
    Gateway,
    InboundEnvelope,
    InProcessSessionAdapter,
)
from openagent.harness import (  # noqa: E402
    ModelProviderAdapter,
    ModelTurnRequest,
    ModelTurnResponse,
)
from openagent.harness.providers import (  # noqa: E402
    ProviderConfigurationError,
    load_model_from_env,
)
from openagent.object_model import RuntimeEvent, ToolResult  # noqa: E402
from openagent.profiles import TuiProfile  # noqa: E402
from openagent.tools import PermissionDecision, ToolCall  # noqa: E402


@dataclass(slots=True)
class EchoTool:
    name: str = "echo"
    input_schema: dict[str, str] = field(default_factory=lambda: {"type": "object"})

    def description(self) -> str:
        return "Echo the provided text."

    def call(self, arguments: dict[str, object]) -> ToolResult:
        return ToolResult(
            tool_name=self.name,
            success=True,
            content=[str(arguments.get("text", ""))],
        )

    def check_permissions(self, arguments: dict[str, object]) -> str:
        del arguments
        return PermissionDecision.ALLOW.value

    def is_concurrency_safe(self) -> bool:
        return True


@dataclass(slots=True)
class AdminTool:
    name: str = "admin"
    input_schema: dict[str, str] = field(default_factory=lambda: {"type": "object"})

    def description(self) -> str:
        return "A permission-gated administrative action."

    def call(self, arguments: dict[str, object]) -> ToolResult:
        action = str(arguments.get("text", ""))
        return ToolResult(
            tool_name=self.name,
            success=True,
            content=[f"admin action completed: {action}"],
        )

    def check_permissions(self, arguments: dict[str, object]) -> str:
        del arguments
        return PermissionDecision.ASK.value

    def is_concurrency_safe(self) -> bool:
        return True


@dataclass(slots=True)
class DemoModel:
    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        latest = request.messages[-1]
        role = str(latest.get("role", "user"))
        content = str(latest.get("content", ""))

        if role == "tool":
            return ModelTurnResponse(assistant_message=f"Tool completed: {content}")

        if content.startswith("tool "):
            return ModelTurnResponse(
                tool_calls=[ToolCall(tool_name="echo", arguments={"text": content[5:]})]
            )

        if content.startswith("admin "):
            return ModelTurnResponse(
                tool_calls=[ToolCall(tool_name="admin", arguments={"text": content[6:]})]
            )

        return ModelTurnResponse(assistant_message=f"Echo: {content}")


def emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload), flush=True)


def emit_error(message: str) -> None:
    emit({"type": "error", "message": message})


def emit_event_payload(event: RuntimeEvent) -> None:
    emit(
        {
            "type": "event",
            "event_type": event.event_type.value,
            "payload": event.payload,
            "session_id": event.session_id,
        }
    )


def session_identity(session_name: str) -> tuple[str, str]:
    conversation_id = f"terminal-{session_name}"
    session_id = f"{conversation_id}-session"
    return conversation_id, session_id


def main() -> None:
    profile = TuiProfile()
    model = _load_bridge_model()
    runtime = profile.create_runtime(
        model=model,
        tools=[EchoTool(), AdminTool()],
    )

    session_adapter = InProcessSessionAdapter(runtime)
    gateway = Gateway(session_adapter)
    sessions: dict[str, ChannelIdentity] = {}
    current_session_name = "main"

    def bind_session(session_name: str) -> tuple[str, str]:
        conversation_id, session_id = session_identity(session_name)
        channel = ChannelIdentity(
            channel_type="terminal",
            user_id="local-user",
            conversation_id=conversation_id,
        )
        gateway.bind_session(channel, session_id)
        sessions[session_name] = channel
        return conversation_id, session_id

    _, current_session_id = bind_session(current_session_name)
    emit(
        {
            "type": "status",
            "message": "ready",
            "session_name": current_session_name,
            "session_id": current_session_id,
        }
    )

    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            emit_error("invalid_json")
            continue

        kind = message.get("kind")

        if kind == "bind":
            session_name = str(message.get("session_name", "")).strip()
            if not session_name:
                emit_error("missing_session_name")
                continue

            _, current_session_id = bind_session(session_name)
            current_session_name = session_name
            emit(
                {
                    "type": "status",
                    "message": "bound",
                    "session_name": current_session_name,
                    "session_id": current_session_id,
                }
            )
            for event in session_adapter.observe(current_session_id):
                emit_event_payload(event)
            continue

        if kind == "list_sessions":
            emit(
                {
                    "type": "sessions",
                    "current_session_name": current_session_name,
                    "sessions": sorted(sessions),
                }
            )
            continue

        if kind == "message":
            channel = sessions[current_session_name]
            egress = gateway.process_user_message(
                InboundEnvelope(
                    channel_identity=channel.to_dict(),
                    input_kind="user_message",
                    payload={"content": str(message.get("content", ""))},
                )
            )
            for item in egress:
                emit(
                    {
                        "type": "event",
                        "event_type": item.event["event_type"],
                        "payload": item.event["payload"],
                        "session_id": item.session_id,
                    }
                )
            continue

        if kind == "control":
            subtype = str(message.get("subtype", ""))
            if subtype not in {"permission_response", "interrupt"}:
                emit_error("unknown_control_subtype")
                continue
            channel = sessions[current_session_name]
            egress = gateway.process_control_message(
                channel,
                {
                    "subtype": subtype,
                    "approved": bool(message.get("approved", False)),
                },
            )
            for item in egress:
                emit(
                    {
                        "type": "event",
                        "event_type": item.event["event_type"],
                        "payload": item.event["payload"],
                        "session_id": item.session_id,
                    }
                )
            continue

        emit_error(f"unknown_message_kind:{kind}")


def _load_bridge_model() -> ModelProviderAdapter:
    if os.getenv("OPENAGENT_MODEL") is None:
        return DemoModel()
    try:
        return load_model_from_env()
    except ProviderConfigurationError as exc:
        emit_error(f"provider_configuration_error:{exc}")
        return DemoModel()


if __name__ == "__main__":
    main()
