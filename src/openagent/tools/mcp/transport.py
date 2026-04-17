"""Transport adapters for MCP clients."""

from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol, cast
from urllib import request
from urllib.error import HTTPError, URLError

from openagent.object_model import JsonObject, JsonValue
from openagent.tools.mcp.errors import McpProtocolError, McpTransportError
from openagent.tools.mcp.models import (
    McpServerConnection,
    McpServerDescriptor,
    McpTransportHandle,
    McpTransportResponse,
    render_prompt,
)


@dataclass(slots=True)
class StdioTransportConfig:
    command: list[str]
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class HttpTransportConfig:
    endpoint: str
    headers: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 5.0


class McpTransport(Protocol):
    def open_stdio(self, config: StdioTransportConfig, server_id: str) -> McpTransportHandle:
        """Open a stdio MCP transport."""

    def open_http(self, config: HttpTransportConfig, server_id: str) -> McpTransportHandle:
        """Open a streamable HTTP MCP transport."""

    def send(
        self,
        session_handle: McpTransportHandle,
        message: JsonObject,
        *,
        accept_sse: bool = False,
    ) -> McpTransportResponse:
        """Send a JSON-RPC message and return the decoded response."""

    def receive(self, session_handle: McpTransportHandle) -> list[JsonObject]:
        """Receive pending notifications for the session."""

    def resume(
        self,
        session_handle: McpTransportHandle,
        stream_ref: str | None = None,
    ) -> McpTransportResponse:
        """Resume a transport stream if supported."""

    def close(self, session_handle: McpTransportHandle) -> None:
        """Close the underlying transport."""


