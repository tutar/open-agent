from __future__ import annotations

import json
import sys
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

from openagent.object_model import ToolResult
from openagent.tools import (
    HttpTransportConfig,
    InMemoryMcpClient,
    InMemoryMcpTransport,
    McpAuthorizationAdapter,
    McpElicitationBridge,
    McpElicitationRequest,
    McpPromptDescriptor,
    McpResourceAdapter,
    McpResourceDescriptor,
    McpRoot,
    McpRootsProvider,
    McpSamplingBridge,
    McpSamplingRequest,
    McpServerCapabilities,
    McpServerConnection,
    McpServerDescriptor,
    McpSkillAdapter,
    McpToolDescriptor,
    StdioMcpTransport,
    StdioTransportConfig,
    StreamableHttpMcpTransport,
    TransportBackedMcpClient,
)
from openagent.tools.mcp.errors import McpCapabilityError, McpProtocolError


def _echo_tool(args: dict[str, object]) -> ToolResult:
    return ToolResult(tool_name="echo", success=True, content=[str(args["text"])])


def test_mcp_initialize_and_version_negotiation() -> None:
    client = InMemoryMcpClient()
    client.connect(
        McpServerConnection(
            descriptor=McpServerDescriptor(server_id="docs", label="Docs Server"),
            server_capabilities=McpServerCapabilities(
                prompts=True,
                tools=True,
                resources=True,
            ),
        )
    )
    connected = client._connected("docs")

    assert connected.handle.protocol_version == "2025-11-25"
    assert connected.handle.initialized is True
    assert client.ping(connected.handle) is True


def test_mcp_stdio_transport_round_trip() -> None:
    transport = StdioMcpTransport()
    client = TransportBackedMcpClient(transport=transport)
    descriptor = McpServerDescriptor(server_id="stdio-docs", label="Stdio", transport="stdio")
    script = """
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        result = {
            "protocolVersion": "2025-11-25",
            "capabilities": {"tools": True},
            "serverInfo": {"server_id": "stdio-docs"},
        }
    elif method == "notifications/initialized":
        result = {}
    elif method == "ping":
        result = {"pong": True}
    else:
        result = {"echo": method}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg.get("id"), "result": result}) + "\\n")
    sys.stdout.flush()
"""
    session = client.initialize(
        descriptor,
        transport_config=StdioTransportConfig(command=[sys.executable, "-c", script]),
    )
    client.send_initialized(session)

    assert client.ping(session) is True
    client.close(session)


def test_mcp_tool_and_prompt_pagination_and_call() -> None:
    client = InMemoryMcpClient()
    client.connect(
        McpServerConnection(
            descriptor=McpServerDescriptor(server_id="docs", label="Docs Server"),
            tools={
                "echo": (McpToolDescriptor(name="echo", description="Echo text"), _echo_tool),
                "upper": (
                    McpToolDescriptor(name="upper", description="Uppercase text"),
                    lambda args: ToolResult(
                        tool_name="upper",
                        success=True,
                        content=[str(args["text"]).upper()],
                    ),
                ),
            },
            prompts={
                "review": McpPromptDescriptor(
                    name="review",
                    description="Review a document",
                    template="Review {topic}",
                ),
                "summarize": McpPromptDescriptor(
                    name="summarize",
                    description="Summarize a document",
                    template="Summarize {topic}",
                ),
            },
            tool_page_size=1,
            prompt_page_size=1,
        )
    )

    first_tools, next_tool_cursor = client.list_tools_page("docs")
    second_tools, second_tool_cursor = client.list_tools_page("docs", next_tool_cursor)
    first_prompts, next_prompt_cursor = client.list_prompts_page("docs")
    second_prompts, second_prompt_cursor = client.list_prompts_page("docs", next_prompt_cursor)

    assert [tool.name for tool in first_tools] == ["echo"]
    assert [tool.name for tool in second_tools] == ["upper"]
    assert second_tool_cursor is None
    assert [prompt.name for prompt in first_prompts] == ["review"]
    assert [prompt.name for prompt in second_prompts] == ["summarize"]
    assert second_prompt_cursor is None
    assert client.call_tool("docs", "echo", {"text": "hello"}).content == ["hello"]
    assert client.get_prompt("docs", "review", {"topic": "api"}) == "Review api"


