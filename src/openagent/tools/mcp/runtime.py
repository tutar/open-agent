"""MCP runtime adaptation helpers."""

from __future__ import annotations

from openagent.object_model import JsonObject, RuntimeEvent, RuntimeEventType
from openagent.tools.commands import Command, CommandKind, CommandVisibility
from openagent.tools.mcp.models import (
    McpLogEvent,
    McpPromptDescriptor,
    McpResourceDescriptor,
    McpTaskHandle,
    McpToolDescriptor,
)


class McpToolAdapter:
    def adapt_mcp_tool(self, server_id: str, remote_tool: McpToolDescriptor) -> McpToolDescriptor:
        remote_tool.description = f"[mcp:{server_id}] {remote_tool.description}"
        remote_tool.metadata["server_id"] = server_id
        remote_tool.metadata["capability_kind"] = "tool"
        return remote_tool


class McpPromptAdapter:
    def adapt_mcp_prompt(self, server_id: str, remote_prompt: McpPromptDescriptor) -> Command:
        return Command(
            id=f"mcp__{server_id}__{remote_prompt.name}",
            name=remote_prompt.name,
            kind=CommandKind.PROMPT,
            description=remote_prompt.description,
            visibility=CommandVisibility.BOTH,
            source="mcp_prompt",
            metadata={
                "server_id": server_id,
                "prompt_name": remote_prompt.name,
                "origin": "mcp_prompt",
                "capability_kind": "prompt",
            },
        )


class McpResourceAdapter:
    def adapt_mcp_resource(
        self,
        server_id: str,
        remote_resource: McpResourceDescriptor,
    ) -> JsonObject:
        return {
            "server_id": server_id,
            "uri": remote_resource.uri,
            "name": remote_resource.name,
            "description": remote_resource.description,
            "mime_type": remote_resource.mime_type,
            "content": remote_resource.content,
            "capability_kind": "resource",
            **remote_resource.metadata,
        }

    def project_resource_notification(
        self,
        server_id: str,
        notification: JsonObject,
    ) -> RuntimeEvent:
        kind = str(notification.get("kind", "resources/list_changed"))
        payload = {"server_id": server_id, **notification}
        return RuntimeEvent(
            event_type=RuntimeEventType.TOOL_PROGRESS,
            event_id=f"{server_id}:{kind}",
            timestamp="1970-01-01T00:00:00Z",
            session_id=server_id,
            payload=payload,
        )

    def project_log_event(self, server_id: str, remote_log_event: McpLogEvent) -> RuntimeEvent:
        return RuntimeEvent(
            event_type=RuntimeEventType.TOOL_PROGRESS,
            event_id=f"{server_id}:log:{remote_log_event.level}",
            timestamp="1970-01-01T00:00:00Z",
            session_id=server_id,
            payload={"server_id": server_id, **remote_log_event.to_dict()},
        )

    def project_task_handle(
        self,
        server_id: str,
        remote_task_handle: McpTaskHandle,
    ) -> RuntimeEvent:
        return RuntimeEvent(
            event_type=RuntimeEventType.TOOL_PROGRESS,
            event_id=f"{server_id}:task:{remote_task_handle.task_id}",
            timestamp="1970-01-01T00:00:00Z",
            session_id=server_id,
            payload={"server_id": server_id, **remote_task_handle.to_dict()},
        )
