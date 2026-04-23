import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError

import pytest

from openagent.harness.context_engineering.instruction_markdown.loader import (
    InstructionMarkdownLoader,
)
from openagent.harness.providers import (
    AnthropicMessagesModelAdapter,
    OpenAIChatCompletionsModelAdapter,
    load_model_from_env,
)
from openagent.harness.providers.base import (
    HttpResponse,
    ProviderConfigurationError,
    ProviderError,
    UrllibHttpTransport,
)
from openagent.harness.runtime import (
    FileModelIoCapture,
    ModelProviderExchange,
    ModelStreamEvent,
    ModelTurnRequest,
    ModelTurnResponse,
    SimpleHarness,
)
from openagent.object_model import JsonObject, ToolResult
from openagent.session import InMemorySessionStore, InMemoryShortTermMemoryStore
from openagent.tools import (
    BashTool,
    GlobTool,
    SimpleToolExecutor,
    StaticToolRegistry,
    ToolCall,
    WebSearchTool,
)


@dataclass(slots=True)
class FakeTransport:
    response_body: JsonObject
    seen_url: str | None = None
    seen_payload: JsonObject | None = None
    seen_headers: dict[str, str] = field(default_factory=dict)

    def post_json(
        self,
        url: str,
        payload: JsonObject,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> HttpResponse:
        del timeout_seconds
        self.seen_url = url
        self.seen_payload = payload
        self.seen_headers = headers
        return HttpResponse(status_code=200, body=self.response_body)

    def post_json_stream(
        self,
        url: str,
        payload: JsonObject,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> Iterator[str]:
        del url, payload, headers, timeout_seconds
        raise AssertionError("Streaming path should not use FakeTransport.post_json_stream")


@dataclass(slots=True)
class FakeStreamingTransport:
    stream_lines: list[str]
    seen_url: str | None = None
    seen_payload: JsonObject | None = None
    seen_headers: dict[str, str] = field(default_factory=dict)

    def post_json(
        self,
        url: str,
        payload: JsonObject,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> HttpResponse:
        del url, payload, headers, timeout_seconds
        raise AssertionError("Streaming path should not use FakeStreamingTransport.post_json")

    def post_json_stream(
        self,
        url: str,
        payload: JsonObject,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> Iterator[str]:
        del timeout_seconds
        self.seen_url = url
        self.seen_payload = payload
        self.seen_headers = headers
        yield from self.stream_lines


@dataclass(slots=True)
class EchoTool:
    name: str = "echo"
    input_schema: dict[str, object] = field(
        default_factory=lambda: {"type": "object", "properties": {"text": {"type": "string"}}}
    )

    def description(self) -> str:
        return "Echo the provided text."

    def call(self, arguments: dict[str, object]) -> ToolResult:
        return ToolResult(tool_name=self.name, success=True, content=[str(arguments.get("text"))])

    def check_permissions(self, arguments: dict[str, object]) -> str:
        del arguments
        return "allow"

    def is_concurrency_safe(self) -> bool:
        return True


@dataclass(slots=True)
class ToolThenReplyModel:
    responses: list[ModelTurnResponse]

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        del request
        return self.responses.pop(0)


@dataclass(slots=True)
class ExchangeBackedModel:
    exchange: ModelProviderExchange

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        del request
        return self.exchange.response

    def generate_with_exchange(self, request: ModelTurnRequest) -> ModelProviderExchange:
        del request
        return self.exchange


def test_openai_chat_adapter_builds_tool_payload_and_parses_tool_calls() -> None:
    transport = FakeTransport(
        response_body={
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "echo",
                                    "arguments": '{"text": "payload"}',
                                },
                            }
                        ],
                    }
                }
            ]
        }
    )
    adapter = OpenAIChatCompletionsModelAdapter(
        model="gpt-test",
        base_url="http://127.0.0.1:8001",
        transport=transport,
    )

    response = adapter.generate(
        ModelTurnRequest(
            session_id="sess_1",
            messages=[{"role": "user", "content": "use echo"}],
            tool_definitions=[
                {
                    "name": "echo",
                    "description": "Echo text.",
                    "input_schema": {"type": "object"},
                }
            ],
        )
    )

    assert transport.seen_url == "http://127.0.0.1:8001/v1/chat/completions"
    assert transport.seen_payload is not None
    assert transport.seen_payload["model"] == "gpt-test"
    assert isinstance(transport.seen_payload["tools"], list)
    assert transport.seen_payload["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "Echo text.",
                "parameters": {"type": "object"},
            },
        }
    ]
    assert response.tool_calls == [
        ToolCall(tool_name="echo", arguments={"text": "payload"}, call_id="call_1")
    ]


