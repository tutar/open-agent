import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from openagent.harness.context_engineering import BootstrapPromptAssembler
from openagent.harness.context_engineering.instruction_markdown.loader import (
    InstructionMarkdownLoader,
)
from openagent.harness.providers import (
    InstructorModelAdapter,
    ProviderConfigurationError,
    ProviderError,
    load_model_from_env,
)
from openagent.harness.providers.instructor_adapter import build_action_models
from openagent.harness.runtime import (
    FileModelIoCapture,
    ModelProviderExchange,
    ModelStreamEvent,
    ModelTurnRequest,
    ModelTurnResponse,
    SimpleHarness,
)
from openagent.object_model import (
    JsonObject,
    RuntimeEventType,
    ToolResult,
    image_block,
    render_tool_result_content,
    text_block,
    tool_reference_block,
)
from openagent.session import FileSessionStore, InMemoryShortTermMemoryStore, SessionMessage
from openagent.tools import (
    ASK_USER_QUESTION_TOOL_NAME,
    BASH_TOOL_NAME,
    BashTool,
    EDIT_TOOL_NAME,
    FileEditTool,
    FileReadTool,
    FileWriteTool,
    GLOB_TOOL_NAME,
    GREP_TOOL_NAME,
    GlobTool,
    GrepTool,
    READ_TOOL_NAME,
    SimpleToolExecutor,
    StaticToolRegistry,
    ToolCall,
    WEB_FETCH_TOOL_NAME,
    WEB_SEARCH_TOOL_NAME,
    WebSearchTool,
    WRITE_TOOL_NAME,
    create_builtin_toolset,
)


@dataclass(slots=True)
class FakeRespondAction:
    message: str | None = None

    __openagent_action_kind__ = "respond"

    def model_dump(self, exclude_none: bool = True) -> JsonObject:
        payload: JsonObject = {"message": self.message}
        if exclude_none:
            payload = {key: value for key, value in payload.items() if value is not None}
        return payload


@dataclass(slots=True)
class FakeEchoToolAction:
    text: str | None = None
    call_id: str | None = None

    __openagent_action_kind__ = "tool"
    __openagent_tool_name__ = "echo"

    def model_dump(self, exclude_none: bool = True) -> JsonObject:
        payload: JsonObject = {"text": self.text, "call_id": self.call_id}
        if exclude_none:
            payload = {key: value for key, value in payload.items() if value is not None}
        return payload


@dataclass(slots=True)
class FakeCompletion:
    body: JsonObject

    def model_dump(self, mode: str = "json") -> JsonObject:
        del mode
        return dict(self.body)


@dataclass(slots=True)
class FakeInstructorClient:
    structured_result: object
    completion: FakeCompletion | None = None
    partials: list[object] = field(default_factory=list)
    seen_create_kwargs: dict[str, object] = field(default_factory=dict)
    seen_partial_kwargs: dict[str, object] = field(default_factory=dict)

    def create_with_completion(self, **kwargs):
        self.seen_create_kwargs = dict(kwargs)
        completion = self.completion or FakeCompletion({"choices": [{"message": {"content": "ok"}}]})
        return self.structured_result, completion

    def create_partial(self, **kwargs):
        self.seen_partial_kwargs = dict(kwargs)
        return iter(self.partials)


class FailingInstructorClient(FakeInstructorClient):
    failure: Exception | None = None

    def create_with_completion(self, **kwargs):
        self.seen_create_kwargs = dict(kwargs)
        assert self.failure is not None
        raise self.failure


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


@dataclass(slots=True)
class StreamingExchangeBackedModel:
    chunks: list[ModelStreamEvent]

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        del request
        raise AssertionError("Streaming path should use stream_generate")

    def stream_generate(self, request: ModelTurnRequest) -> Iterator[ModelStreamEvent]:
        del request
        yield from self.chunks


def openai_adapter(**kwargs: object) -> InstructorModelAdapter:
    return InstructorModelAdapter(provider="openai", **kwargs)


def anthropic_adapter(**kwargs: object) -> InstructorModelAdapter:
    return InstructorModelAdapter(provider="anthropic", max_tokens=1024, **kwargs)


