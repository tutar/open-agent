"""High-level capability surface facade."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openagent.capability_surface.models import (
    CapabilityDescriptor,
    CapabilityOrigin,
    InvocableEntry,
)
from openagent.capability_surface.projection import (
    apply_capability_filters,
    project_descriptors_for_host,
)
from openagent.object_model import JsonObject
from openagent.tools.compat import tool_description
from openagent.tools import Command, SkillDefinition, ToolDefinition


@dataclass(slots=True)
class CapabilitySurface:
    tools: list[tuple[ToolDefinition, CapabilityOrigin]] = field(default_factory=list)
    commands: list[tuple[Command, CapabilityOrigin]] = field(default_factory=list)
    skills: list[tuple[SkillDefinition, CapabilityOrigin]] = field(default_factory=list)

    def describe_capabilities(self) -> list[CapabilityDescriptor]:
        descriptors: list[CapabilityDescriptor] = []
        for tool, origin in self.tools:
            aliases = getattr(tool, "aliases", [])
            descriptors.append(
                CapabilityDescriptor(
                    capability_id=tool.name,
                    capability_type="tool",
                    display_name=tool.name,
                    description=tool_description(tool),
                    invocation_mode="tool",
                    origin=origin.to_dict(),
                    metadata={
                        "input_schema": dict(tool.input_schema),
                        "aliases": list(aliases) if isinstance(aliases, list) else [],
                    },
                )
            )
        for command, origin in self.commands:
            visible_to_model = command.visibility.value in {"model", "both"}
            visible_to_user = command.visibility.value in {"user", "both"}
            descriptors.append(
                CapabilityDescriptor(
                    capability_id=command.id,
                    capability_type="command",
                    display_name=command.name,
                    description=command.description,
                    invocation_mode=command.visibility.value,
                    origin=origin.to_dict(),
                    visible_to_model=visible_to_model,
                    visible_to_user=visible_to_user,
                    metadata={
                        "kind": command.kind.value,
                        "source": command.source,
                        **command.metadata,
                    },
                )
            )
        for skill, origin in self.skills:
            descriptors.append(
                CapabilityDescriptor(
                    capability_id=skill.id,
                    capability_type="skill",
                    display_name=skill.name,
                    description=skill.description,
                    invocation_mode="model",
                    origin=origin.to_dict(),
                    metadata={
                        "arguments": list(skill.arguments),
                        "allowed_tools": list(skill.allowed_tools),
                        "scope": skill.scope,
                        "trust_level": skill.trust_level,
                        "source": skill.source,
                        "disclosure": skill.disclosure,
                        "activation_mode": skill.activation_mode,
                        "skill_root": skill.skill_root,
                        "skill_file": skill.skill_file,
                        "frontmatter_mode": skill.frontmatter_mode,
                        "listed_resources": list(skill.listed_resources),
                        "diagnostics": list(skill.diagnostics),
                        "invocable_by_model": skill.invocable_by_model,
                        "invocable_by_user": skill.invocable_by_user,
                        "host_extensions": dict(skill.host_extensions),
                        **skill.metadata,
                    },
                )
            )
        return descriptors

    def list_capabilities(
        self,
        scope: str = "default",
        filters: JsonObject | None = None,
    ) -> list[str]:
        del scope
        descriptors = apply_capability_filters(self.describe_capabilities(), filters)
        return [descriptor.capability_id for descriptor in descriptors]

    def list_command_surface(
        self,
        scope: str = "default",
        filters: JsonObject | None = None,
    ) -> list[InvocableEntry]:
        del scope
        entries: list[InvocableEntry] = []
        for descriptor in apply_capability_filters(self.describe_capabilities(), filters):
            if descriptor.capability_type not in {"command", "skill"}:
                continue
            entries.append(
                InvocableEntry(
                    entry_id=descriptor.capability_id,
                    entry_type=descriptor.capability_type,
                    display_name=descriptor.display_name,
                    description=descriptor.description,
                    source_origin=descriptor.origin,
                    invocation_mode=descriptor.invocation_mode,
                    visible_to_model=descriptor.visible_to_model,
                    visible_to_user=descriptor.visible_to_user,
                    metadata=descriptor.metadata,
                )
            )
        return entries

    def resolve_capability(self, capability_id: str) -> Any:
        for tool, _ in self.tools:
            if tool.name == capability_id:
                return tool
        for command, _ in self.commands:
            if command.id == capability_id:
                return command
        for skill, _ in self.skills:
            if skill.id == capability_id:
                return skill
        raise KeyError(f"Unknown capability: {capability_id}")

    def project_for_host(self, host_profile: str) -> JsonObject:
        descriptors = self.describe_capabilities()
        host_descriptors = project_descriptors_for_host(descriptors, host_profile)
        return {
            "host_profile": host_profile,
            "capability_count": len(host_descriptors),
            "capabilities": [descriptor.to_dict() for descriptor in host_descriptors],
            "command_surface": [
                entry.to_dict()
                for entry in self.list_command_surface(filters={"host_profile": host_profile})
            ],
        }