def test_openai_chat_adapter_streams_deltas_and_sets_stream_true() -> None:
    transport = FakeStreamingTransport(
        stream_lines=[
            'data: {"choices":[{"delta":{"content":"hello "}}]}\n',
            "\n",
            (
                'data: {"choices":[{"delta":{"content":"world"}}],'
                '"usage":{"prompt_tokens":3,"completion_tokens":2}}\n'
            ),
            "\n",
            "data: [DONE]\n",
            "\n",
        ]
    )
    adapter = OpenAIChatCompletionsModelAdapter(
        model="gpt-test",
        base_url="http://127.0.0.1:8001",
        transport=transport,
    )

    events = list(
        adapter.stream_generate(
            ModelTurnRequest(
                session_id="sess_stream",
                messages=[{"role": "user", "content": "stream"}],
            )
        )
    )

    assert transport.seen_url == "http://127.0.0.1:8001/v1/chat/completions"
    assert transport.seen_payload is not None
    assert transport.seen_payload["stream"] is True
    assert [event.assistant_delta for event in events[:-1]] == ["hello ", "world"]
    assert events[-1] == ModelStreamEvent(
        assistant_message="hello world",
        tool_calls=[],
        usage={"prompt_tokens": 3, "completion_tokens": 2},
    )


def test_provider_transport_reports_empty_http_error_body_readably(monkeypatch) -> None:
    def _raise_http_error(*args, **kwargs):
        del args, kwargs
        raise HTTPError(
            url="http://127.0.0.1:8001/v1/chat/completions",
            code=502,
            msg="Bad Gateway",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr("openagent.harness.providers.base.request.urlopen", _raise_http_error)

    with pytest.raises(ProviderError) as exc:
        UrllibHttpTransport().post_json(
            "http://127.0.0.1:8001/v1/chat/completions",
            {"model": "gpt-test", "messages": []},
            {},
            10.0,
        )

    assert str(exc.value) == "HTTP 502: upstream returned an empty error body"


def test_load_model_from_env_resolves_single_advertised_openai_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            del exc_type, exc, tb

        def read(self) -> bytes:
            return json.dumps(
                {
                    "data": [
                        {
                            "id": "unsloth/Qwen3.5-9B-GGUF",
                        }
                    ]
                }
            ).encode("utf-8")

    monkeypatch.setenv("OPENAGENT_PROVIDER", "openai")
    monkeypatch.setenv("OPENAGENT_BASE_URL", "http://127.0.0.1:8001")
    monkeypatch.setenv("OPENAGENT_MODEL", "gpt-4.1")
    monkeypatch.setattr(
        "openagent.harness.providers.request.urlopen",
        lambda req, timeout=10.0: _Response(),
    )

    adapter = load_model_from_env()

    assert isinstance(adapter, OpenAIChatCompletionsModelAdapter)
    assert adapter.model == "unsloth/Qwen3.5-9B-GGUF"


def test_load_model_from_env_rejects_unknown_openai_model_when_multiple_models_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            del exc_type, exc, tb

        def read(self) -> bytes:
            return json.dumps(
                {
                    "data": [
                        {"id": "model-a"},
                        {"id": "model-b"},
                    ]
                }
            ).encode("utf-8")

    monkeypatch.setenv("OPENAGENT_PROVIDER", "openai")
    monkeypatch.setenv("OPENAGENT_BASE_URL", "http://127.0.0.1:8001")
    monkeypatch.setenv("OPENAGENT_MODEL", "gpt-4.1")
    monkeypatch.setattr(
        "openagent.harness.providers.request.urlopen",
        lambda req, timeout=10.0: _Response(),
    )

    with pytest.raises(ProviderConfigurationError) as exc:
        load_model_from_env()

    assert "requested=gpt-4.1" in str(exc.value)
    assert "available=[model-a, model-b]" in str(exc.value)


def test_anthropic_adapter_builds_tool_payload_and_parses_tool_use() -> None:
    transport = FakeTransport(
        response_body={
            "content": [
                {"type": "text", "text": "Let me check."},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "echo",
                    "input": {"text": "payload"},
                },
            ]
        }
    )
    adapter = AnthropicMessagesModelAdapter(
        model="claude-test",
        base_url="http://127.0.0.1:8001",
        transport=transport,
    )

    response = adapter.generate(
        ModelTurnRequest(
            session_id="sess_1",
            messages=[
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "use echo"},
                {
                    "role": "tool",
                    "content": "done",
                    "metadata": {"tool_use_id": "toolu_prev"},
                },
            ],
            tool_definitions=[
                {
                    "name": "echo",
                    "description": "Echo text.",
                    "input_schema": {"type": "object"},
                }
            ],
        )
    )

    assert transport.seen_url == "http://127.0.0.1:8001/v1/messages"
    assert transport.seen_payload is not None
    assert transport.seen_payload["system"] == "system prompt"
    assert isinstance(transport.seen_payload["tools"], list)
    assert isinstance(transport.seen_payload["messages"], list)
    assert transport.seen_payload["tools"] == [
        {
            "name": "echo",
            "description": "Echo text.",
            "input_schema": {"type": "object"},
        }
    ]
    messages_payload = transport.seen_payload["messages"]
    assert isinstance(messages_payload, list)
    assert messages_payload[-1] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "toolu_prev", "content": "done"}],
    }
    assert response.assistant_message == "Let me check."
    assert response.tool_calls == [
        ToolCall(tool_name="echo", arguments={"text": "payload"}, call_id="toolu_1")
    ]