def test_openai_chat_adapter_builds_projected_messages_and_parses_structured_tool_calls() -> None:
    client = FakeInstructorClient(
        structured_result=FakeEchoToolAction(text="payload", call_id="call_1"),
        completion=FakeCompletion(
            {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "echo", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            }
        ),
    )
    adapter = openai_adapter(
        model="gpt-test",
        base_url="http://127.0.0.1:8001",
        instructor_client=client,
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

    assert client.seen_create_kwargs["model"] == "gpt-test"
    messages_payload = client.seen_create_kwargs["messages"]
    assert isinstance(messages_payload, list)
    assert messages_payload[-1] == {"role": "user", "content": "use echo"}
    assert "response_model" in client.seen_create_kwargs
    assert response.tool_calls == [
        ToolCall(tool_name="echo", arguments={"text": "payload"}, call_id="call_1")
    ]
    assert response.usage == {"prompt_tokens": 3, "completion_tokens": 2}


def test_openai_chat_adapter_streams_partial_messages() -> None:
    client = FakeInstructorClient(
        structured_result=FakeRespondAction(),
        partials=[
            FakeRespondAction(message="hello "),
            FakeRespondAction(message="hello world"),
        ],
    )
    adapter = openai_adapter(
        model="gpt-test",
        base_url="http://127.0.0.1:8001",
        instructor_client=client,
    )

    events = list(
        adapter.stream_generate(
            ModelTurnRequest(
                session_id="sess_stream",
                messages=[{"role": "user", "content": "stream"}],
            )
        )
    )

    assert client.seen_partial_kwargs["stream"] is True
    assert client.seen_partial_kwargs["messages"] == [{"role": "user", "content": "stream"}]
    assert [event.assistant_delta for event in events[:-1]] == ["hello ", "world"]
    assert events[-1] == ModelStreamEvent(
        assistant_message="hello world",
        tool_calls=[],
        provider_payload={"model": "gpt-test", "messages": [{"role": "user", "content": "stream"}], "stream": True},
        raw_provider_events=[],
        reasoning=None,
        transport_metadata={"streaming": True, "via_instructor": True},
    )


def test_openai_chat_adapter_streams_partial_tool_calls_and_validates_final_shape() -> None:
    client = FakeInstructorClient(
        structured_result=FakeRespondAction(),
        partials=[
            FakeEchoToolAction(),
            FakeEchoToolAction(text="payload", call_id="call_1"),
        ],
    )
    adapter = openai_adapter(
        model="gpt-test",
        base_url="http://127.0.0.1:8001",
        instructor_client=client,
    )

    events = list(
        adapter.stream_generate(
            ModelTurnRequest(
                session_id="sess_stream_tools",
                messages=[{"role": "user", "content": "use echo"}],
                tool_definitions=[
                    {
                        "name": "echo",
                        "description": "Echo text.",
                        "input_schema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"],
                        },
                    }
                ],
            )
        )
    )

    assert "response_model" in client.seen_partial_kwargs
    assert events[-1].assistant_message is None
    assert events[-1].tool_calls == [
        ToolCall(tool_name="echo", arguments={"text": "payload"}, call_id="call_1")
    ]


