"""Deterministic Feishu host used by local end-to-end tests."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from openagent.gateway import (
    FeishuAppConfig,
    FeishuChannelAdapter,
    FeishuHostRunLock,
    FeishuLongConnectionHost,
    OfficialFeishuBotClient,
    create_feishu_gateway,
)
from openagent.harness import ModelTurnRequest, ModelTurnResponse
from openagent.object_model import ToolResult
from openagent.tools import (
    PermissionDecision,
    ToolCall,
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


@dataclass(slots=True)
class DeterministicFeishuModel:
    """Scriptable model for stable Feishu E2E assertions."""

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        latest = request.messages[-1]
        role = str(latest.get("role", "user"))
        content = str(latest.get("content", ""))

        if role == "tool":
            if "admin action completed" in content:
                return ModelTurnResponse(assistant_message="e2e approval completed")
            if "payload" in content:
                return ModelTurnResponse(assistant_message="e2e stream completed")
            return ModelTurnResponse(assistant_message="e2e tool completed")

        if content == "admin rotate":
            return ModelTurnResponse(
                tool_calls=[ToolCall(tool_name="admin", arguments={"text": "rotate"})]
            )
        if content == "run stream":
            return ModelTurnResponse(
                tool_calls=[ToolCall(tool_name="stream", arguments={"text": "payload"})]
            )
        if "group" in content:
            return ModelTurnResponse(assistant_message=f"e2e group reply: {content}")
        return ModelTurnResponse(assistant_message=f"e2e reply: {content}")


def create_feishu_e2e_host_from_env() -> FeishuLongConnectionHost:
    """Build a deterministic Feishu host for local E2E tests."""

    config = FeishuAppConfig.from_env()
    gateway, _ = create_feishu_gateway(
        config=config,
        model=DeterministicFeishuModel(),
        tools=[ApprovalTool(), StreamingTool()],
    )
    client = OfficialFeishuBotClient(config.app_id, config.app_secret)
    adapter = gateway.get_channel_adapter("feishu")
    assert isinstance(adapter, FeishuChannelAdapter)
    adapter.client = client
    return FeishuLongConnectionHost(
        gateway=gateway,
        adapter=adapter,
        client=client,
        run_lock=FeishuHostRunLock(config.app_id, config.lock_root),
    )


def main() -> None:
    """Start the deterministic Feishu host used by local E2E tests."""

    host = create_feishu_e2e_host_from_env()
    host.run()


if __name__ == "__main__":
    main()