def test_provider_adapters_include_short_term_memory_summary() -> None:
    openai_transport = FakeTransport(response_body={"choices": [{"message": {"content": "ok"}}]})
    openai_adapter = OpenAIChatCompletionsModelAdapter(
        model="gpt-test",
        base_url="http://127.0.0.1:8001",
        transport=openai_transport,
    )
    anthropic_transport = FakeTransport(response_body={"content": [{"type": "text", "text": "ok"}]})
    anthropic_adapter = AnthropicMessagesModelAdapter(
        model="claude-test",
        base_url="http://127.0.0.1:8001",
        transport=anthropic_transport,
    )
    request = ModelTurnRequest(
        session_id="sess_1",
        messages=[{"role": "user", "content": "continue"}],
        short_term_memory={"summary": "User wants to continue the launch checklist."},
    )

    openai_adapter.generate(request)
    anthropic_adapter.generate(request)

    assert openai_transport.seen_payload is not None
    assert anthropic_transport.seen_payload is not None
    openai_messages = openai_transport.seen_payload["messages"]
    assert isinstance(openai_messages, list)
    assert openai_messages[0] == {
        "role": "system",
        "content": "Session continuity summary: User wants to continue the launch checklist.",
    }
    assert anthropic_transport.seen_payload["system"] == (
        "Session continuity summary: User wants to continue the launch checklist."
    )


def test_provider_adapters_include_bootstrap_system_prompt() -> None:
    openai_transport = FakeTransport(response_body={"choices": [{"message": {"content": "ok"}}]})
    openai_adapter = OpenAIChatCompletionsModelAdapter(
        model="gpt-test",
        base_url="http://127.0.0.1:8001",
        transport=openai_transport,
    )
    anthropic_transport = FakeTransport(response_body={"content": [{"type": "text", "text": "ok"}]})
    anthropic_adapter = AnthropicMessagesModelAdapter(
        model="claude-test",
        base_url="http://127.0.0.1:8001",
        transport=anthropic_transport,
    )
    request = ModelTurnRequest(
        session_id="sess_bootstrap",
        messages=[{"role": "user", "content": "list files"}],
        system_prompt="You are OpenAgent.\nWorkspace root: /tmp/workspace",
    )

    openai_adapter.generate(request)
    anthropic_adapter.generate(request)

    assert openai_transport.seen_payload is not None
    openai_messages = openai_transport.seen_payload["messages"]
    assert isinstance(openai_messages, list)
    assert openai_messages[0] == {
        "role": "system",
        "content": "You are OpenAgent.\nWorkspace root: /tmp/workspace",
    }
    assert anthropic_transport.seen_payload is not None
    assert anthropic_transport.seen_payload["system"] == (
        "You are OpenAgent.\nWorkspace root: /tmp/workspace"
    )


