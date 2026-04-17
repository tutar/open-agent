"""MCP protocol client and compatibility facade."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar, cast

from openagent.object_model import JsonObject, JsonValue, SerializableModel, ToolResult
from openagent.tools.mcp.auth import McpAuthorizationAdapter
from openagent.tools.mcp.client_capabilities import (
    McpElicitationBridge,
    McpRootsProvider,
    McpSamplingBridge,
)
from openagent.tools.mcp.errors import (
    McpAuthorizationError,
    McpCapabilityError,
    McpProtocolError,
    McpRemoteToolError,
)
from openagent.tools.mcp.models import (
    McpAuthState,
    McpClientCapabilities,
    McpElicitationRequest,
    McpPromptDescriptor,
    McpResourceDescriptor,
    McpRoot,
    McpSamplingRequest,
    McpServerConnection,
    McpServerDescriptor,
    McpSessionHandle,
    McpToolDescriptor,
    McpTransportHandle,
)
from openagent.tools.mcp.transport import (
    HttpTransportConfig,
    InMemoryMcpTransport,
    McpTransport,
    StdioTransportConfig,
)


@dataclass(slots=True)
class _ConnectedSession:
    descriptor: McpServerDescriptor
    handle: McpSessionHandle
    transport_handle: McpTransportHandle
    auth_state: McpAuthState | None = None


@dataclass(slots=True)
class McpProtocolClient:
    transport: McpTransport
    auth: McpAuthorizationAdapter | None = None
    roots_provider: McpRootsProvider | None = None
    sampling_bridge: McpSamplingBridge | None = None
    elicitation_bridge: McpElicitationBridge | None = None
    client_capabilities: McpClientCapabilities = field(default_factory=McpClientCapabilities)
    client_info: JsonObject = field(default_factory=lambda: {"name": "openagent-python-sdk"})
    _sessions: dict[str, _ConnectedSession] = field(default_factory=dict, init=False, repr=False)

    def initialize(
        self,
        server_descriptor: McpServerDescriptor,
        *,
        transport_config: StdioTransportConfig | HttpTransportConfig | None = None,
        client_capabilities: McpClientCapabilities | None = None,
        client_info: JsonObject | None = None,
    ) -> McpSessionHandle:
        capabilities = client_capabilities or self.client_capabilities
        info = client_info or self.client_info
        auth_state: McpAuthState | None = None
        if server_descriptor.transport == "inmemory":
            if not isinstance(self.transport, InMemoryMcpTransport):
                raise McpProtocolError(
                    "inmemory MCP initialization requires InMemoryMcpTransport"
                )
            transport_handle = self.transport.open_http(
                HttpTransportConfig(endpoint="inmemory://local"),
                server_descriptor.server_id,
            )
        elif server_descriptor.transport == "stdio":
            if not isinstance(transport_config, StdioTransportConfig):
                raise McpProtocolError("stdio MCP initialization requires StdioTransportConfig")
            transport_handle = self.transport.open_stdio(
                transport_config,
                server_descriptor.server_id,
            )
        else:
            config = (
                transport_config
                if isinstance(transport_config, HttpTransportConfig)
                else HttpTransportConfig(endpoint=server_descriptor.endpoint or "")
            )
            if not config.endpoint:
                raise McpProtocolError("HTTP MCP initialization requires a server endpoint")
            transport_handle = self.transport.open_http(config, server_descriptor.server_id)
            if self.auth is not None:
                auth_metadata = self.auth.discover_authorization(config.endpoint)
                if auth_metadata.token_endpoint is not None or auth_metadata.scopes:
                    auth_state = self.auth.acquire_token(auth_metadata, scopes=auth_metadata.scopes)
                    transport_handle.metadata["access_token"] = auth_state.access_token or ""
                    transport_handle.metadata["scopes"] = list(auth_state.scopes)
        request = _rpc_message(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": capabilities.to_dict(),
                "clientInfo": info,
            },
            protocol_version="2025-11-25",
        )
        response = self.transport.send(transport_handle, request)
        result = response.result or {}
        protocol_version = str(result.get("protocolVersion", ""))
        if protocol_version != "2025-11-25":
            raise McpProtocolError(
                f"MCP protocol version mismatch: expected 2025-11-25, got {protocol_version}"
            )
        negotiated = result.get("capabilities", {})
        if not isinstance(negotiated, dict):
            raise McpProtocolError("MCP initialize must return JSON object capabilities")
        session = McpSessionHandle(
            server_id=server_descriptor.server_id,
            protocol_version=protocol_version,
            negotiated_capabilities=dict(negotiated),
            transport=server_descriptor.transport,
            session_id=str(uuid.uuid4()),
            auth_state=None,
            initialized=False,
        )
        self._sessions[server_descriptor.server_id] = _ConnectedSession(
            descriptor=server_descriptor,
            handle=session,
            transport_handle=transport_handle,
            auth_state=auth_state if server_descriptor.transport == "http" else None,
        )
        return session

    def send_initialized(self, session_handle: McpSessionHandle) -> None:
        connected = self._connected(session_handle.server_id)
        request = _rpc_message(
            "notifications/initialized",
            {},
            request_id=None,
            protocol_version=session_handle.protocol_version,
        )
        self.transport.send(connected.transport_handle, request)
        connected.handle.initialized = True

    def ping(self, session_handle: McpSessionHandle) -> bool:
        connected = self._connected(session_handle.server_id)
        response = self.transport.send(
            connected.transport_handle,
            _rpc_message("ping", {}, protocol_version=session_handle.protocol_version),
        )
        return bool((response.result or {}).get("pong"))

    def cancel_request(self, session_handle: McpSessionHandle, request_id: str) -> None:
        connected = self._connected(session_handle.server_id)
        self.transport.send(
            connected.transport_handle,
            _rpc_message(
                "notifications/cancelled",
                {"requestId": request_id},
                request_id=None,
                protocol_version=session_handle.protocol_version,
            ),
        )

    def close(self, session_handle: McpSessionHandle) -> None:
        connected = self._connected(session_handle.server_id)
        self.transport.close(connected.transport_handle)
        self._sessions.pop(session_handle.server_id, None)

    def list_tools_page(
        self,
        server_id: str,
        cursor: str | None = None,
    ) -> tuple[list[McpToolDescriptor], str | None]:
        result = self._request(server_id, "tools/list", {"cursor": cursor} if cursor else {})
        tools = _parse_descriptor_list(result.get("tools"), McpToolDescriptor)
        next_cursor = result.get("nextCursor")
        return tools, str(next_cursor) if next_cursor is not None else None

    def list_prompts_page(
        self,
        server_id: str,
        cursor: str | None = None,
    ) -> tuple[list[McpPromptDescriptor], str | None]:
        result = self._request(server_id, "prompts/list", {"cursor": cursor} if cursor else {})
        prompts = _parse_descriptor_list(result.get("prompts"), McpPromptDescriptor)
        next_cursor = result.get("nextCursor")
        return prompts, str(next_cursor) if next_cursor is not None else None

    def list_resources_page(
        self,
        server_id: str,
        cursor: str | None = None,
    ) -> tuple[list[McpResourceDescriptor], str | None]:
        result = self._request(server_id, "resources/list", {"cursor": cursor} if cursor else {})
        resources = _parse_descriptor_list(result.get("resources"), McpResourceDescriptor)
        next_cursor = result.get("nextCursor")
        return resources, str(next_cursor) if next_cursor is not None else None

    def list_tools(self, server_id: str) -> list[McpToolDescriptor]:
        return self._collect_all(server_id, self.list_tools_page)

    def list_prompts(self, server_id: str) -> list[McpPromptDescriptor]:
        return self._collect_all(server_id, self.list_prompts_page)

    def list_resources(self, server_id: str) -> list[McpResourceDescriptor]:
        return self._collect_all(server_id, self.list_resources_page)

    def call_tool(self, server_id: str, tool_name: str, input: JsonObject) -> ToolResult:
        result = self._request(server_id, "tools/call", {"name": tool_name, "arguments": input})
        if result.get("error") is not None:
            raise McpRemoteToolError(str(result["error"]))
        return ToolResult.from_dict(result)

    def get_prompt(self, server_id: str, prompt_name: str, args: JsonObject) -> str:
        result = self._request(server_id, "prompts/get", {"name": prompt_name, "arguments": args})
        messages = result.get("messages", [])
        if isinstance(messages, list) and messages and isinstance(messages[0], dict):
            content = messages[0].get("content")
            return str(content) if content is not None else ""
        raise McpProtocolError("MCP prompts/get must return messages")

    def read_resource(self, server_id: str, resource_uri: str) -> McpResourceDescriptor:
        result = self._request(server_id, "resources/read", {"uri": resource_uri})
        resource = result.get("resource")
        if isinstance(resource, dict):
            return McpResourceDescriptor.from_dict(resource)
        raise McpProtocolError("MCP resources/read must return resource")

    def subscribe_resource(self, server_id: str, resource_uri: str) -> None:
        self._request(server_id, "resources/subscribe", {"uri": resource_uri})

    def poll_notifications(self, server_id: str) -> list[JsonObject]:
        connected = self._connected(server_id)
        return self.transport.receive(connected.transport_handle)

    def list_roots(self, server_id: str) -> list[McpRoot]:
        self._require_client_capability("roots")
        result = self._request(server_id, "roots/list", {})
        return _parse_descriptor_list(result.get("roots"), McpRoot)

    def notify_roots_changed(self, server_id: str) -> None:
        self._require_client_capability("roots")
        if self.roots_provider is not None:
            self.roots_provider.notify_roots_changed()
        self._request(server_id, "notifications/roots/list_changed", {})

    def handle_sampling_request(self, server_id: str, request: McpSamplingRequest) -> JsonObject:
        self._require_client_capability("sampling")
        if request.tools and not bool(self.client_capabilities.experimental.get("sampling_tools")):
            raise McpCapabilityError("Sampling tools requested without declared support")
        if self.sampling_bridge is not None:
            return self.sampling_bridge.handle_sampling_request(server_id, request)
        result = self._request(server_id, "sampling/createMessage", {"request": request.to_dict()})
        return result

    def handle_elicitation_request(
        self,
        server_id: str,
        request: McpElicitationRequest,
    ) -> JsonObject:
        self._require_client_capability("elicitation")
        if self.elicitation_bridge is not None:
            return self.elicitation_bridge.handle_elicitation_request(server_id, request)
        return self._request(server_id, "elicitation/request", {"request": request.to_dict()})

    def _collect_all(
        self,
        server_id: str,
        pager: Callable[[str, str | None], tuple[list[TDescriptor], str | None]],
    ) -> list[TDescriptor]:
        items: list[TDescriptor] = []
        cursor: str | None = None
        while True:
            page_items, cursor = pager(server_id, cursor)
            items.extend(page_items)
            if cursor is None:
                return items

    def _request(self, server_id: str, method: str, params: JsonObject) -> JsonObject:
        connected = self._connected(server_id)
        response = self.transport.send(
            connected.transport_handle,
            _rpc_message(
                method,
                params,
                protocol_version=connected.handle.protocol_version,
            ),
            accept_sse=method.startswith("tools/"),
        )
        if response.status_code == 401:
            if self.auth is None:
                raise McpAuthorizationError("MCP server requires authorization")
            challenge = response.headers.get("www-authenticate", "")
            current_state = connected.auth_state or McpAuthState()
            upgraded = self.auth.handle_www_authenticate(challenge, current_state)
            connected.auth_state = upgraded
            connected.handle.auth_state = upgraded.to_dict()
            scopes = upgraded.scopes
            if hasattr(connected.transport_handle, "metadata"):
                connected.transport_handle.metadata["access_token"] = upgraded.access_token or ""
                connected.transport_handle.metadata["scopes"] = cast(JsonValue, scopes)
            retry = self.transport.send(
                connected.transport_handle,
                _rpc_message(
                    method,
                    params,
                    protocol_version=connected.handle.protocol_version,
                ),
                accept_sse=method.startswith("tools/"),
            )
            return retry.result or {}
        return response.result or {}

    def _connected(self, server_id: str) -> _ConnectedSession:
        if (
            server_id not in self._sessions
            and isinstance(self.transport, InMemoryMcpTransport)
            and self.transport.has_server(server_id)
        ):
            descriptor = self.transport.describe_server(server_id)
            session = self.initialize(descriptor, client_capabilities=self.client_capabilities)
            self.send_initialized(session)
        if server_id not in self._sessions:
            raise KeyError(f"MCP server is not initialized: {server_id}")
        return self._sessions[server_id]

    def _require_client_capability(self, name: str) -> None:
        if not bool(getattr(self.client_capabilities, name)):
            raise McpCapabilityError(f"MCP client capability '{name}' is not enabled")


TDescriptor = TypeVar("TDescriptor", bound=SerializableModel)


def _parse_descriptor_list(
    value: object,
    descriptor_type: type[TDescriptor],
) -> list[TDescriptor]:
    if not isinstance(value, list):
        return []
    parsed: list[TDescriptor] = []
    for item in value:
        if isinstance(item, dict):
            parsed.append(descriptor_type.from_dict(cast(JsonObject, item)))
    return parsed


def _rpc_message(
    method: str,
    params: JsonObject,
    *,
    request_id: str | None = None,
    protocol_version: str,
) -> JsonObject:
    payload: JsonObject = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "_protocol_version": protocol_version,
    }
    if request_id is None and not method.startswith("notifications/"):
        payload["id"] = str(uuid.uuid4())
    elif request_id is not None:
        payload["id"] = request_id
    return payload


class TransportBackedMcpClient(McpProtocolClient):
    """High-level MCP client facade backed by a transport implementation."""


class InMemoryMcpClient(TransportBackedMcpClient):
    """Compatibility facade over the deterministic in-memory transport."""

    def __init__(self, transport: InMemoryMcpTransport | None = None) -> None:
        self._transport_impl = transport or InMemoryMcpTransport()
        super().__init__(transport=self._transport_impl)

    def connect(self, server: McpServerConnection) -> McpServerDescriptor:
        self._transport_impl.connect(server)
        capabilities = server.client_capabilities
        if capabilities.sampling:
            capabilities.experimental.setdefault("sampling_tools", True)
        session = self.initialize(server.descriptor, client_capabilities=capabilities)
        if capabilities.sampling:
            connected = self._connected(server.descriptor.server_id)
            if hasattr(connected.transport_handle, "metadata"):
                connected.transport_handle.metadata["client_sampling_tools"] = True
        self.send_initialized(session)
        return server.descriptor

    def disconnect(self, server_id: str) -> None:
        if server_id in self._sessions:
            self.close(self._sessions[server_id].handle)
        self._transport_impl.disconnect(server_id)
