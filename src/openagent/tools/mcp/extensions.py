"""Host-specific MCP extensions."""

from __future__ import annotations

from openagent.tools.mcp.models import McpResourceDescriptor
from openagent.tools.skills import SkillDefinition


class McpSkillAdapter:
    def discover_skills_from_resources(
        self,
        server_id: str,
        resources: list[McpResourceDescriptor],
    ) -> list[SkillDefinition]:
        del server_id
        skills: list[SkillDefinition] = []
        for resource in resources:
            if not resource.uri.startswith("skill://"):
                continue
            skill_id = resource.uri.removeprefix("skill://")
            skills.append(
                SkillDefinition(
                    id=skill_id,
                    name=resource.name,
                    description=resource.description,
                    content=resource.content,
                    source="mcp",
                    metadata={"source_uri": resource.uri, "loaded_from": "mcp"},
                )
            )
        return skills

    def adapt_mcp_skill(self, server_id: str, remote_skill: SkillDefinition) -> SkillDefinition:
        remote_skill.metadata["server_id"] = server_id
        remote_skill.metadata["host_extension"] = "mcp_skill"
        return remote_skill
