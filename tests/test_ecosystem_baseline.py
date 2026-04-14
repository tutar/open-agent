from pathlib import Path

from openagent.object_model import JsonObject, ToolResult
from openagent.tools import (
    CommandKind,
    CommandVisibility,
    FileSkillRegistry,
    InMemoryMcpClient,
    McpPromptAdapter,
    McpPromptDescriptor,
    McpResourceDescriptor,
    McpServerConnection,
    McpServerDescriptor,
    McpSkillAdapter,
    McpToolDescriptor,
    SkillActivator,
    SkillInvocationBridge,
    StaticCommandRegistry,
)


def _echo_tool(args: JsonObject) -> ToolResult:
    return ToolResult(tool_name="echo", success=True, content=[str(args["text"])])


def test_file_skill_registry_and_bridge(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "summarize"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "# Summarize\n\nSummarize the input for {audience}.\n\nUse source: {source}\n",
        encoding="utf-8",
    )

    registry = FileSkillRegistry([tmp_path / "skills"])
    activator = SkillActivator()
    bridge = SkillInvocationBridge(registry, activator)

    skills = registry.discover_skills()
    assert len(skills) == 1
    assert skills[0].id == "summarize"
    assert skills[0].arguments == ["audience", "source"]

    commands = bridge.list_model_invocable_skills()
    assert commands[0].kind is CommandKind.PROMPT
    assert commands[0].visibility is CommandVisibility.MODEL

    rendered = bridge.invoke_skill(
        "summarize",
        args={"audience": "engineers"},
        runtime_context={"source": "release notes"},
    )
    assert "engineers" in rendered
    assert "release notes" in rendered


def test_static_command_registry() -> None:
    registry = StaticCommandRegistry()
    prompt = McpPromptAdapter().adapt_mcp_prompt(
        "server_a",
        McpPromptDescriptor(name="greet", description="Greeting prompt", template="Hello {name}"),
    )
    registry.register(prompt, lambda args: f"Hello {args['name']}")

    assert registry.resolve_command(prompt.id).source == "mcp_prompt"
    assert registry.invoke_command(prompt.id, {"name": "Ada"}) == "Hello Ada"


def test_in_memory_mcp_client_and_adapters() -> None:
    client = InMemoryMcpClient()
    connection = McpServerConnection(
        descriptor=McpServerDescriptor(server_id="docs", label="Docs Server"),
        tools={
            "echo": (
                McpToolDescriptor(name="echo", description="Echo text"),
                _echo_tool,
            )
        },
        prompts={
            "review": McpPromptDescriptor(
                name="review",
                description="Review a document",
                template="Review {topic}",
            )
        },
        resources={
            "skill://summarize": McpResourceDescriptor(
                uri="skill://summarize",
                name="Summarize",
                description="Summarize a document",
                content="Summarize {topic}",
            ),
            "file://plain": McpResourceDescriptor(
                uri="file://plain",
                name="Plain File",
                description="A plain text resource",
                content="raw",
            ),
        },
    )
    client.connect(connection)

    tools = client.list_tools("docs")
    prompts = client.list_prompts("docs")
    resources = client.list_resources("docs")
    result = client.call_tool("docs", "echo", {"text": "hello"})
    rendered_prompt = client.get_prompt("docs", "review", {"topic": "api"})

    assert tools[0].name == "echo"
    assert prompts[0].name == "review"
    assert len(resources) == 2
    assert result.content == ["hello"]
    assert rendered_prompt == "Review api"

    prompt_command = McpPromptAdapter().adapt_mcp_prompt("docs", prompts[0])
    assert prompt_command.id == "mcp__docs__review"

    skill_adapter = McpSkillAdapter()
    discovered_skills = skill_adapter.discover_skills_from_resources("docs", resources)
    adapted_skill = skill_adapter.adapt_mcp_skill("docs", discovered_skills[0])
    assert adapted_skill.id == "summarize"
    assert adapted_skill.metadata["server_id"] == "docs"
