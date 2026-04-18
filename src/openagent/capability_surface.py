"""Unified capability surface and host projection helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from openagent.object_model import JsonObject, SerializableModel
from openagent.tools import Command, SkillDefinition, ToolDefinition


class CapabilityOriginType(StrEnum):
    BUILTIN = "builtin"
    BUNDLED = "bundled"
    PLUGIN = "plugin"
    USER = "user"
    PROJECT = "project"
    MANAGED = "managed"
    MCP = "mcp"
    REMOTE = "remote"


@dataclass(slots=True)
class CapabilityOrigin(SerializableModel):
    origin_type: CapabilityOriginType
    package_id: str | None = None
    provider_id: str | None = None
    installation_scope: str | None = None


@dataclass(slots=True)
class InvocableEntry(SerializableModel):
    entry_id: str
    entry_type: str
    display_name: str
    description: str
    source_origin: JsonObject
    invocation_mode: str
    visible_to_model: bool = True
    visible_to_user: bool = True
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class CapabilityDescriptor(SerializableModel):
    capability_id: str
    capability_type: str
    display_name: str
    description: str
    invocation_mode: str
    origin: JsonObject
    visible_to_model: bool = True
    visible_to_user: bool = True
    metadata: JsonObject = field(default_factory=dict)


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
                    description=tool.description(),
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
        descriptors = self._apply_filters(self.describe_capabilities(), filters)
        return [descriptor.capability_id for descriptor in descriptors]

    def list_command_surface(
        self,
        scope: str = "default",
        filters: JsonObject | None = None,
    ) -> list[InvocableEntry]:
        del scope
        entries: list[InvocableEntry] = []
        for descriptor in self._apply_filters(self.describe_capabilities(), filters):
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
        host_descriptors = self._project_descriptors_for_host(descriptors, host_profile)
        return {
            "host_profile": host_profile,
            "capability_count": len(host_descriptors),
            "capabilities": [descriptor.to_dict() for descriptor in host_descriptors],
            "command_surface": [
                entry.to_dict()
                for entry in self.list_command_surface(filters={"host_profile": host_profile})
            ],
        }

    def _apply_filters(
        self,
        descriptors: list[CapabilityDescriptor],
        filters: JsonObject | None,
    ) -> list[CapabilityDescriptor]:
        if filters is None:
            return descriptors

        filtered = descriptors
        capability_type = filters.get("capability_type")
        if isinstance(capability_type, str):
            filtered = [
                descriptor
                for descriptor in filtered
                if descriptor.capability_type == capability_type
            ]

        host_profile = filters.get("host_profile")
        if isinstance(host_profile, str):
            filtered = self._project_descriptors_for_host(filtered, host_profile)

        visibility = filters.get("visibility")
        if visibility == "model":
            filtered = [descriptor for descriptor in filtered if descriptor.visible_to_model]
        if visibility == "user":
            filtered = [descriptor for descriptor in filtered if descriptor.visible_to_user]

        origin_type = filters.get("origin_type")
        if isinstance(origin_type, str):
            filtered = [
                descriptor
                for descriptor in filtered
                if descriptor.origin.get("origin_type") == origin_type
            ]
        return filtered

    def _project_descriptors_for_host(
        self,
        descriptors: list[CapabilityDescriptor],
        host_profile: str,
    ) -> list[CapabilityDescriptor]:
        if host_profile in {"local", "terminal"}:
            return descriptors

        if host_profile == "feishu":
            return [
                descriptor
                for descriptor in descriptors
                if descriptor.metadata.get("kind") != "local_ui"
            ]

        if host_profile == "cloud":
            return [
                descriptor
                for descriptor in descriptors
                if descriptor.metadata.get("kind") not in {"local_ui", "local"}
            ]

        return descriptors
