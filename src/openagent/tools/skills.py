"""Skill registry and activation baseline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from string import Formatter

from openagent.object_model import JsonObject, JsonValue, SerializableModel
from openagent.tools.commands import Command, CommandKind, CommandVisibility


@dataclass(slots=True)
class SkillDefinition(SerializableModel):
    id: str
    name: str
    description: str
    content: str
    arguments: list[str] = field(default_factory=list)
    when_to_use: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    metadata: JsonObject = field(default_factory=dict)


class FileSkillRegistry:
    """Discover skills from `SKILL.md` directories."""

    def __init__(self, roots: list[str | Path]) -> None:
        self._roots = [Path(root) for root in roots]
        self._cache: dict[str, SkillDefinition] = {}

    def discover_skills(self, scope: str = "default") -> list[SkillDefinition]:
        del scope
        discovered: dict[str, SkillDefinition] = {}
        for root in self._roots:
            if not root.exists():
                continue
            for skill_file in root.rglob("SKILL.md"):
                skill = self._load_from_file(skill_file)
                discovered[skill.id] = skill
        self._cache = discovered
        return list(discovered.values())

    def load_skill(self, skill_id: str) -> SkillDefinition:
        if skill_id not in self._cache:
            self.discover_skills()
        return self._cache[skill_id]

    def invalidate_skills(self, scope: str = "default") -> None:
        del scope
        self._cache = {}

    def _load_from_file(self, skill_file: Path) -> SkillDefinition:
        raw = skill_file.read_text(encoding="utf-8")
        lines = raw.splitlines()
        title = skill_file.parent.name
        description = ""
        if lines and lines[0].startswith("# "):
            title = lines[0][2:].strip()
        for line in lines[1:]:
            stripped = line.strip()
            if stripped:
                description = stripped
                break
        arguments = sorted(
            {
                field_name
                for _, field_name, _, _ in Formatter().parse(raw)
                if field_name is not None and field_name.isidentifier()
            }
        )
        return SkillDefinition(
            id=skill_file.parent.name,
            name=title,
            description=description or f"Skill loaded from {skill_file.parent.name}",
            content=raw,
            arguments=arguments,
            metadata={"source_path": str(skill_file)},
        )


class SkillActivator:
    """Render skill content with explicit args and context."""

    def activate_skill(
        self,
        skill_id: str,
        args: JsonObject,
        context: JsonObject,
        registry: FileSkillRegistry,
    ) -> SkillDefinition:
        del args, context
        return registry.load_skill(skill_id)

    def render_skill_prompt(
        self,
        skill_id: str,
        args: JsonObject,
        context: JsonObject,
        registry: FileSkillRegistry,
    ) -> str:
        skill = registry.load_skill(skill_id)
        render_context = dict(context)
        render_context.update(args)
        return skill.content.format_map(_SafeFormatMap(render_context))


class SkillInvocationBridge:
    """Expose skills through a model-invocable command-like layer."""

    def __init__(self, registry: FileSkillRegistry, activator: SkillActivator) -> None:
        self._registry = registry
        self._activator = activator

    def list_model_invocable_skills(self) -> list[Command]:
        return [
            Command(
                id=skill.id,
                name=skill.name,
                kind=CommandKind.PROMPT,
                description=skill.description,
                visibility=CommandVisibility.MODEL,
                source="skill",
                metadata={"skill_id": skill.id},
            )
            for skill in self._registry.discover_skills()
        ]

    def invoke_skill(
        self,
        skill_id: str,
        args: JsonObject,
        runtime_context: JsonObject,
    ) -> str:
        return self._activator.render_skill_prompt(
            skill_id=skill_id,
            args=args,
            context=runtime_context,
            registry=self._registry,
        )


class _SafeFormatMap(dict[str, JsonValue]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"
