"""Role-oriented MCP manifest loading and tool adaptation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openagent.object_model import JsonObject, JsonValue, ToolResult
from openagent.tools.mcp.models import (
    McpAuthConfig,
    McpClientCapabilities,
    McpServerCapabilities,
    McpServerConnection,
    McpServerDescriptor,
    McpToolDescriptor,
)
from openagent.tools.mcp.protocol import McpProtocolClient, TransportBackedMcpClient
from openagent.tools.mcp.transport import (
    HttpTransportConfig,
    InMemoryMcpTransport,
    StdioMcpTransport,
    StdioTransportConfig,
    StreamableHttpMcpTransport,
)


@dataclass(slots=True)
class McpPluginManifest:
    server_id: str
    label: str
    transport: str = "http"
    endpoint: str | None = None
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    auth: JsonObject = field(default_factory=dict)
    client_capabilities: JsonObject = field(default_factory=dict)
    server_capabilities: JsonObject = field(default_factory=dict)
    fixture_tools: list[JsonObject] = field(default_factory=list)
    manifest_path: str = ""


@dataclass(slots=True)
class MountedMcpServer:
    server_id: str
    manifest_path: str
    tool_names: list[str] = field(default_factory=list)


class McpRemoteTool:
    def __init__(
        self,
        *,
        server_id: str,
        remote_tool_name: str,
        description_text: str,
        input_schema: JsonObject,
        client: McpProtocolClient,
    ) -> None:
        self.name = f"mcp__{server_id}__{remote_tool_name}"
        self.aliases: list[str] = []
        self.input_schema = dict(input_schema)
        self.source = "mcp_adapter"
        self.visibility = "both"
        self._server_id = server_id
        self._remote_tool_name = remote_tool_name
        self._description_text = description_text
        self._client = client
        self.provenance = {"origin": "mcp", "server_id": server_id}

    def description(self, *args: Any, **kwargs: Any) -> str:
        del args, kwargs
        return self._description_text

    def call(self, arguments: dict[str, object], *args: Any, **kwargs: Any) -> ToolResult:
        del args, kwargs
        return self._client.call_tool(
            self._server_id,
            self._remote_tool_name,
            {str(key): cast_json_value(value) for key, value in arguments.items()},
        )

    def check_permissions(self, *args: Any, **kwargs: Any) -> str:
        del args, kwargs
        return "allow"

    def is_concurrency_safe(self, *args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        return False


def load_mcp_plugin_manifests(plugins_root: str | Path) -> dict[str, McpPluginManifest]:
    manifests: dict[str, McpPluginManifest] = {}
    root = Path(plugins_root)
    if not root.exists():
        return manifests
    candidates = sorted(root.rglob("mcp.json")) + sorted(root.rglob("*.mcp.json"))
    for path in candidates:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        server_id = data.get("server_id") or data.get("id") or path.parent.name
        label = data.get("label") or str(server_id)
        if not isinstance(server_id, str) or not server_id.strip():
            continue
        manifests[server_id.strip()] = McpPluginManifest(
            server_id=server_id.strip(),
            label=str(label),
            transport=str(data.get("transport", "http")),
            endpoint=str(data["endpoint"]) if isinstance(data.get("endpoint"), str) else None,
            command=str(data["command"]) if isinstance(data.get("command"), str) else None,
            args=(
                [str(item) for item in data.get("args", [])]
                if isinstance(data.get("args"), list)
                else []
            ),
            env={
                str(key): str(value)
                for key, value in data.get("env", {}).items()
            }
            if isinstance(data.get("env"), dict)
            else {},
            cwd=str(data["cwd"]) if isinstance(data.get("cwd"), str) else None,
            auth=dict(data.get("auth", {})) if isinstance(data.get("auth"), dict) else {},
            client_capabilities=(
                dict(data.get("client_capabilities", {}))
                if isinstance(data.get("client_capabilities"), dict)
                else {}
            ),
            server_capabilities=(
                dict(data.get("server_capabilities", {}))
                if isinstance(data.get("server_capabilities"), dict)
                else {}
            ),
            fixture_tools=(
                [dict(item) for item in data.get("tools", []) if isinstance(item, dict)]
                if isinstance(data.get("tools"), list)
                else []
            ),
            manifest_path=str(path),
        )
    return manifests


def mount_role_mcp_tools(
    *,
    server_ids: list[str],
    plugins_root: str | Path,
) -> tuple[list[McpRemoteTool], list[MountedMcpServer]]:
    manifests = load_mcp_plugin_manifests(plugins_root)
    mounted_tools: list[McpRemoteTool] = []
    mounted_servers: list[MountedMcpServer] = []
    for server_id in server_ids:
        manifest = manifests.get(server_id)
        if manifest is None:
            raise FileNotFoundError(f"MCP manifest not found for role-mounted server: {server_id}")
        client = _build_client(manifest)
        descriptors = client.list_tools(manifest.server_id)
        tool_names: list[str] = []
        for descriptor in descriptors:
            mounted_tools.append(
                McpRemoteTool(
                    server_id=manifest.server_id,
                    remote_tool_name=descriptor.name,
                    description_text=f"[mcp:{manifest.server_id}] {descriptor.description}",
                    input_schema=descriptor.input_schema,
                    client=client,
                )
            )
            tool_names.append(descriptor.name)
        mounted_servers.append(
            MountedMcpServer(
                server_id=manifest.server_id,
                manifest_path=manifest.manifest_path,
                tool_names=tool_names,
            )
        )
    return mounted_tools, mounted_servers


def _build_client(manifest: McpPluginManifest) -> McpProtocolClient:
    transport = manifest.transport.strip().lower()
    if transport == "inmemory":
        return _build_inmemory_client(manifest)
    if transport == "stdio":
        if not manifest.command:
            raise RuntimeError(f"MCP stdio manifest is missing command: {manifest.manifest_path}")
        descriptor = McpServerDescriptor(
            server_id=manifest.server_id,
            label=manifest.label,
            transport="stdio",
        )
        client = TransportBackedMcpClient(transport=StdioMcpTransport())
        session = client.initialize(
            descriptor,
            transport_config=StdioTransportConfig(
                command=manifest.command,
                args=list(manifest.args),
                cwd=manifest.cwd,
                env=dict(manifest.env),
            ),
            client_capabilities=_client_capabilities(manifest.client_capabilities),
        )
        client.send_initialized(session)
        return client
    if transport == "http":
        descriptor = McpServerDescriptor(
            server_id=manifest.server_id,
            label=manifest.label,
            endpoint=manifest.endpoint,
            transport="http",
        )
        client = TransportBackedMcpClient(transport=StreamableHttpMcpTransport())
        if not manifest.endpoint:
            raise RuntimeError(f"MCP http manifest is missing endpoint: {manifest.manifest_path}")
        session = client.initialize(
            descriptor,
            transport_config=HttpTransportConfig(endpoint=manifest.endpoint),
            client_capabilities=_client_capabilities(manifest.client_capabilities),
        )
        client.send_initialized(session)
        return client
    raise RuntimeError(f"Unsupported MCP transport: {manifest.transport}")


def _build_inmemory_client(manifest: McpPluginManifest) -> McpProtocolClient:
    transport = InMemoryMcpTransport()
    tools: dict[str, tuple[McpToolDescriptor, object]] = {}
    for tool in manifest.fixture_tools:
        name = tool.get("name")
        description = tool.get("description")
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(description, str) or not description.strip():
            description = name
        result = tool.get("result", [])

        def _handler(
            arguments: JsonObject,
            *,
            tool_name: str = name.strip(),
            tool_result: object = result,
        ) -> ToolResult:
            del arguments
            payload = tool_result if isinstance(tool_result, list) else [tool_result]
            return ToolResult(
                tool_name=tool_name,
                success=True,
                content=[cast_json_value(item) for item in payload],
            )

        tools[name.strip()] = (
            McpToolDescriptor(
                name=name.strip(),
                description=str(description),
                input_schema=dict(tool.get("input_schema", {}))
                if isinstance(tool.get("input_schema"), dict)
                else {},
            ),
            _handler,
        )
    connection = McpServerConnection(
        descriptor=McpServerDescriptor(
            server_id=manifest.server_id,
            label=manifest.label,
            transport="inmemory",
        ),
        tools=tools,
        client_capabilities=McpClientCapabilities(),
        server_capabilities=McpServerCapabilities(),
        auth=McpAuthConfig(),
    )
    transport.connect(connection)
    client = TransportBackedMcpClient(transport=transport)
    session = client.initialize(
        McpServerDescriptor(
            server_id=manifest.server_id,
            label=manifest.label,
            transport="inmemory",
        )
    )
    client.send_initialized(session)
    return client


def _client_capabilities(data: JsonObject) -> McpClientCapabilities:
    return McpClientCapabilities(
        prompts=bool(data.get("prompts", True)),
        tools=bool(data.get("tools", True)),
        resources=bool(data.get("resources", True)),
        roots=bool(data.get("roots", False)),
        sampling=bool(data.get("sampling", False)),
        elicitation=bool(data.get("elicitation", False)),
        tasks=bool(data.get("tasks", False)),
        experimental=dict(data.get("experimental", {}))
        if isinstance(data.get("experimental"), dict)
        else {},
    )


def cast_json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list):
        return [cast_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): cast_json_value(item) for key, item in value.items()}
    return str(value)