def test_mcp_roots_list_and_list_changed() -> None:
    roots_provider = McpRootsProvider(
        roots=[McpRoot(uri="file:///workspace", name="workspace", writable=True)]
    )
    client = InMemoryMcpClient()
    client.roots_provider = roots_provider
    client.client_capabilities.roots = True
    client.connect(
        McpServerConnection(
            descriptor=McpServerDescriptor(server_id="docs", label="Docs Server"),
            roots=roots_provider.list_roots(),
        )
    )

    roots = client.list_roots("docs")
    client.notify_roots_changed("docs")

    assert roots[0].uri == "file:///workspace"
    assert roots_provider.changed is True


def test_mcp_resource_subscribe_and_list_changed() -> None:
    transport = InMemoryMcpTransport()
    client = TransportBackedMcpClient(transport=transport)
    connection = McpServerConnection(
        descriptor=McpServerDescriptor(server_id="docs", label="Docs Server", transport="inmemory"),
        resources={
            "file://plain": McpResourceDescriptor(
                uri="file://plain",
                name="Plain File",
                description="A plain text resource",
                content="raw",
            )
        },
    )
    transport.connect(connection)
    session = client.initialize(connection.descriptor)
    client.send_initialized(session)
    client.subscribe_resource("docs", "file://plain")
    transport.emit_resource_updated("docs", "file://plain")
    transport.emit_resource_list_changed("docs")
    notifications = client.poll_notifications("docs")
    adapter = McpResourceAdapter()

    assert [item["kind"] for item in notifications] == [
        "resources/updated",
        "resources/list_changed",
    ]
    projected = adapter.project_resource_notification("docs", notifications[0])
    assert projected.payload["kind"] == "resources/updated"


def test_mcp_sampling_with_tools_requires_negotiated_support() -> None:
    transport = InMemoryMcpTransport()
    client = TransportBackedMcpClient(transport=transport)
    client.client_capabilities.sampling = True
    client.sampling_bridge = McpSamplingBridge()
    connection = McpServerConnection(
        descriptor=McpServerDescriptor(server_id="docs", label="Docs Server", transport="inmemory"),
        client_capabilities=client.client_capabilities,
    )
    transport.connect(connection)
    session = client.initialize(
        connection.descriptor,
        client_capabilities=client.client_capabilities,
    )
    client.send_initialized(session)
    request = McpSamplingRequest(
        server_id="docs",
        request_id="mcp-sample-1",
        messages=[{"role": "user", "content": "hello"}],
        tools=[{"name": "echo"}],
    )

    try:
        client.handle_sampling_request("docs", request)
    except McpCapabilityError as exc:
        assert "Sampling tools" in str(exc)
    else:
        raise AssertionError("Expected McpCapabilityError")

    client.client_capabilities.experimental["sampling_tools"] = True
    result = client.handle_sampling_request("docs", request)
    assert result["action_type"] == "mcp_sampling"
    assert result["request_id"] == "mcp-sample-1"


def test_mcp_elicitation_bridge_maps_to_requires_action() -> None:
    bridge = McpElicitationBridge()
    result = bridge.handle_elicitation_request(
        "docs",
        McpElicitationRequest(
            server_id="docs",
            request_id="elicitation-1",
            mode="url",
            url="https://example.com/secret",
        ),
    )

    assert result["action_type"] == "mcp_elicitation"
    assert result["request_id"] == "elicitation-1"


def test_mcp_host_extension_skill_discovery_remains_separate() -> None:
    adapter = McpSkillAdapter()
    resources = [
        McpResourceDescriptor(
            uri="skill://summarize",
            name="Summarize",
            description="Summarize notes",
            content="Summarize {topic}",
        ),
        McpResourceDescriptor(
            uri="file://plain",
            name="Plain",
            description="Not a skill",
        ),
    ]

    discovered = adapter.discover_skills_from_resources("docs", resources)

    assert [skill.id for skill in discovered] == ["summarize"]
    assert discovered[0].metadata["loaded_from"] == "mcp"


class _FakeHeaders(dict[str, str]):
    def items(self):  # type: ignore[override]
        return super().items()


class _FakeResponse:
    def __init__(self, body: bytes, headers: dict[str, str], status: int = 200) -> None:
        self._body = body
        self.headers = _FakeHeaders(headers)
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb


def _fake_urlopen_factory(*, version: str = "2025-11-25"):
    last_headers: dict[str, str] = {}

    def _fake_urlopen(http_request, timeout=None):  # type: ignore[no-untyped-def]
        del timeout
        url = http_request.full_url
        headers = {str(k).lower(): str(v) for k, v in http_request.header_items()}
        last_headers.clear()
        last_headers.update(headers)
        if url.endswith("/.well-known/oauth-authorization-server"):
            return _FakeResponse(
                json.dumps(
                    {
                        "issuer": "http://127.0.0.1",
                        "token_endpoint": "http://127.0.0.1/token",
                        "scopes_supported": ["mcp.read"],
                    }
                ).encode("utf-8"),
                {"content-type": "application/json", "mcp-protocol-version": version},
            )
        if url.endswith("/token"):
            body = json.loads((http_request.data or b"{}").decode("utf-8"))
            scopes = body.get("scopes", [])
            scope_string = " ".join(scopes) if isinstance(scopes, list) else ""
            token = "token-tools" if "mcp.tools.call" in scope_string else "token-read"
            return _FakeResponse(
                json.dumps({"access_token": token, "scope": scope_string}).encode("utf-8"),
                {"content-type": "application/json", "mcp-protocol-version": version},
            )
        payload = json.loads((http_request.data or b"{}").decode("utf-8"))
        method = payload.get("method")
        authorization = headers.get("authorization", "")
        if method == "tools/call" and authorization != "Bearer token-tools":
            response_body = json.dumps({"error": "missing_scope"}).encode("utf-8")
            raise HTTPError(
                url=url,
                code=401,
                msg="Unauthorized",
                hdrs=_FakeHeaders(
                    {
                        "content-type": "application/json",
                        "mcp-protocol-version": version,
                        "www-authenticate": 'Bearer scope="mcp.tools.call"',
                    }
                ),
                fp=BytesIO(response_body),
            )
        if method == "initialize":
            result = {
                "protocolVersion": version,
                "capabilities": {"tools": True, "prompts": True, "resources": True},
                "serverInfo": {"server_id": "http-docs"},
            }
        elif method == "tools/call":
            result = ToolResult(tool_name="echo", success=True, content=["hello"]).to_dict()
        else:
            result = {"pong": True}
        accept = headers.get("accept", "")
        if "text/event-stream" in accept:
            body = f"data: {json.dumps({'result': result})}\n\n".encode()
            return _FakeResponse(
                body,
                {"content-type": "text/event-stream", "mcp-protocol-version": version},
            )
        return _FakeResponse(
            json.dumps({"jsonrpc": "2.0", "result": result}).encode("utf-8"),
            {"content-type": "application/json", "mcp-protocol-version": version},
        )

    return _fake_urlopen, last_headers


def test_mcp_streamable_http_post_and_sse_and_auth_scope_upgrade() -> None:
    fake_urlopen, last_headers = _fake_urlopen_factory()
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        endpoint = "http://127.0.0.1/mcp"
        transport = StreamableHttpMcpTransport()
        auth = McpAuthorizationAdapter(scope_token_map={"mcp.tools.call": "token-tools"})
        client = TransportBackedMcpClient(transport=transport, auth=auth)
        descriptor = McpServerDescriptor(
            server_id="http-docs",
            label="HTTP Docs",
            endpoint=endpoint,
            transport="http",
        )
        session = client.initialize(
            descriptor,
            transport_config=HttpTransportConfig(endpoint=endpoint),
        )
        client.send_initialized(session)
        result = client.call_tool("http-docs", "echo", {"text": "hello"})

        assert result.content == ["hello"]
        assert last_headers["mcp-protocol-version"] == "2025-11-25"
        connected = client._connected("http-docs")
        assert connected.auth_state is not None
        assert "mcp.tools.call" in connected.auth_state.scopes
        assert last_headers["authorization"] == "Bearer token-tools"


def test_mcp_initialize_rejects_version_mismatch() -> None:
    fake_urlopen, _ = _fake_urlopen_factory(version="2024-01-01")
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        endpoint = "http://127.0.0.1/mcp"
        client = TransportBackedMcpClient(transport=StreamableHttpMcpTransport())
        try:
            client.initialize(
                McpServerDescriptor(
                    server_id="bad-http",
                    label="Bad HTTP",
                    endpoint=endpoint,
                    transport="http",
                ),
                transport_config=HttpTransportConfig(endpoint=endpoint),
            )
        except McpProtocolError as exc:
            assert "version mismatch" in str(exc)
        else:
            raise AssertionError("Expected McpProtocolError")