def test_openai_chat_adapter_stream_falls_back_to_non_stream_when_partials_are_empty() -> None:
    client = FakeInstructorClient(
        structured_result=FakeEchoToolAction(text="payload", call_id="call_1"),
        completion=FakeCompletion({"choices": [{"message": {"content": None}}]}),
        partials=[],
    )
    adapter = openai_adapter(
        model="gpt-test",
        base_url="http://127.0.0.1:8001",
        instructor_client=client,
    )

    events = list(
        adapter.stream_generate(
            ModelTurnRequest(
                session_id="sess_stream_fallback",
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
    )

    assert len(events) == 1
    assert events[-1].tool_calls == [
        ToolCall(tool_name="echo", arguments={"text": "payload"}, call_id="call_1")
    ]
    assert events[-1].transport_metadata == {
        "streaming": True,
        "via_instructor": True,
        "stream_fallback": "generate_with_exchange",
    }


def test_openai_chat_adapter_recovers_raw_assistant_text_from_instructor_retry_exception() -> None:
    class FailedAttempt:
        def __init__(self, completion: object):
            self.completion = completion

    class RetryFailure(Exception):
        def __init__(self, completion: object):
            super().__init__("Instructor does not support multiple tool calls")
            self.failed_attempts = [FailedAttempt(completion)]

    completion = FakeCompletion(
        {
            "choices": [
                {
                    "message": {
                        "content": "final summary",
                    }
                }
            ],
            "usage": {"prompt_tokens": 4, "completion_tokens": 3},
        }
    )
    client = FailingInstructorClient(structured_result=FakeRespondAction())
    client.failure = RetryFailure(completion)
    adapter = openai_adapter(
        model="gpt-test",
        base_url="http://127.0.0.1:8001",
        instructor_client=client,
    )

    exchange = adapter.generate_with_exchange(
        ModelTurnRequest(
            session_id="sess_retry_recover",
            messages=[{"role": "user", "content": "summarize"}],
            tool_definitions=[
                {
                    "name": "echo",
                    "description": "Echo text.",
                    "input_schema": {"type": "object"},
                }
            ],
        )
    )

    assert exchange.response.assistant_message == "final summary"
    assert exchange.response.tool_calls == []
    assert exchange.response.usage == {"prompt_tokens": 4, "completion_tokens": 3}
    assert exchange.raw_response == {
        "choices": [{"message": {"content": "final summary"}}],
        "usage": {"prompt_tokens": 4, "completion_tokens": 3},
    }


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

    assert isinstance(adapter, InstructorModelAdapter)
    assert adapter.model == "unsloth/Qwen3.5-9B-GGUF"
    assert adapter.provider_family == "openai"


def test_openai_adapter_uses_placeholder_api_key_for_local_endpoint() -> None:
    adapter = openai_adapter(
        model="gpt-test",
        base_url="http://127.0.0.1:8001",
    )

    assert adapter._effective_api_key() == "openagent-local"


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


def test_anthropic_adapter_builds_projected_messages_and_parses_structured_tool_calls() -> None:
    client = FakeInstructorClient(
        structured_result=FakeEchoToolAction(text="payload", call_id="toolu_1"),
        completion=FakeCompletion(
            {
                "content": [
                    {"type": "thinking", "thinking": "chain"},
                    {"type": "text", "text": "Let me check."},
                ],
                "usage": {"input_tokens": 10, "output_tokens": 3},
            }
        ),
    )
    adapter = anthropic_adapter(
        model="claude-test",
        base_url="http://127.0.0.1:8001",
        instructor_client=client,
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

    assert client.seen_create_kwargs["system"] == "system prompt"
    messages_payload = client.seen_create_kwargs["messages"]
    assert isinstance(messages_payload, list)
    assert messages_payload[-1] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "toolu_prev", "content": "done"}],
    }
    assert response.assistant_message is None
    assert response.tool_calls == [
        ToolCall(tool_name="echo", arguments={"text": "payload"}, call_id="toolu_1")
    ]
    assert response.usage == {"input_tokens": 10, "output_tokens": 3}


def test_provider_adapters_include_short_term_memory_summary() -> None:
    openai_client = FakeInstructorClient(structured_result=FakeRespondAction(message="ok"))
    openai_model = openai_adapter(
        model="gpt-test",
        base_url="http://127.0.0.1:8001",
        instructor_client=openai_client,
    )
    anthropic_client = FakeInstructorClient(structured_result=FakeRespondAction(message="ok"))
    anthropic_model = anthropic_adapter(
        model="claude-test",
        base_url="http://127.0.0.1:8001",
        instructor_client=anthropic_client,
    )
    request = ModelTurnRequest(
        session_id="sess_1",
        messages=[{"role": "user", "content": "continue"}],
        short_term_memory={"summary": "User wants to continue the launch checklist."},
    )

    openai_model.generate(request)
    anthropic_model.generate(request)

    openai_messages = openai_client.seen_create_kwargs["messages"]
    assert isinstance(openai_messages, list)
    assert openai_messages[0] == {
        "role": "system",
        "content": "Session continuity summary: User wants to continue the launch checklist.",
    }
    assert anthropic_client.seen_create_kwargs["system"] == (
        "Session continuity summary: User wants to continue the launch checklist."
    )


