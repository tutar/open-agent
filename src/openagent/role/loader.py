"""Role loading and fallback behavior."""

from __future__ import annotations

from pathlib import Path

from openagent.object_model import JsonObject
from openagent.role.frontmatter import parse_markdown_frontmatter
from openagent.role.models import (
    RoleCapabilityRefs,
    RoleDefinition,
    RoleMcpRef,
    RoleMemoryBinding,
    RoleSkillRef,
)
from openagent.shared import DEFAULT_ROLE_ID, resolve_role_root


def load_role_definition(openagent_root: str, role_id: str | None = None) -> RoleDefinition:
    resolved_role_id = (
        role_id.strip()
        if isinstance(role_id, str) and role_id.strip()
        else DEFAULT_ROLE_ID
    )
    role_root = resolve_role_root(openagent_root, resolved_role_id)
    role_markdown_path = role_root / "ROLE.md"
    user_markdown_path = role_root / "USER.md"
    if not role_markdown_path.exists() and not user_markdown_path.exists():
        if resolved_role_id == DEFAULT_ROLE_ID:
            return _default_role_definition(role_root)
        raise FileNotFoundError(f"Role is not defined: {resolved_role_id}")
    role_markdown_text = (
        role_markdown_path.read_text(encoding="utf-8") if role_markdown_path.exists() else ""
    )
    frontmatter, role_body = parse_markdown_frontmatter(role_markdown_text)
    user_body = (
        user_markdown_path.read_text(encoding="utf-8") if user_markdown_path.exists() else ""
    )
    if not user_body and resolved_role_id != DEFAULT_ROLE_ID:
        raise FileNotFoundError(f"Role USER.md is missing: {resolved_role_id}")
    declared_role_id = frontmatter.get("role_id")
    if isinstance(declared_role_id, str) and declared_role_id.strip():
        resolved_role_id = declared_role_id.strip()
    memory_root = role_root / "memory"
    return RoleDefinition(
        role_id=resolved_role_id,
        role_root=str(role_root),
        role_markdown_path=str(role_markdown_path),
        user_markdown_path=str(user_markdown_path),
        role_markdown_body=role_body,
        user_markdown_body=user_body,
        capabilities=RoleCapabilityRefs(
            recommended_models=_string_list(frontmatter.get("recommended_models")),
            skills=_skill_refs(frontmatter.get("skills")),
            mcps=_mcp_refs(frontmatter.get("mcps")),
        ),
        memory=RoleMemoryBinding(
            root=str(memory_root),
            records_root=str(memory_root / "records"),
            user_memory_path=str(memory_root / "MEMORY.md"),
            topic_memory_path=str(memory_root / "TOPIC.md"),
        ),
        metadata={
            key: value
            for key, value in frontmatter.items()
            if key not in {"role_id", "recommended_models", "skills", "mcps"}
        },
    )


def load_default_role_definition(openagent_root: str) -> RoleDefinition:
    return load_role_definition(openagent_root, DEFAULT_ROLE_ID)


def _default_role_definition(role_root: Path) -> RoleDefinition:
    memory_root = role_root / "memory"
    return RoleDefinition(
        role_id=DEFAULT_ROLE_ID,
        role_root=str(role_root),
        role_markdown_path=str(role_root / "ROLE.md"),
        user_markdown_path=str(role_root / "USER.md"),
        capabilities=RoleCapabilityRefs(),
        memory=RoleMemoryBinding(
            root=str(memory_root),
            records_root=str(memory_root / "records"),
            user_memory_path=str(memory_root / "MEMORY.md"),
            topic_memory_path=str(memory_root / "TOPIC.md"),
        ),
    )


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _skill_refs(value: object) -> list[RoleSkillRef]:
    refs: list[RoleSkillRef] = []
    if not isinstance(value, list):
        return refs
    for item in value:
        if isinstance(item, str) and item.strip():
            refs.append(RoleSkillRef(skill_id=item.strip()))
            continue
        if isinstance(item, dict):
            skill_id = item.get("skill_id") or item.get("id") or item.get("name")
            if isinstance(skill_id, str) and skill_id.strip():
                metadata: JsonObject = {
                    str(key): item_value
                    for key, item_value in item.items()
                    if key not in {"skill_id", "id", "name"}
                }
                refs.append(RoleSkillRef(skill_id=skill_id.strip(), metadata=metadata))
    return refs


def _mcp_refs(value: object) -> list[RoleMcpRef]:
    refs: list[RoleMcpRef] = []
    if not isinstance(value, list):
        return refs
    for item in value:
        if isinstance(item, str) and item.strip():
            refs.append(RoleMcpRef(server_id=item.strip()))
            continue
        if isinstance(item, dict):
            server_id = item.get("server_id") or item.get("id") or item.get("name")
            if isinstance(server_id, str) and server_id.strip():
                metadata: JsonObject = {
                    str(key): item_value
                    for key, item_value in item.items()
                    if key not in {"server_id", "id", "name"}
                }
                refs.append(RoleMcpRef(server_id=server_id.strip(), metadata=metadata))
    return refs
