"""Role domain models."""

from __future__ import annotations

from dataclasses import dataclass, field

from openagent.object_model import JsonObject, SerializableModel


@dataclass(slots=True)
class RoleSkillRef(SerializableModel):
    skill_id: str
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class RoleMcpRef(SerializableModel):
    server_id: str
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class RoleCapabilityRefs(SerializableModel):
    recommended_models: list[str] = field(default_factory=list)
    skills: list[RoleSkillRef] = field(default_factory=list)
    mcps: list[RoleMcpRef] = field(default_factory=list)


@dataclass(slots=True)
class RoleMemoryBinding(SerializableModel):
    root: str
    records_root: str
    user_memory_path: str
    topic_memory_path: str


@dataclass(slots=True)
class RoleDefinition(SerializableModel):
    role_id: str
    role_root: str
    role_markdown_path: str
    user_markdown_path: str
    role_markdown_body: str = ""
    user_markdown_body: str = ""
    capabilities: RoleCapabilityRefs = field(default_factory=RoleCapabilityRefs)
    memory: RoleMemoryBinding | None = None
    metadata: JsonObject = field(default_factory=dict)