def test_provider_adapters_include_bootstrap_system_prompt() -> None:
    openai_client = FakeInstructorClient(structured_result=FakeRespondAction(message="ok"))
    openai_model = openai_adapter(
        model="gpt-test",
        base_url="http://127.0.0.1:8001",
        instructor_client=openai_client,
    )
    anthropic_client = FakeInstructorClient(structured_result=FakeRespondAction(message="ok"))
    anthropic_model = anthropic_adapter(
        model="claude-test",
        base_url="http://127.0.0.1:8001",
        instructor_client=anthropic_client,
    )
    request = ModelTurnRequest(
        session_id="sess_bootstrap",
        messages=[{"role": "user", "content": "list files"}],
        system_prompt="You are OpenAgent.\nWorkspace root: /tmp/workspace",
    )

    openai_model.generate(request)
    anthropic_model.generate(request)

    openai_messages = openai_client.seen_create_kwargs["messages"]
    assert isinstance(openai_messages, list)
    assert openai_messages[0] == {
        "role": "system",
        "content": "You are OpenAgent.\nWorkspace root: /tmp/workspace",
    }
    assert anthropic_client.seen_create_kwargs["system"] == (
        "You are OpenAgent.\nWorkspace root: /tmp/workspace"
    )


def test_openai_adapter_merges_system_planes_into_single_prefix() -> None:
    client = FakeInstructorClient(structured_result=FakeRespondAction(message="ok"))
    adapter = openai_adapter(
        model="gpt-test",
        base_url="http://127.0.0.1:8001",
        instructor_client=client,
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

    messages = client.seen_create_kwargs["messages"]
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


def test_openai_adapter_rejects_requests_without_user_message() -> None:
    adapter = openai_adapter(
        model="gpt-test",
        base_url="http://127.0.0.1:8001",
        instructor_client=FakeInstructorClient(structured_result=FakeRespondAction(message="ok")),
    )

    with pytest.raises(ProviderError) as exc:
        adapter.generate(
            ModelTurnRequest(
                session_id="sess_missing_user",
                messages=[
                    {"role": "assistant", "content": "intermediate summary"},
                    {"role": "tool", "content": "result", "metadata": {"tool_use_id": "call_1"}},
                ],
                system_prompt="You are OpenAgent.",
            )
        )

    assert str(exc.value) == "model request is missing a user message after context compaction"


def test_turn_response_model_includes_builtin_tool_argument_shapes() -> None:
    response_model = build_action_models(
        [
            {
                "name": tool.name,
                "description": tool.description(),
                "input_schema": tool.input_schema,
            }
            for tool in (GlobTool("."), BashTool("."), WebSearchTool())
        ]
    ).response_model
    schema = TypeAdapter(response_model).json_schema()
    schema_text = json.dumps(schema, ensure_ascii=False)

    assert "Respond" in schema_text
    assert "GlobAction" in schema_text
    assert "BashAction" in schema_text
    assert "WebSearchAction" in schema_text
    assert "pattern" in schema_text
    assert "command" in schema_text
    assert "query" in schema_text


def test_turn_response_model_includes_core_local_tool_argument_shapes() -> None:
    toolset = [
        FileReadTool("."),
        FileWriteTool("."),
        FileEditTool("."),
        GlobTool("."),
        GrepTool("."),
        BashTool("."),
    ]
    response_model = build_action_models(
        [
            {
                "name": tool.name,
                "description": tool.description(),
                "input_schema": tool.input_schema,
            }
            for tool in toolset
        ]
    ).response_model
    schema_text = json.dumps(TypeAdapter(response_model).json_schema(), ensure_ascii=False)

    for field_name in ("path", "offset", "content", "pattern", "output_mode", "timeout_ms"):
        assert field_name in schema_text


def test_turn_response_models_expose_object_level_action_conversion() -> None:
    action_models = build_action_models(
        [
            {
                "name": "echo",
                "description": "Echo text.",
                "input_schema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            }
        ]
    )

    respond = action_models.respond_model(message="done")
    tool_action = action_models.tool_models["echo"](text="payload")

    assert respond.to_assistant_message() == "done"
    assert tool_action.to_tool_call() == ToolCall(tool_name="echo", arguments={"text": "payload"})


def test_openai_adapter_generate_with_exchange_exposes_payload_and_raw_response() -> None:
    client = FakeInstructorClient(
        structured_result=FakeRespondAction(message="ok"),
        completion=FakeCompletion(
            {
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
        ),
    )
    adapter = openai_adapter(
        model="gpt-test",
        base_url="http://127.0.0.1:8001",
        instructor_client=client,
    )

    exchange = adapter.generate_with_exchange(
        ModelTurnRequest(
            session_id="sess_exchange",
            messages=[{"role": "user", "content": "hello"}],
        )
    )

    assert exchange.provider_payload == {
        "model": "gpt-test",
        "messages": [{"role": "user", "content": "hello"}],
    }
    assert exchange.raw_response == client.completion.body
    assert exchange.reasoning == "draft reasoning"
    assert exchange.response.assistant_message == "ok"


def test_anthropic_adapter_generate_with_exchange_exposes_reasoning_blocks() -> None:
    client = FakeInstructorClient(
        structured_result=FakeRespondAction(message="answer"),
        completion=FakeCompletion(
            {
                "content": [
                    {"type": "thinking", "thinking": "chain"},
                    {"type": "text", "text": "answer"},
                ]
            }
        ),
    )
    adapter = anthropic_adapter(
        model="claude-test",
        base_url="http://127.0.0.1:8001",
        instructor_client=client,
    )

    exchange = adapter.generate_with_exchange(
        ModelTurnRequest(
            session_id="sess_reasoning",
            messages=[{"role": "user", "content": "hello"}],
        )
    )

    assert exchange.raw_response == client.completion.body
    assert exchange.reasoning == [{"type": "thinking", "thinking": "chain"}]
    assert exchange.response.assistant_message == "answer"


def test_harness_build_model_input_includes_tool_definitions(tmp_path: Path) -> None:
    tool = EchoTool()
    harness = SimpleHarness(
        model=ToolThenReplyModel(responses=[ModelTurnResponse(assistant_message="ok")]),
        sessions=FileSessionStore(tmp_path / "sessions"),
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
        sessions=FileSessionStore(tmp_path / "sessions"),
        tools=StaticToolRegistry([tool]),
        executor=SimpleToolExecutor(StaticToolRegistry([tool])),
    )
    session = harness.sessions.load_session("sess_bootstrap")
    session.metadata["workdir"] = str(tmp_path)
    session.messages.append(harness._new_session_message(role="user", content="show files"))

    request = harness.build_model_input(session, [])

    assert request.system_prompt is not None
    assert "You are an interactive agent named OpenAgent" in request.system_prompt
    assert "# System" in request.system_prompt
    assert "# Using your tools" in request.system_prompt
    assert (
        "Only use tools that are explicitly available in the runtime capability summary."
    ) in request.system_prompt
    assert (
        "You can call multiple tools in a single response."
    ) in request.system_prompt
    assert f"Workspace root: {tmp_path.resolve()}" in request.system_prompt
    section_names = [section["name"] for section in request.prompt_sections]
    assert section_names == [
        "intro",
        "system",
        "doing_tasks",
        "actions_with_care",
        "using_your_tools",
        "tone_and_style",
        "output_efficiency",
        "workspace_context",
        "environment_summary",
    ]
    assert request.prompt_blocks is not None
    assert len(request.prompt_blocks["static_blocks"]) == 7
    assert all("Workspace root:" not in block for block in request.prompt_blocks["static_blocks"])
    assert len(request.prompt_blocks["dynamic_blocks"]) == 2
    assert any(
        f"Workspace root: {tmp_path.resolve()}" in block
        for block in request.prompt_blocks["dynamic_blocks"]
    )
    assert [item["kind"] for item in request.startup_contexts] == ["session_start", "turn_zero"]
    assert all(message["role"] != "system" for message in request.messages)


def test_bootstrap_prompt_assembler_split_static_dynamic_separates_sections() -> None:
    assembler = BootstrapPromptAssembler()
    sections = assembler.build_default_prompt(
        runtime_capabilities=["Read", "Edit", "Bash", "AskUserQuestion"],
        model_view={"workspace_root": "/tmp/demo"},
    )

    blocks = assembler.split_static_dynamic(sections)

    assert blocks.attribution_prefix == "OpenAgent bootstrap prompt"
    assert len(blocks.static_blocks) == 7
    assert len(blocks.dynamic_blocks) == 2
    assert any("# Doing tasks" in block for block in blocks.static_blocks)
    assert any("Workspace root: /tmp/demo" in block for block in blocks.dynamic_blocks)
    assert any(
        "Available runtime capabilities: Read, Edit, Bash, AskUserQuestion" in block
        for block in blocks.dynamic_blocks
    )


def test_harness_build_model_input_includes_short_term_memory(tmp_path: Path) -> None:
    harness = SimpleHarness(
        model=ToolThenReplyModel(responses=[ModelTurnResponse(assistant_message="ok")]),
        sessions=FileSessionStore(tmp_path / "sessions"),
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


@pytest.mark.parametrize(
    ("tool_name", "arguments", "prepare_workspace", "assert_result"),
    [
            (
                READ_TOOL_NAME,
                {"path": "notes.txt"},
                lambda root: (root / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8"),
                lambda payload: render_tool_result_content(payload["content"]) == "1\talpha\n2\tbeta",
            ),
        (
            WRITE_TOOL_NAME,
            {"path": "nested/out.txt", "content": "hello\n"},
            lambda root: None,
            lambda payload: str(payload["structured_content"]["path"]).endswith("nested/out.txt"),
        ),
        (
            EDIT_TOOL_NAME,
            {"path": "notes.txt", "old": "beta", "new": "gamma"},
            lambda root: (root / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8"),
            lambda payload: payload["structured_content"]["replacements"] == 1,
        ),
            (
                GLOB_TOOL_NAME,
                {"pattern": "*.py", "path": "src"},
                lambda root: (
                    (root / "src").mkdir(),
                    (root / "src" / "main.py").write_text("print('x')\n", encoding="utf-8"),
                ),
                lambda payload: render_tool_result_content(payload["content"])
                == "Found 1 matching files\nsrc/main.py",
            ),
            (
                GREP_TOOL_NAME,
                {"pattern": "needle", "path": "src"},
                lambda root: (
                    (root / "src").mkdir(),
                    (root / "src" / "main.py").write_text("needle\n", encoding="utf-8"),
                ),
                lambda payload: render_tool_result_content(payload["content"])
                == "Found 1 matching files\nsrc/main.py",
            ),
            (
                BASH_TOOL_NAME,
                {"command": "pwd"},
                lambda root: None,
                lambda payload: render_tool_result_content(payload["content"])
                == str(Path(payload["structured_content"]["cwd"])),
            ),
        ],
)
def test_harness_roundtrips_core_local_tool_calls(
    tmp_path: Path,
    tool_name: str,
    arguments: dict[str, object],
    prepare_workspace,
    assert_result,
) -> None:
    prepare_workspace(tmp_path)
    toolset = create_builtin_toolset(root=str(tmp_path))
    harness = SimpleHarness(
        model=ToolThenReplyModel(
            responses=[
                ModelTurnResponse(tool_calls=[ToolCall(tool_name=tool_name, arguments=arguments)]),
                ModelTurnResponse(assistant_message=f"{tool_name} completed"),
            ]
        ),
        sessions=FileSessionStore(tmp_path / "sessions"),
        tools=StaticToolRegistry(toolset),
        executor=SimpleToolExecutor(StaticToolRegistry(toolset)),
    )
    session = harness.sessions.load_session(f"sess_{tool_name.lower()}")
    session.metadata["workdir"] = str(tmp_path)
    harness.sessions.save_session(session.session_id, session)

    events, terminal = harness.run_turn(f"use {tool_name}", session.session_id)

    assert terminal.status.value == "completed"
    assert [event.event_type for event in events] == [
        RuntimeEventType.TURN_STARTED,
        RuntimeEventType.TOOL_STARTED,
        RuntimeEventType.TOOL_RESULT,
        RuntimeEventType.ASSISTANT_MESSAGE,
        RuntimeEventType.TURN_COMPLETED,
    ]
    tool_result_payload = events[2].payload
    assert tool_result_payload["tool_name"] == tool_name
    assert tool_result_payload["success"] is True
    assert assert_result(tool_result_payload)


def test_tool_results_preserve_tool_use_id_in_session_messages(tmp_path: Path) -> None:
    tool = EchoTool()
    registry = StaticToolRegistry([tool])
    harness = SimpleHarness(
        model=ToolThenReplyModel(
            responses=[
                ModelTurnResponse(tool_calls=[ToolCall(tool_name="echo", arguments={"text": "x"})]),
                ModelTurnResponse(assistant_message="done"),
            ]
        ),
        sessions=FileSessionStore(tmp_path / "sessions"),
        tools=registry,
        executor=SimpleToolExecutor(registry),
        session_root_dir=tmp_path / "agent_default" / "sessions",
    )

    harness.run_turn("use tool", "sess_tool_metadata")
    session = harness.sessions.load_session("sess_tool_metadata")
    tool_messages = [message for message in session.messages if message.role == "tool"]

    assert len(tool_messages) == 1
    assert tool_messages[0].metadata["tool_use_id"] == "toolu_1"
    assert isinstance(tool_messages[0].content, list)


def test_openai_adapter_downgrades_structured_tool_result_to_text() -> None:
    client = FakeInstructorClient(structured_result=FakeRespondAction(message="ok"))
    adapter = openai_adapter(
        model="gpt-test",
        base_url="http://127.0.0.1:8001",
        instructor_client=client,
    )

    adapter.generate(
        ModelTurnRequest(
            session_id="sess_tool_result_stringify",
            messages=[
                {"role": "user", "content": "search"},
                {
                    "role": "tool",
                    "content": [
                        text_block("Found 1 matching files"),
                        tool_reference_block(ref="src/main.py", title="src/main.py", preview="src/main.py"),
                    ],
                    "metadata": {"tool_use_id": "toolu_prev"},
                },
            ],
        )
    )

    messages_payload = client.seen_create_kwargs["messages"]
    assert isinstance(messages_payload, list)
    assert messages_payload[-1]["role"] == "tool"
    assert messages_payload[-1]["tool_call_id"] == "toolu_prev"
    assert isinstance(messages_payload[-1]["content"], str)
    encoded = json.loads(messages_payload[-1]["content"])
    assert encoded == {
        "tool_name": "",
        "success": True,
        "content": [
            {"type": "text", "text": "Found 1 matching files"},
            {
                "type": "tool_reference",
                "ref": "src/main.py",
                "title": "src/main.py",
                "preview": "src/main.py",
                "ref_kind": "file",
            },
        ],
        "structured_content": None,
        "truncated": False,
        "persisted_ref": None,
    }


def test_anthropic_adapter_preserves_text_and_image_tool_result_blocks() -> None:
    client = FakeInstructorClient(structured_result=FakeRespondAction(message="ok"))
    adapter = anthropic_adapter(
        model="claude-test",
        base_url="http://127.0.0.1:8001",
        instructor_client=client,
    )

    adapter.generate(
        ModelTurnRequest(
            session_id="sess_tool_result_blocks",
            messages=[
                {"role": "user", "content": "show image"},
                {
                    "role": "tool",
                    "content": [
                        text_block("Rendered preview"),
                        image_block(
                            media_type="image/png",
                            data="ZmFrZQ==",
                            alt_text="bash output image",
                        ),
                    ],
                    "metadata": {"tool_use_id": "toolu_prev"},
                },
            ],
        )
    )

    messages_payload = client.seen_create_kwargs["messages"]
    assert isinstance(messages_payload, list)
    payload = messages_payload[-1]
    assert payload["role"] == "user"
    tool_result = payload["content"][0]
    assert tool_result["type"] == "tool_result"
    assert tool_result["tool_use_id"] == "toolu_prev"
    assert tool_result["content"][0] == {"type": "text", "text": "Rendered preview"}
    assert tool_result["content"][1] == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": "ZmFrZQ==",
        },
    }


def test_openai_build_model_input_appends_tool_result_json_hint(tmp_path: Path) -> None:
    harness = SimpleHarness(
        model=openai_adapter(
            model="gpt-test",
            base_url="http://127.0.0.1:8001",
            instructor_client=FakeInstructorClient(structured_result=FakeRespondAction(message="ok")),
        ),
        sessions=FileSessionStore(tmp_path / "sessions"),
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
    )
    session = harness.sessions.load_session("sess_openai_tool_hint")
    session.messages.append(SessionMessage(role="user", content="find files"))
    session.messages.append(
        SessionMessage(
            role="tool",
            content=[text_block("Found 1 matching files")],
            metadata={"tool_use_id": "toolu_prev"},
        )
    )
    harness.sessions.save_session(session.session_id, session)

    request = harness.build_model_input(session, [])

    assert request.system_prompt is not None
    assert "OpenAI-compatible tool-result note" in request.system_prompt
    assert "JSON-formatted string" in request.system_prompt


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
        sessions=FileSessionStore(tmp_path / "sessions"),
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
        short_term_memory_store=InMemoryShortTermMemoryStore(),
        model_io_capture=FileModelIoCapture(model_io_root),
        session_root_dir=tmp_path / "agent_default" / "sessions",
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
    assert row["provider_projected_messages"] == [{"role": "user"}]
    assert row["provider_projected_messages"] == row["provider_payload"]["messages"]
    assert row["provider_response_raw"]["usage"]["prompt_tokens"] == 3
    assert row["parsed_response"]["assistant_message"] == "captured"
    assert row["reasoning"] == "deliberation"
    assert row["record_path"]
    record = json.loads(Path(row["record_path"]).read_text(encoding="utf-8"))
    assert record["capture_id"] == row["capture_id"]


def test_streaming_model_io_capture_persists_usage_and_provider_exchange(tmp_path: Path) -> None:
    model_io_root = tmp_path / "data" / "model-io"
    harness = SimpleHarness(
        model=StreamingExchangeBackedModel(
            chunks=[
                ModelStreamEvent(assistant_delta="hello "),
                ModelStreamEvent(
                    assistant_message="hello world",
                    usage={"prompt_tokens": 3, "completion_tokens": 2},
                    provider_payload={
                        "model": "gpt-test",
                        "messages": [{"role": "user", "content": "stream"}],
                        "stream": True,
                        "stream_options": {"include_usage": True},
                    },
                    raw_provider_events=[
                        {"choices": [{"delta": {"content": "hello "}}]},
                        {
                            "choices": [{"delta": {"content": "world"}}],
                            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                        },
                    ],
                    reasoning="deliberation",
                    transport_metadata={"streaming": True},
                ),
            ]
        ),
        sessions=FileSessionStore(tmp_path / "sessions"),
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
        short_term_memory_store=InMemoryShortTermMemoryStore(),
        model_io_capture=FileModelIoCapture(model_io_root),
        session_root_dir=tmp_path / "agent_default" / "sessions",
    )

    events, terminal = harness.run_turn("stream", "sess_stream_capture")

    assert terminal.reason == "assistant_message"
    assert any(event.event_type.value == "assistant_delta" for event in events)
    rows = [
        json.loads(line)
        for line in (model_io_root / "index.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["streaming"] is True
    assert row["usage"] == {"prompt_tokens": 3, "completion_tokens": 2}
    assert row["provider_projected_messages"] == [{"role": "user", "content": "stream"}]
    assert row["provider_projected_messages"] == row["provider_payload"]["messages"]
    assert row["provider_payload"]["stream_options"] == {"include_usage": True}
    assert row["provider_response_raw"] == {
        "events": [
            {"choices": [{"delta": {"content": "hello "}}]},
            {
                "choices": [{"delta": {"content": "world"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
    }
    assert row["provider_response_summary"] == row["provider_response_raw"]
    assert row["reasoning"] == "deliberation"
    assert row["stream_deltas"] == ["hello "]


def test_model_io_capture_keeps_canonical_and_provider_message_views_distinct(tmp_path: Path) -> None:
    model_io_root = tmp_path / "data" / "model-io"
    harness = SimpleHarness(
        model=ExchangeBackedModel(
            exchange=ModelProviderExchange(
                response=ModelTurnResponse(assistant_message="ok"),
                provider_payload={
                    "model": "gpt-test",
                    "messages": [
                        {"role": "system", "content": "wire system"},
                        {"role": "user", "content": "wire user"},
                    ],
                },
                raw_response={"choices": [{"message": {"content": "ok"}}]},
            )
        ),
        sessions=FileSessionStore(tmp_path / "sessions"),
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
        model_io_capture=FileModelIoCapture(model_io_root),
        session_root_dir=tmp_path / "agent_default" / "sessions",
    )

    harness.run_turn("wire user", "sess_views")

    rows = [json.loads(line) for line in (model_io_root / "index.jsonl").read_text("utf-8").splitlines()]
    row = rows[0]
    assert row["assembled_request"]["messages"] == [{"role": "user", "content": "wire user"}]
    assert row["provider_projected_messages"] == [
        {"role": "system", "content": "wire system"},
        {"role": "user", "content": "wire user"},
    ]
    assert row["assembled_request"]["messages"] != row["provider_projected_messages"]
