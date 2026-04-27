"""Bootstrap prompt assembly for the OpenAgent harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from openagent.object_model import JsonObject, SerializableModel


@dataclass(slots=True)
class PromptSection(SerializableModel):
    name: str
    text: str
    cache_policy: str = "cacheable"
    cache_breaking: bool = False
    dynamic: bool = False


@dataclass(slots=True)
class ResolvedPromptSections(SerializableModel):
    sections: list[PromptSection] = field(default_factory=list)


@dataclass(slots=True)
class PromptBlocks(SerializableModel):
    static_blocks: list[str] = field(default_factory=list)
    dynamic_blocks: list[str] = field(default_factory=list)
    attribution_prefix: str | None = None


@dataclass(slots=True)
class BootstrapPromptAssembler:
    agent_name: str = "OpenAgent"

    def build_default_prompt(
        self,
        *,
        runtime_capabilities: list[str],
        model_view: JsonObject,
    ) -> ResolvedPromptSections:
        workspace_root = str(model_view.get("workspace_root", "."))
        sections = [
            PromptSection(
                name="default_behavior",
                text=(
                    f"You are {self.agent_name}, a local-first task execution agent. "
                    "You are not a generic chatbot. "
                    "You should complete tasks by reasoning over the workspace, "
                    "available tools, and prior conversation state."
                ),
            ),
            PromptSection(
                name="agent_identity",
                text=(
                    f"Identity: {self.agent_name}. "
                    "Role: pragmatic software agent operating inside a real local workspace."
                ),
            ),
            PromptSection(
                name="operating_mode",
                text=(
                    "Operating mode: local-first, tool-using, grounded in the current workspace. "
                    "Do not pretend a tool was executed if it was not. "
                    "If a tool is needed, call it."
                ),
            ),
            PromptSection(
                name="workspace_context",
                text=(
                    f"Workspace root: {workspace_root}. "
                    "Restrict file assumptions and file operations to this workspace "
                    "unless the user explicitly broadens the scope."
                ),
                dynamic=True,
                cache_policy="volatile",
                cache_breaking=True,
            ),
            PromptSection(
                name="tool_usage_contract",
                text=(
                    "Tool contract: choose the most appropriate tool for the task and always "
                    "provide complete required arguments. "
                    "Never emit an empty tool call or omit required fields."
                ),
            ),
            PromptSection(
                name="environment_summary",
                text=(
                    "Available runtime capabilities: " + ", ".join(runtime_capabilities)
                    if runtime_capabilities
                    else "Available runtime capabilities: none declared."
                ),
                dynamic=True,
                cache_policy="volatile",
            ),
        ]
        return ResolvedPromptSections(sections=sections)

    def merge_prompt_layers(
        self,
        base: ResolvedPromptSections,
        overrides: list[PromptSection] | None = None,
    ) -> ResolvedPromptSections:
        if not overrides:
            return base
        merged: dict[str, PromptSection] = {section.name: section for section in base.sections}
        ordered = [section.name for section in base.sections]
        for section in overrides:
            merged[section.name] = section
            if section.name not in ordered:
                ordered.append(section.name)
        return ResolvedPromptSections(sections=[merged[name] for name in ordered])

    def resolve_sections(
        self,
        sections: ResolvedPromptSections,
        runtime_state: JsonObject | None = None,
    ) -> ResolvedPromptSections:
        del runtime_state
        return sections

    def split_static_dynamic(self, sections: ResolvedPromptSections) -> PromptBlocks:
        static_blocks: list[str] = []
        dynamic_blocks: list[str] = []
        for section in sections.sections:
            if not section.text:
                continue
            if section.dynamic:
                dynamic_blocks.append(section.text)
            else:
                static_blocks.append(section.text)
        return PromptBlocks(
            attribution_prefix=f"{self.agent_name} bootstrap prompt",
            static_blocks=static_blocks,
            dynamic_blocks=dynamic_blocks,
        )

    def invalidate_sections(self, reason: str) -> None:
        del reason


def default_workspace_root_from_metadata(metadata: JsonObject | None) -> str:
    if isinstance(metadata, dict):
        workdir = metadata.get("workdir")
        if isinstance(workdir, str) and workdir:
            return str(Path(workdir).resolve())
    return str(Path.cwd())