class InMemoryMcpTransport:
    """Deterministic MCP transport for tests and offline development."""

    def __init__(self) -> None:
        self._servers: dict[str, McpServerConnection] = {}
        self._handles: dict[str, McpTransportHandle] = {}

    def connect(self, server: McpServerConnection) -> McpTransportHandle:
        self._servers[server.descriptor.server_id] = server
        handle = McpTransportHandle(transport="inmemory", server_id=server.descriptor.server_id)
        self._handles[server.descriptor.server_id] = handle
        return handle

    def open_stdio(self, config: StdioTransportConfig, server_id: str) -> McpTransportHandle:
        del config
        return self._handles[server_id]

    def open_http(self, config: HttpTransportConfig, server_id: str) -> McpTransportHandle:
        del config
        return self._handles[server_id]

    def disconnect(self, server_id: str) -> None:
        self._servers.pop(server_id, None)
        self._handles.pop(server_id, None)

    def has_server(self, server_id: str) -> bool:
        return server_id in self._servers

    def describe_server(self, server_id: str) -> McpServerDescriptor:
        return self._servers[server_id].descriptor

    def send(
        self,
        session_handle: McpTransportHandle,
        message: JsonObject,
        *,
        accept_sse: bool = False,
    ) -> McpTransportResponse:
        del accept_sse
        server = self._servers[session_handle.server_id]
        method = str(message["method"])
        params = message.get("params", {})
        if not isinstance(params, dict):
            params = {}
        if method == "initialize":
            result: JsonObject = {
                "protocolVersion": server.protocol_version,
                "capabilities": server.server_capabilities.to_dict(),
                "serverInfo": server.descriptor.to_dict(),
            }
            return McpTransportResponse(result=result)
        if method == "notifications/initialized":
            return McpTransportResponse(result={})
        if method == "ping":
            return McpTransportResponse(result={"pong": True})
        if method == "tools/list":
            return McpTransportResponse(result=self._paginate_tools(server, params))
        if method == "tools/call":
            tool_name = str(params["name"])
            required_scopes = server.extra_tool_scopes.get(tool_name, [])
            granted_scopes = []
            session_scopes = session_handle.metadata.get("scopes")
            if isinstance(session_scopes, list):
                granted_scopes = [str(scope) for scope in session_scopes]
            missing_scopes = [scope for scope in required_scopes if scope not in granted_scopes]
            if missing_scopes:
                return McpTransportResponse(
                    status_code=401,
                    headers={"www-authenticate": f'Bearer scope="{" ".join(missing_scopes)}"'},
                    result={"error": "missing_scope"},
                )
            _, handler = server.tools[tool_name]
            arguments = params.get("arguments", {})
            if not isinstance(arguments, dict):
                raise McpProtocolError("tools/call arguments must be a JSON object")
            result = handler(arguments).to_dict()
            return McpTransportResponse(result=result)
        if method == "prompts/list":
            return McpTransportResponse(result=self._paginate_prompts(server, params))
        if method == "prompts/get":
            prompt_name = str(params["name"])
            args = params.get("arguments", {})
            if not isinstance(args, dict):
                args = {}
            prompt = server.prompts[prompt_name]
            return McpTransportResponse(
                result={
                    "name": prompt_name,
                    "description": prompt.description,
                    "messages": [
                        {
                            "role": "user",
                            "content": render_prompt(prompt.template, args),
                        }
                    ],
                }
            )
        if method == "resources/list":
            return McpTransportResponse(result=self._paginate_resources(server, params))
        if method == "resources/read":
            uri = str(params["uri"])
            return McpTransportResponse(result={"resource": server.resources[uri].to_dict()})
        if method == "resources/subscribe":
            uri = str(params["uri"])
            server.resource_subscriptions.add(uri)
            return McpTransportResponse(result={"subscribed": True, "uri": uri})
        if method == "roots/list":
            return McpTransportResponse(result={"roots": [root.to_dict() for root in server.roots]})
        if method == "sampling/createMessage":
            request_payload = params.get("request", {})
            if not isinstance(request_payload, dict):
                request_payload = {}
            tools = request_payload.get("tools")
            if tools and server.require_sampling_tools_support and not bool(
                session_handle.metadata.get("client_sampling_tools")
            ):
                raise McpProtocolError("Sampling request includes tools without negotiated support")
            return McpTransportResponse(
                result={
                    "request_id": request_payload.get("request_id"),
                    "message": {"role": "assistant", "content": "sampled"},
                }
            )
        if method == "elicitation/request":
            request_payload = params.get("request", {})
            if not isinstance(request_payload, dict):
                request_payload = {}
            mode = str(request_payload.get("mode", "form"))
            return McpTransportResponse(
                result={
                    "request_id": request_payload.get("request_id"),
                    "mode": mode,
                    "result": {"accepted": True},
                }
            )
        if method == "notifications/roots/list_changed":
            return McpTransportResponse(result={"notified": True})
        if method == "notifications/cancelled":
            return McpTransportResponse(result={"cancelled": True})
        raise McpProtocolError(f"Unsupported in-memory MCP method: {method}")

    def receive(self, session_handle: McpTransportHandle) -> list[JsonObject]:
        server = self._servers[session_handle.server_id]
        notifications = [notification.to_dict() for notification in server.notifications]
        server.notifications.clear()
        return notifications

    def resume(
        self,
        session_handle: McpTransportHandle,
        stream_ref: str | None = None,
    ) -> McpTransportResponse:
        del session_handle, stream_ref
        return McpTransportResponse(result={})

    def close(self, session_handle: McpTransportHandle) -> None:
        self._handles.pop(session_handle.server_id, None)

    def emit_resource_updated(self, server_id: str, resource_uri: str) -> None:
        server = self._servers[server_id]
        if resource_uri in server.resource_subscriptions:
            from openagent.tools.mcp.models import McpResourceNotification

            server.notifications.append(
                McpResourceNotification(
                    server_id=server_id,
                    kind="resources/updated",
                    resource_uri=resource_uri,
                )
            )

    def emit_resource_list_changed(self, server_id: str) -> None:
        server = self._servers[server_id]
        from openagent.tools.mcp.models import McpResourceNotification

        server.notifications.append(
            McpResourceNotification(
                server_id=server_id,
                kind="resources/list_changed",
            )
        )

    def _paginate_tools(self, server: McpServerConnection, params: JsonObject) -> JsonObject:
        cursor = str(params.get("cursor", "0")) if params.get("cursor") is not None else "0"
        page_size = server.tool_page_size or len(server.tools)
        return self._paginate_items(
            [tool.to_dict() for tool, _ in server.tools.values()],
            cursor,
            page_size,
            "tools",
        )

    def _paginate_prompts(self, server: McpServerConnection, params: JsonObject) -> JsonObject:
        cursor = str(params.get("cursor", "0")) if params.get("cursor") is not None else "0"
        page_size = server.prompt_page_size or len(server.prompts)
        return self._paginate_items(
            [prompt.to_dict() for prompt in server.prompts.values()],
            cursor,
            page_size,
            "prompts",
        )

    def _paginate_resources(self, server: McpServerConnection, params: JsonObject) -> JsonObject:
        cursor = str(params.get("cursor", "0")) if params.get("cursor") is not None else "0"
        page_size = server.resource_page_size or len(server.resources)
        return self._paginate_items(
            [resource.to_dict() for resource in server.resources.values()],
            cursor,
            page_size,
            "resources",
        )

    def _paginate_items(
        self,
        items: list[JsonObject],
        cursor: str,
        page_size: int,
        key: str,
    ) -> JsonObject:
        start = int(cursor)
        end = start + page_size
        next_cursor = str(end) if end < len(items) else None
        page_items = cast(list[JsonValue], items[start:end])
        return {key: page_items, "nextCursor": next_cursor}


