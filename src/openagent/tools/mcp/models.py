"""Shared MCP models aligned with the SDK object model."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from openagent.object_model import JsonObject, JsonValue, SerializableModel, ToolResult


@dataclass(slots=True)
class McpServerDescriptor(SerializableModel):
    server_id: str
    label: str
    endpoint: str | None = None
    transport: str = "inmemory"
    capabilities: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class McpToolDescriptor(SerializableModel):
    name: str
    description: str
    input_schema: JsonObject = field(default_factory=dict)
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class McpPromptDescriptor(SerializableModel):
    name: str
    description: str
    template: str = ""
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class McpResourceDescriptor(SerializableModel):
    uri: str
    name: str
    description: str
    mime_type: str = "text/plain"
    content: str = ""
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class McpListPage(SerializableModel):
    items: list[JsonObject]
    next_cursor: str | None = None


@dataclass(slots=True)
class McpClientCapabilities(SerializableModel):
    prompts: bool = True
    tools: bool = True
    resources: bool = True
    roots: bool = False
    sampling: bool = False
    elicitation: bool = False
    tasks: bool = False
    experimental: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class McpServerCapabilities(SerializableModel):
    prompts: bool = True
    tools: bool = True
    resources: bool = True
    logging: bool = False
    completions: bool = False
    tasks: bool = False
    subscribe: bool = False
    experimental: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class McpAuthConfig(SerializableModel):
    mode: str = "none"
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class McpAuthMetadata(SerializableModel):
    token_endpoint: str | None = None
    authorization_server: str | None = None
    scopes: list[str] = field(default_factory=list)
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class McpAuthState(SerializableModel):
    access_token: str | None = None
    scopes: list[str] = field(default_factory=list)
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class McpRoot(SerializableModel):
    uri: str
    name: str | None = None
    writable: bool | None = None


@dataclass(slots=True)
class McpSamplingRequest(SerializableModel):
    server_id: str
    request_id: str
    messages: list[JsonObject]
    system_prompt: str | None = None
    tools: list[JsonObject] = field(default_factory=list)
    tool_choice: JsonObject | None = None
    model_preferences: JsonObject = field(default_factory=dict)
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class McpElicitationRequest(SerializableModel):
    server_id: str
    request_id: str
    mode: str
    title: str | None = None
    schema: JsonObject | None = None
    url: str | None = None
    explanation: str | None = None


@dataclass(slots=True)
class McpLogEvent(SerializableModel):
    server_id: str
    level: str
    message: str
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class McpTaskHandle(SerializableModel):
    server_id: str
    task_id: str
    status: str
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class McpResourceNotification(SerializableModel):
    server_id: str
    kind: str
    resource_uri: str | None = None
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class McpSessionHandle(SerializableModel):
    server_id: str
    protocol_version: str
    negotiated_capabilities: JsonObject
    transport: str
    session_id: str | None = None
    auth_state: JsonObject | None = None
    initialized: bool = False


@dataclass(slots=True)
class McpServerConnection:
    descriptor: McpServerDescriptor
    tools: dict[str, tuple[McpToolDescriptor, Callable[[JsonObject], ToolResult]]] = field(
        default_factory=dict
    )
    prompts: dict[str, McpPromptDescriptor] = field(default_factory=dict)
    resources: dict[str, McpResourceDescriptor] = field(default_factory=dict)
    client_capabilities: McpClientCapabilities = field(default_factory=McpClientCapabilities)
    server_capabilities: McpServerCapabilities = field(default_factory=McpServerCapabilities)
    auth: McpAuthConfig = field(default_factory=McpAuthConfig)
    auth_metadata: McpAuthMetadata = field(default_factory=McpAuthMetadata)
    protocol_version: str = "2025-11-25"
    tool_page_size: int = 0
    prompt_page_size: int = 0
    resource_page_size: int = 0
    roots: list[McpRoot] = field(default_factory=list)
    require_sampling_tools_support: bool = True
    extra_tool_scopes: dict[str, list[str]] = field(default_factory=dict)
    resource_subscriptions: set[str] = field(default_factory=set)
    notifications: list[McpResourceNotification] = field(default_factory=list)
    tasks: dict[str, McpTaskHandle] = field(default_factory=dict)
    logs: list[McpLogEvent] = field(default_factory=list)


@dataclass(slots=True)
class McpTransportHandle:
    transport: str
    server_id: str
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class McpTransportResponse:
    result: JsonObject | None = None
    events: list[JsonObject] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    status_code: int = 200


class _SafeFormatMap(dict[str, JsonValue]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render_prompt(template: str, args: JsonObject) -> str:
    return template.format_map(_SafeFormatMap(args))
