from pathlib import Path

from openagent.object_model import JsonObject, ToolResult
from openagent.tools import (
    CommandKind,
    CommandVisibility,
    DiscoveredSkillRef,
    FileSkillRegistry,
    ImportedSkillManifest,
    InMemoryMcpClient,
    InMemoryMcpTransport,
    McpPromptAdapter,
    McpPromptDescriptor,
    McpResourceDescriptor,
    McpServerConnection,
    McpServerDescriptor,
    McpSkillAdapter,
    McpToolDescriptor,
    SkillActivationResult,
    SkillActivator,
    SkillContextManager,
    SkillDiscoveryRoot,
    SkillInvocationBridge,
    StaticCommandRegistry,
    TransportBackedMcpClient,
)


def _echo_tool(args: JsonObject) -> ToolResult:
    return ToolResult(tool_name="echo", success=True, content=[str(args["text"])])


def test_file_skill_registry_and_bridge(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "summarize"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: Summarize\n"
        "description: Summarize content for a target audience.\n"
        "allowed-tools: [Read, Grep]\n"
        "arguments:\n"
        "  - audience\n"
        "when_to_use: Use when the user wants a concise recap.\n"
        "user-invocable: true\n"
        "metadata:\n"
        "  owner: docs\n"
        "---\n"
        "# Summarize\n\nSummarize the input for {audience}.\n\nUse source: {source}\n",
        encoding="utf-8",
    )
    (skill_dir / "scripts").mkdir()
    (skill_dir / "references").mkdir()

    registry = FileSkillRegistry([tmp_path / "skills"])
    context_manager = SkillContextManager()
    activator = SkillActivator(context_manager=context_manager)
    bridge = SkillInvocationBridge(registry, activator)

    skills = registry.discover_skills()
    assert len(skills) == 1
    assert skills[0].id == "summarize"
    assert skills[0].arguments == ["audience"]
    assert skills[0].allowed_tools == ["Read", "Grep"]
    assert skills[0].when_to_use == "Use when the user wants a concise recap."
    assert skills[0].invocable_by_user is True
    assert skills[0].frontmatter_mode == "stripped"
    assert skills[0].listed_resources == ["scripts", "references"]
    assert isinstance(skills[0].imported_manifest, ImportedSkillManifest)
    assert isinstance(skills[0].discovered_ref, DiscoveredSkillRef)

    commands = bridge.list_model_invocable_skills()
    assert commands[0].kind is CommandKind.PROMPT
    assert commands[0].visibility is CommandVisibility.MODEL
    assert commands[0].metadata["listed_resources"] == ["scripts", "references"]

    rendered = bridge.invoke_skill(
        "summarize",
        args={"audience": "engineers"},
        runtime_context={"source": "release notes"},
    )
    assert "engineers" in rendered
    assert "release notes" in rendered

    catalog = registry.list_catalog_entries()
    activation = bridge.invoke_skill_wrapped(
        "summarize",
        args={"audience": "engineers"},
        runtime_context={"source": "release notes"},
    )

    assert catalog[0].name == "Summarize"
    assert catalog[0].description == "Summarize content for a target audience."
    assert isinstance(activation, SkillActivationResult)
    assert activation.skill_name == "Summarize"
    assert activation.wrapped is True
    assert activation.activation_mode == "model"
    assert activation.frontmatter_mode == "stripped"
    assert activation.listed_resources == ["scripts", "references"]
    assert activation.metadata["already_active"] is False
    assert activation.metadata["compaction_protected"] is True
    assert context_manager.is_already_active("summarize") is True
    assert context_manager.list_bound_resources("summarize") == ["scripts", "references"]

    repeated = bridge.invoke_skill_wrapped(
        "summarize",
        args={"audience": "engineers"},
        runtime_context={"source": "release notes"},
    )
    assert repeated.metadata["already_active"] is True


def test_skill_registry_resolves_precedence_and_emits_shadow_diagnostics(tmp_path: Path) -> None:
    user_root = tmp_path / "user" / "skills" / "summarize"
    project_root = tmp_path / "project" / "skills" / "summarize"
    user_root.mkdir(parents=True)
    project_root.mkdir(parents=True)
    (user_root / "SKILL.md").write_text("# Summarize\n\nUser version.\n", encoding="utf-8")
    (project_root / "SKILL.md").write_text("# Summarize\n\nProject version.\n", encoding="utf-8")

    registry = FileSkillRegistry(
        [
            SkillDiscoveryRoot(path=str(user_root.parent), scope="user"),
            SkillDiscoveryRoot(path=str(project_root.parent), scope="project"),
        ]
    )

    skills = registry.discover_skills()

    assert len(skills) == 1
    assert skills[0].scope == "project"
    assert "shadowed skill 'summarize' from user:" in skills[0].diagnostics[0]


def test_skill_registry_parses_lenient_frontmatter_and_filters_untrusted_model_invocation(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "skills" / "triage"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: Triage\n"
        "\tdescription: Triage an issue quickly.\n"
        "disable-model-invocation: true\n"
        "paths:\n"
        "  - src\n"
        "  - tests\n"
        "shell: bash\n"
        "---\n"
        "Triage {issue}\n",
        encoding="utf-8",
    )

    registry = FileSkillRegistry(
        [
            SkillDiscoveryRoot(
                path=str(tmp_path / "skills"),
                scope="project",
                trust_level="untrusted",
            )
        ]
    )

    skills = registry.discover_skills()

    assert len(skills) == 1
    assert skills[0].invocable_by_model is False
    assert skills[0].host_extensions["paths"] == ["src", "tests"]
    assert skills[0].host_extensions["shell"] == "bash"
    assert any("frontmatter_lenient_retry" in item for item in skills[0].diagnostics)
    assert any("trust_blocked" in item for item in skills[0].diagnostics)
    assert registry.list_catalog_entries(audience="model") == []
    assert len(registry.list_catalog_entries(audience="user")) == 1


def test_skill_context_manager_tracks_activation_and_bound_resources() -> None:
    manager = SkillContextManager()
    binding = manager.mark_activated(
        "summarize",
        "summarize:model",
        skill_root="/tmp/skills/summarize",
        listed_resources=["scripts", "assets"],
    )

    assert binding.skill_name == "summarize"
    assert manager.is_already_active("summarize") is True
    assert manager.list_bound_resources("summarize") == ["scripts", "assets"]

    manager.protect_from_compaction("summarize:model")
    assert binding.protected_from_compaction is True


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


def test_transport_backed_mcp_client_uses_transport_seam() -> None:
    transport = InMemoryMcpTransport()
    transport.connect(
        McpServerConnection(
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
                )
            },
        )
    )
    client = TransportBackedMcpClient(transport)

    result = client.call_tool("docs", "echo", {"text": "hello"})
    rendered_prompt = client.get_prompt("docs", "review", {"topic": "api"})
    resource = client.read_resource("docs", "skill://summarize")

    assert result.content == ["hello"]
    assert rendered_prompt == "Review api"
    assert resource.uri == "skill://summarize"