class StdioMcpTransport:
    """Line-delimited JSON-RPC stdio MCP transport."""

    def __init__(self) -> None:
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._locks: dict[str, threading.Lock] = {}

    def open_stdio(self, config: StdioTransportConfig, server_id: str) -> McpTransportHandle:
        process = subprocess.Popen(
            config.command,
            cwd=config.cwd,
            env=None if not config.env else {**config.env},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._processes[server_id] = process
        self._locks[server_id] = threading.Lock()
        return McpTransportHandle(transport="stdio", server_id=server_id)

    def open_http(self, config: HttpTransportConfig, server_id: str) -> McpTransportHandle:
        del config, server_id
        raise McpTransportError("StdioMcpTransport does not support HTTP")

    def send(
        self,
        session_handle: McpTransportHandle,
        message: JsonObject,
        *,
        accept_sse: bool = False,
    ) -> McpTransportResponse:
        del accept_sse
        process = self._processes[session_handle.server_id]
        if process.stdin is None or process.stdout is None:
            raise McpTransportError("Stdio MCP process does not have pipes attached")
        lock = self._locks[session_handle.server_id]
        with lock:
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()
            raw = process.stdout.readline()
        if not raw:
            stderr = process.stderr.read() if process.stderr is not None else ""
            raise McpTransportError(f"Stdio MCP server closed unexpectedly: {stderr}")
        response = json.loads(raw)
        if not isinstance(response, dict):
            raise McpProtocolError("Stdio MCP response must be a JSON object")
        result = response.get("result")
        if result is not None and not isinstance(result, dict):
            raise McpProtocolError("Stdio MCP result must be a JSON object")
        return McpTransportResponse(result=result if isinstance(result, dict) else {})

    def receive(self, session_handle: McpTransportHandle) -> list[JsonObject]:
        del session_handle
        return []

    def resume(
        self,
        session_handle: McpTransportHandle,
        stream_ref: str | None = None,
    ) -> McpTransportResponse:
        del session_handle, stream_ref
        return McpTransportResponse(result={})

    def close(self, session_handle: McpTransportHandle) -> None:
        process = self._processes.pop(session_handle.server_id, None)
        self._locks.pop(session_handle.server_id, None)
        if process is not None:
            process.terminate()
            process.wait(timeout=3)


class StreamableHttpMcpTransport:
    """HTTP MCP transport supporting JSON and SSE responses."""

    def __init__(self) -> None:
        self._configs: dict[str, HttpTransportConfig] = {}

    def open_stdio(self, config: StdioTransportConfig, server_id: str) -> McpTransportHandle:
        del config, server_id
        raise McpTransportError("StreamableHttpMcpTransport does not support stdio")

    def open_http(self, config: HttpTransportConfig, server_id: str) -> McpTransportHandle:
        self._configs[server_id] = config
        return McpTransportHandle(transport="http", server_id=server_id)

    def send(
        self,
        session_handle: McpTransportHandle,
        message: JsonObject,
        *,
        accept_sse: bool = False,
    ) -> McpTransportResponse:
        config = self._configs[session_handle.server_id]
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if accept_sse else "application/json",
            "MCP-Protocol-Version": str(message.get("_protocol_version", "2025-11-25")),
            **config.headers,
        }
        access_token = session_handle.metadata.get("access_token")
        if isinstance(access_token, str) and access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        request_body = {
            key: value
            for key, value in message.items()
            if not str(key).startswith("_")
        }
        payload = json.dumps(request_body).encode("utf-8")
        http_request = request.Request(
            config.endpoint,
            data=payload,
            headers=headers,
            method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=config.timeout_seconds) as response:
                response_headers = {key.lower(): value for key, value in response.headers.items()}
                content_type = response_headers.get("content-type", "")
                protocol_version = response_headers.get("mcp-protocol-version")
                if protocol_version is None:
                    raise McpProtocolError(
                        "HTTP MCP response is missing MCP-Protocol-Version header"
                    )
                raw_body = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            headers = {key.lower(): value for key, value in exc.headers.items()}
            return McpTransportResponse(
                result={"error": detail},
                headers=headers,
                status_code=exc.code,
            )
        except URLError as exc:
            raise McpTransportError(f"HTTP MCP transport error: {exc.reason}") from exc
        if "text/event-stream" in content_type:
            events = list(_parse_sse(raw_body))
            result: JsonObject = {}
            for event in events:
                if isinstance(event.get("result"), dict):
                    result = cast(JsonObject, event["result"])
                    break
            return McpTransportResponse(
                result=result,
                events=events,
                headers=response_headers,
            )
        body = json.loads(raw_body)
        if not isinstance(body, dict):
            raise McpProtocolError("HTTP MCP response must be a JSON object")
        response_result = body.get("result")
        return McpTransportResponse(
            result=cast(JsonObject, response_result)
            if isinstance(response_result, dict)
            else {},
            headers=response_headers,
        )

    def receive(self, session_handle: McpTransportHandle) -> list[JsonObject]:
        del session_handle
        return []

    def resume(
        self,
        session_handle: McpTransportHandle,
        stream_ref: str | None = None,
    ) -> McpTransportResponse:
        del session_handle, stream_ref
        return McpTransportResponse(result={})

    def close(self, session_handle: McpTransportHandle) -> None:
        self._configs.pop(session_handle.server_id, None)


def _parse_sse(raw_body: str) -> Iterator[JsonObject]:
    data_lines: list[str] = []
    for line in raw_body.splitlines():
        if line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())
            continue
        if line.strip() == "" and data_lines:
            joined = "\n".join(data_lines)
            payload = json.loads(joined)
            if isinstance(payload, dict):
                yield payload
            data_lines = []
    if data_lines:
        payload = json.loads("\n".join(data_lines))
        if isinstance(payload, dict):
            yield payload