def test_openai_adapter_merges_system_planes_into_single_prefix() -> None:
    transport = FakeTransport(response_body={"choices": [{"message": {"content": "ok"}}]})
    adapter = OpenAIChatCompletionsModelAdapter(
        model="gpt-test",
        base_url="http://127.0.0.1:8001",
        transport=transport,
    )

    adapter.generate(
        ModelTurnRequest(
            session_id="sess_system_merge",
            system_prompt="You are OpenAgent.",
            messages=[
                {"role": "system", "content": "Startup context (turn_zero): first turn."},
                {"role": "user", "content": "当前目录下有哪些文件"},
            ],
            short_term_memory={"summary": "User is asking about workspace files."},
            memory_context=[{"summary": "Project root is the current working directory."}],
        )
    )

    assert transport.seen_payload is not None
    messages = transport.seen_payload["messages"]
    assert isinstance(messages, list)
    assert messages[0]["role"] == "system"
    assert "You are OpenAgent." in str(messages[0]["content"])
    assert "Startup context (turn_zero): first turn." in str(messages[0]["content"])
    assert "Session continuity summary: User is asking about workspace files." in str(
        messages[0]["content"]
    )
    assert "Relevant memory: Project root is the current working directory." in str(
        messages[0]["content"]
    )
    assert [message["role"] for message in messages] == ["system", "user"]


def test_openai_chat_adapter_emits_complete_builtin_tool_schema() -> None:
    transport = FakeTransport(response_body={"choices": [{"message": {"content": "ok"}}]})
    adapter = OpenAIChatCompletionsModelAdapter(
        model="gpt-test",
        base_url="http://127.0.0.1:8001",
        transport=transport,
    )

    adapter.generate(
        ModelTurnRequest(
            session_id="sess_builtin_schema",
            messages=[{"role": "user", "content": "list files"}],
            tool_definitions=[
                {
                    "name": tool.name,
                    "description": tool.description(),
                    "input_schema": tool.input_schema,
                }
                for tool in (GlobTool("."), BashTool("."), WebSearchTool())
            ],
        )
    )

    assert transport.seen_payload is not None
    tools_payload = transport.seen_payload["tools"]
    assert isinstance(tools_payload, list)

    by_name = {
        item["function"]["name"]: item["function"]
        for item in tools_payload
        if isinstance(item, dict) and isinstance(item.get("function"), dict)
    }
    glob_parameters = by_name["Glob"]["parameters"]
    bash_parameters = by_name["Bash"]["parameters"]
    search_parameters = by_name["WebSearch"]["parameters"]

    assert glob_parameters["properties"]["pattern"]["description"]
    assert glob_parameters["properties"]["pattern"]["examples"] == ["*", "*.py", "src/**/*.py"]
    assert glob_parameters["additionalProperties"] is False
    assert bash_parameters["properties"]["command"]["description"]
    assert bash_parameters["properties"]["command"]["examples"] == [
        "ls -la",
        "pwd",
        "pytest -q tests/tools/test_tools_alignment.py",
    ]
    assert search_parameters["properties"]["query"]["description"]
    assert search_parameters["properties"]["query"]["examples"] == [
        "qwen3.6",
        "OpenAgent bootstrap prompts",
    ]


