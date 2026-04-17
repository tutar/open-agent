"""Skill discovery, disclosure, and activation baseline."""

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
    source: str = "file"
    scope: str = "project"
    trust_level: str = "trusted"
    disclosure: str = "model"
    activation_mode: str = "prompt"
    skill_root: str = ""
    skill_file: str = ""
    diagnostics: list[str] = field(default_factory=list)
    invocable_by_model: bool = True
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class SkillDiscoveryRoot(SerializableModel):
    path: str
    scope: str = "project"
    source: str = "file"
    trust_level: str = "trusted"


@dataclass(slots=True)
class SkillCatalogEntry(SerializableModel):
    name: str
    description: str
    location: str | None = None
    source: str = "file"
    invocable_by_model: bool = True
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class SkillActivationResult(SerializableModel):
    skill_name: str
    body: str
    frontmatter_mode: str = "full"
    skill_root: str = ""
    listed_resources: list[str] = field(default_factory=list)
    wrapped: bool = True
    activation_mode: str = "model"
    metadata: JsonObject = field(default_factory=dict)


class FileSkillRegistry:
    """Discover skills from `SKILL.md` directories with deterministic precedence."""

    _scope_precedence = {
        "project": 0,
        "managed": 1,
        "user": 2,
        "bundled": 3,
        "plugin": 4,
        "mcp": 5,
    }

    def __init__(self, roots: list[str | Path | SkillDiscoveryRoot]) -> None:
        self._roots = [self._coerce_root(root) for root in roots]
        self._cache: dict[str, SkillDefinition] = {}

    def discover_skills(self, scope: str = "default") -> list[SkillDefinition]:
        del scope
        discovered: dict[str, SkillDefinition] = {}
        for root in self._roots:
            root_path = Path(root.path)
            if not root_path.exists():
                continue
            for skill_file in root_path.rglob("SKILL.md"):
                candidate = self._load_from_file(skill_file, root)
                existing = discovered.get(candidate.id)
                if existing is None:
                    discovered[candidate.id] = candidate
                    continue
                winner, loser = self._resolve_shadow(existing, candidate)
                winner.diagnostics = list(winner.diagnostics) + [
                    f"shadowed skill '{loser.id}' from {loser.scope}:{loser.skill_root}"
                ]
                winner.metadata["shadowed_skill_root"] = loser.skill_root
                winner.metadata["shadowed_skill_scope"] = loser.scope
                discovered[winner.id] = winner
        self._cache = discovered
        return list(discovered.values())

    def list_catalog_entries(self, audience: str = "model") -> list[SkillCatalogEntry]:
        entries: list[SkillCatalogEntry] = []
        for skill in self.discover_skills():
            if audience == "model" and not skill.invocable_by_model:
                continue
            entries.append(
                SkillCatalogEntry(
                    name=skill.name,
                    description=skill.description,
                    location=skill.skill_root or None,
                    source=skill.source,
                    invocable_by_model=skill.invocable_by_model,
                    metadata={
                        "skill_id": skill.id,
                        "scope": skill.scope,
                        "trust_level": skill.trust_level,
                        "diagnostics": list(skill.diagnostics),
                    },
                )
            )
        return entries

    def load_skill(self, skill_id: str) -> SkillDefinition:
        if skill_id not in self._cache:
            self.discover_skills()
        return self._cache[skill_id]

    def invalidate_skills(self, scope: str = "default") -> None:
        del scope
        self._cache = {}

    def _load_from_file(self, skill_file: Path, root: SkillDiscoveryRoot) -> SkillDefinition:
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
            source=root.source,
            scope=root.scope,
            trust_level=root.trust_level,
            skill_root=str(skill_file.parent),
            skill_file=str(skill_file),
            metadata={
                "source_path": str(skill_file),
                "scope": root.scope,
                "trust_level": root.trust_level,
            },
        )

    def _coerce_root(self, root: str | Path | SkillDiscoveryRoot) -> SkillDiscoveryRoot:
        if isinstance(root, SkillDiscoveryRoot):
            return root
        path = Path(root)
        return SkillDiscoveryRoot(path=str(path), scope=_infer_scope(path))

    def _resolve_shadow(
        self,
        existing: SkillDefinition,
        candidate: SkillDefinition,
    ) -> tuple[SkillDefinition, SkillDefinition]:
        existing_rank = self._scope_precedence.get(existing.scope, 100)
        candidate_rank = self._scope_precedence.get(candidate.scope, 100)
        if candidate_rank < existing_rank:
            return candidate, existing
        if candidate_rank > existing_rank:
            return existing, candidate
        if candidate.skill_root < existing.skill_root:
            return candidate, existing
        return existing, candidate


class SkillActivator:
    """Render skill content with explicit args and context."""

    def activate_skill(
        self,
        skill_id: str,
        args: JsonObject,
        context: JsonObject,
        registry: FileSkillRegistry,
        activation_mode: str = "model",
    ) -> SkillActivationResult:
        skill = registry.load_skill(skill_id)
        rendered = self.render_skill_prompt(skill_id, args, context, registry)
        return SkillActivationResult(
            skill_name=skill.name,
            body=rendered,
            skill_root=skill.skill_root,
            listed_resources=_list_skill_resources(skill.skill_root),
            wrapped=True,
            activation_mode=activation_mode,
            metadata={
                "skill_id": skill.id,
                "scope": skill.scope,
                "source": skill.source,
                "diagnostics": list(skill.diagnostics),
            },
        )

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
                metadata={
                    "skill_id": skill.id,
                    "skill_source": skill.source,
                    "activation_mode": skill.activation_mode,
                    "scope": skill.scope,
                    "trust_level": skill.trust_level,
                    "catalog_description": skill.description,
                },
            )
            for skill in self._registry.discover_skills()
            if skill.invocable_by_model
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

    def invoke_skill_wrapped(
        self,
        skill_id: str,
        args: JsonObject,
        runtime_context: JsonObject,
        activation_mode: str = "model",
    ) -> SkillActivationResult:
        return self._activator.activate_skill(
            skill_id=skill_id,
            args=args,
            context=runtime_context,
            registry=self._registry,
            activation_mode=activation_mode,
        )


class _SafeFormatMap(dict[str, JsonValue]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _infer_scope(path: Path) -> str:
    lowered = str(path).lower()
    if "bundled" in lowered:
        return "bundled"
    if lowered.startswith(str(Path.home()).lower()):
        return "user"
    return "project"


def _list_skill_resources(skill_root: str) -> list[str]:
    root = Path(skill_root)
    resources: list[str] = []
    for name in ("scripts", "references", "assets"):
        if (root / name).exists():
            resources.append(name)
    return resources
