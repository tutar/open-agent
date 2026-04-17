"""Host-provided client capabilities for MCP."""

from __future__ import annotations

from dataclasses import dataclass, field

from openagent.object_model import JsonObject, RequiresAction
from openagent.tools.mcp.models import (
    McpElicitationRequest,
    McpRoot,
    McpSamplingRequest,
)


@dataclass(slots=True)
class McpRootsProvider:
    roots: list[McpRoot] = field(default_factory=list)
    changed: bool = False

    def list_roots(self) -> list[McpRoot]:
        return list(self.roots)

    def notify_roots_changed(self) -> None:
        self.changed = True


@dataclass(slots=True)
class McpSamplingBridge:
    approval_required: bool = True

    def handle_sampling_request(
        self,
        server_id: str,
        request: McpSamplingRequest,
    ) -> JsonObject:
        if self.approval_required:
            return RequiresAction(
                action_type="mcp_sampling",
                session_id=request.request_id,
                description=f"MCP sampling request from {server_id}",
                request_id=request.request_id,
                input=request.to_dict(),
            ).to_dict()
        return {
            "request_id": request.request_id,
            "message": {"role": "assistant", "content": "sampled"},
        }


@dataclass(slots=True)
class McpElicitationBridge:
    def handle_elicitation_request(
        self,
        server_id: str,
        request: McpElicitationRequest,
    ) -> JsonObject:
        return RequiresAction(
            action_type="mcp_elicitation",
            session_id=request.request_id,
            description=f"MCP elicitation request from {server_id}",
            request_id=request.request_id,
            input=request.to_dict(),
        ).to_dict()