def test_openai_adapter_generate_with_exchange_exposes_payload_and_raw_response() -> None:
    transport = FakeTransport(
        response_body={
            "choices": [
                {
                    "message": {
                        "content": "ok",
                        "reasoning_content": "draft reasoning",
                    }
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        }
    )
    adapter = OpenAIChatCompletionsModelAdapter(
        model="gpt-test",
        base_url="http://127.0.0.1:8001",
        transport=transport,
    )

    exchange = adapter.generate_with_exchange(
        ModelTurnRequest(
            session_id="sess_exchange",
            messages=[{"role": "user", "content": "hello"}],
        )
    )

    assert exchange.provider_payload == transport.seen_payload
    assert exchange.raw_response == transport.response_body
    assert exchange.reasoning == "draft reasoning"
    assert exchange.response.assistant_message == "ok"


def test_anthropic_adapter_generate_with_exchange_exposes_reasoning_blocks() -> None:
    transport = FakeTransport(
        response_body={
            "content": [
                {"type": "thinking", "thinking": "chain"},
                {"type": "text", "text": "answer"},
            ]
        }
    )
    adapter = AnthropicMessagesModelAdapter(
        model="claude-test",
        base_url="http://127.0.0.1:8001",
        transport=transport,
    )

    exchange = adapter.generate_with_exchange(
        ModelTurnRequest(
            session_id="sess_reasoning",
            messages=[{"role": "user", "content": "hello"}],
        )
    )

    assert exchange.raw_response == transport.response_body
    assert exchange.reasoning == [{"type": "thinking", "thinking": "chain"}]
    assert exchange.response.assistant_message == "answer"


def test_harness_build_model_input_includes_tool_definitions() -> None:
    tool = EchoTool()
    harness = SimpleHarness(
        model=ToolThenReplyModel(responses=[ModelTurnResponse(assistant_message="ok")]),
        sessions=InMemorySessionStore(),
        tools=StaticToolRegistry([tool]),
        executor=SimpleToolExecutor(StaticToolRegistry([tool])),
    )
    session = harness.sessions.load_session("sess_tools")
    session.messages.append(harness._new_session_message(role="user", content="hi"))

    request = harness.build_model_input(session, [])

    assert request.available_tools == ["echo"]
    assert request.tool_definitions == [
        {
            "name": "echo",
            "description": "Echo the provided text.",
            "input_schema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
            },
        }
    ]


def test_harness_build_model_input_includes_bootstrap_prompt_sections(tmp_path: Path) -> None:
    tool = EchoTool()
    harness = SimpleHarness(
        model=ToolThenReplyModel(responses=[ModelTurnResponse(assistant_message="ok")]),
        sessions=InMemorySessionStore(),
        tools=StaticToolRegistry([tool]),
        executor=SimpleToolExecutor(StaticToolRegistry([tool])),
    )
    session = harness.sessions.load_session("sess_bootstrap")
    session.metadata["workdir"] = str(tmp_path)
    session.messages.append(harness._new_session_message(role="user", content="show files"))

    request = harness.build_model_input(session, [])

    assert request.system_prompt is not None
    assert "OpenAgent" in request.system_prompt
    assert f"Workspace root: {tmp_path.resolve()}" in request.system_prompt
    section_names = [section["name"] for section in request.prompt_sections]
    assert section_names == [
        "default_behavior",
        "agent_identity",
        "operating_mode",
        "workspace_context",
        "tool_usage_contract",
        "environment_summary",
    ]
    assert request.prompt_blocks is not None
    assert [item["kind"] for item in request.startup_contexts] == ["session_start", "turn_zero"]
    assert all(message["role"] != "system" for message in request.messages)


def test_harness_build_model_input_includes_short_term_memory() -> None:
    harness = SimpleHarness(
        model=ToolThenReplyModel(responses=[ModelTurnResponse(assistant_message="ok")]),
        sessions=InMemorySessionStore(),
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
        short_term_memory_store=InMemoryShortTermMemoryStore(),
    )
    session = harness.sessions.load_session("sess_short_term")
    session.messages.append(
        harness._new_session_message(role="user", content="Keep tracking the rollout")
    )
    session.messages.append(
        harness._new_session_message(role="assistant", content="I am tracking it")
    )

    harness.schedule_memory_maintenance(session)
    harness.stabilize_short_term_memory(session, timeout_ms=1000)
    request = harness.build_model_input(session, [])

    assert request.short_term_memory is not None
    assert "rollout" in str(request.short_term_memory["summary"]).lower()


def test_tool_results_preserve_tool_use_id_in_session_messages() -> None:
    tool = EchoTool()
    registry = StaticToolRegistry([tool])
    harness = SimpleHarness(
        model=ToolThenReplyModel(
            responses=[
                ModelTurnResponse(tool_calls=[ToolCall(tool_name="echo", arguments={"text": "x"})]),
                ModelTurnResponse(assistant_message="done"),
            ]
        ),
        sessions=InMemorySessionStore(),
        tools=registry,
        executor=SimpleToolExecutor(registry),
    )

    harness.run_turn("use tool", "sess_tool_metadata")
    session = harness.sessions.load_session("sess_tool_metadata")
    tool_messages = [message for message in session.messages if message.role == "tool"]

    assert len(tool_messages) == 1
    assert tool_messages[0].metadata["tool_use_id"] == "toolu_1"


def test_instruction_markdown_loader_merges_home_workspace_and_target_hierarchy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    (home / ".openagent").mkdir(parents=True)
    (home / ".openagent" / "AGENTS.md").write_text("Global guidance\nOwner: global\n", "utf-8")
    workdir = tmp_path / "repo"
    nested = workdir / "pkg" / "feature"
    sibling = workdir / "pkg" / "other"
    nested.mkdir(parents=True)
    sibling.mkdir(parents=True)
    (workdir / "AGENTS.md").write_text("Project guidance\nOwner: project\n", "utf-8")
    (nested / "AGENTS.md").write_text("Feature guidance\nOwner: subtree\n", "utf-8")
    (sibling / "AGENTS.md").write_text("Sibling guidance\n", "utf-8")

    documents = InstructionMarkdownLoader().load(
        workspace_root=str(workdir),
        runtime_state={"target_path": "pkg/feature/file.py"},
    )

    rendered = "\n".join(
        rule.text
        for document in documents
        for rule in document.rules
    )

    assert "Global guidance" in rendered
    assert "Project guidance" in rendered
    assert "Feature guidance" in rendered
    assert "Sibling guidance" not in rendered


def test_model_io_capture_persists_request_and_response_records(tmp_path: Path) -> None:
    model_io_root = tmp_path / "data" / "model-io"
    harness = SimpleHarness(
        model=ExchangeBackedModel(
            exchange=ModelProviderExchange(
                response=ModelTurnResponse(assistant_message="captured"),
                provider_payload={"model": "gpt-test", "messages": [{"role": "user"}]},
                raw_response={
                    "choices": [{"message": {"content": "captured"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                },
                reasoning="deliberation",
            )
        ),
        sessions=InMemorySessionStore(),
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
        short_term_memory_store=InMemoryShortTermMemoryStore(),
        model_io_capture=FileModelIoCapture(model_io_root),
    )
    session = harness.sessions.load_session("sess_capture")
    session.messages.append(harness._new_session_message(role="user", content="first"))
    harness.schedule_memory_maintenance(session)
    harness.stabilize_short_term_memory(session, timeout_ms=1000)

    events, terminal = harness.run_turn("hello", "sess_capture")

    assert terminal.reason == "assistant_message"
    assert any(event.event_type.value == "assistant_message" for event in events)
    index_path = model_io_root / "index.jsonl"
    assert index_path.exists()
    rows = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["session_id"] == "sess_capture"
    assert row["assembled_request"]["messages"][-1]["content"] == "hello"
    assert row["assembled_request"]["short_term_memory"]["summary"]
    assert row["provider_payload"]["model"] == "gpt-test"
    assert row["provider_response_raw"]["usage"]["prompt_tokens"] == 3
    assert row["parsed_response"]["assistant_message"] == "captured"
    assert row["reasoning"] == "deliberation"
    assert row["record_path"]
    record = json.loads(Path(row["record_path"]).read_text(encoding="utf-8"))
    assert record["capture_id"] == row["capture_id"]
