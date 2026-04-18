"""Host-local demo model and tools used as a fallback runtime."""

from __future__ import annotations

from dataclasses import dataclass, field

from openagent.harness import ModelProviderExchange, ModelTurnRequest, ModelTurnResponse
from openagent.object_model import ToolResult
from openagent.tools import PermissionDecision, ToolCall


@dataclass(slots=True)
class EchoTool:
    name: str = "echo"
    input_schema: dict[str, str] = field(default_factory=lambda: {"type": "object"})
    aliases: list[str] = field(default_factory=list)

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
    aliases: list[str] = field(default_factory=list)

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
    provider_family = "demo"

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        return self.generate_with_exchange(request).response

    def generate_with_exchange(self, request: ModelTurnRequest) -> ModelProviderExchange:
        latest = request.messages[-1]
        role = str(latest.get("role", "user"))
        content = str(latest.get("content", ""))

        if role == "tool":
            response = ModelTurnResponse(assistant_message=f"Tool completed: {content}")
            return ModelProviderExchange(response=response)

        if content.startswith("tool "):
            response = ModelTurnResponse(
                tool_calls=[ToolCall(tool_name="echo", arguments={"text": content[5:]})]
            )
            return ModelProviderExchange(response=response)

        if content.startswith("admin "):
            response = ModelTurnResponse(
                tool_calls=[ToolCall(tool_name="admin", arguments={"text": content[6:]})]
            )
            return ModelProviderExchange(response=response)

        response = ModelTurnResponse(assistant_message=f"Echo: {content}")
        return ModelProviderExchange(response=response)
