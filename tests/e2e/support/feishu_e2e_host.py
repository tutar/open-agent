"""Deterministic Feishu host used by local end-to-end tests."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from openagent.gateway import (
    FeishuAppConfig,
    create_feishu_gateway,
    create_feishu_host,
)
from openagent.harness.providers import load_model_from_env
from openagent.object_model import ToolResult
from openagent.tools import (
    PermissionDecision,
    ToolExecutionContext,
    ToolProgressUpdate,
    ToolStreamItem,
)


@dataclass(slots=True)
class ApprovalTool:
    """Permission-gated tool used to exercise approval continuation."""

    name: str = "admin"
    permission: PermissionDecision = PermissionDecision.ASK
    input_schema: dict[str, str] = field(default_factory=lambda: {"type": "object"})

    def description(self) -> str:
        return "Administrative action requiring approval."

    def call(self, arguments: dict[str, object]) -> ToolResult:
        return ToolResult(
            tool_name=self.name,
            success=True,
            content=[f"admin action completed: {arguments.get('text', 'ok')}"],
        )

    def check_permissions(self, arguments: dict[str, object]) -> str:
        del arguments
        return self.permission.value

    def is_concurrency_safe(self) -> bool:
        return True


@dataclass(slots=True)
class StreamingTool:
    """Streaming tool used to verify progress notifications."""

    name: str = "stream"
    input_schema: dict[str, str] = field(default_factory=lambda: {"type": "object"})

    def description(self) -> str:
        return "Streaming tool used by Feishu E2E tests."

    def call(self, arguments: dict[str, object]) -> ToolResult:
        return ToolResult(
            tool_name=self.name,
            success=True,
            content=[str(arguments.get("text", "ok"))],
        )

    def check_permissions(self, arguments: dict[str, object]) -> str:
        del arguments
        return PermissionDecision.ALLOW.value

    def is_concurrency_safe(self) -> bool:
        return True

    def stream_call(
        self,
        arguments: dict[str, object],
        context: ToolExecutionContext,
    ) -> Iterator[ToolStreamItem]:
        del context
        payload = str(arguments.get("text", "payload"))
        yield ToolStreamItem(
            progress=ToolProgressUpdate(
                tool_name=self.name,
                message="working",
                progress=0.5,
            )
        )
        yield ToolStreamItem(
            result=ToolResult(
                tool_name=self.name,
                success=True,
                content=[payload],
            )
        )


def create_feishu_e2e_host_from_env():
    """Build a real-provider Feishu host for local E2E tests."""

    config = FeishuAppConfig.from_env()
    gateway, _ = create_feishu_gateway(
        config=config,
        model=load_model_from_env(),
        tools=[ApprovalTool(), StreamingTool()],
    )
    return create_feishu_host(gateway, config)


def main() -> None:
    """Start the real-provider Feishu host used by local E2E tests."""

    host = create_feishu_e2e_host_from_env()
    host.run()


if __name__ == "__main__":
    main()
