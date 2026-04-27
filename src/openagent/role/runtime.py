"""Runtime assembly helpers for role-owned assets."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from openagent.harness.context_engineering.assembly.models import CapabilityExposure
from openagent.harness.context_engineering.assembly.providers import DefaultContextFragmentProvider
from openagent.role.models import RoleDefinition
from openagent.tools import (
    FileSkillRegistry,
    SkillActivator,
    SkillContextManager,
    SkillDiscoveryRoot,
    SkillInvocationBridge,
)
from openagent.tools.mcp import MountedMcpServer, mount_role_mcp_tools


@dataclass(slots=True)
class ResolvedRoleRuntime:
    role: RoleDefinition
    skill_bridge: SkillInvocationBridge | None = None
    mounted_mcp_servers: list[MountedMcpServer] = field(default_factory=list)
    mounted_mcp_tools: list[object] = field(default_factory=list)

    def as_context_provider(self) -> DefaultContextFragmentProvider:
        always_loaded = [ref.skill_id for ref in self.role.capabilities.skills]
        always_loaded.extend(f"mcp:{server.server_id}" for server in self.mounted_mcp_servers)
        always_loaded.extend(
            f"model:{model}" for model in self.role.capabilities.recommended_models
        )
        return DefaultContextFragmentProvider(
            system_fragments=[],
            user_fragments=[],
            attachment_fragments=[],
            capability_surface=CapabilityExposure(always_loaded=always_loaded),
            evidence_fragments=[],
        )


def resolve_role_runtime(
    *,
    role: RoleDefinition,
    plugins_root: str,
) -> ResolvedRoleRuntime:
    skill_bridge = _resolve_skill_bridge(role, plugins_root)
    mounted_mcp_tools, mounted_mcp_servers = mount_role_mcp_tools(
        server_ids=[ref.server_id for ref in role.capabilities.mcps],
        plugins_root=plugins_root,
    ) if role.capabilities.mcps else ([], [])
    return ResolvedRoleRuntime(
        role=role,
        skill_bridge=skill_bridge,
        mounted_mcp_servers=mounted_mcp_servers,
        mounted_mcp_tools=mounted_mcp_tools,
    )


def _resolve_skill_bridge(
    role: RoleDefinition,
    plugins_root: str,
) -> SkillInvocationBridge | None:
    if not role.capabilities.skills:
        return None
    registry = FileSkillRegistry(
        [SkillDiscoveryRoot(path=str(Path(plugins_root)), scope="plugin", source="plugin")]
    )
    allowed_skill_ids = {ref.skill_id for ref in role.capabilities.skills}
    discovered = {skill.id for skill in registry.discover_skills()}
    missing = sorted(
        ref.skill_id for ref in role.capabilities.skills if ref.skill_id not in discovered
    )
    if missing:
        raise FileNotFoundError(
            "Role skill refs could not be resolved from agent plugins root: "
            + ", ".join(missing)
        )
    filtered_registry = _RoleSkillRegistry(registry, allowed_skill_ids)
    return SkillInvocationBridge(
        registry=filtered_registry,
        activator=SkillActivator(context_manager=SkillContextManager()),
    )


@dataclass(slots=True)
class _RoleSkillRegistry:
    registry: FileSkillRegistry
    allowed_skill_ids: set[str]

    def discover_skills(self, scope: str = "default") -> list[object]:
        return [
            skill
            for skill in self.registry.discover_skills(scope)
            if skill.id in self.allowed_skill_ids
        ]

    def load_skill(self, skill_id: str) -> object:
        if skill_id not in self.allowed_skill_ids:
            raise KeyError(f"Role skill is not mounted: {skill_id}")
        return self.registry.load_skill(skill_id)
