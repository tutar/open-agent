"""Skill discovery, disclosure, activation, and context management."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from string import Formatter

from openagent.object_model import JsonObject, JsonValue, SerializableModel
from openagent.tools.commands import Command, CommandKind, CommandVisibility


@dataclass(slots=True)
class SkillDiagnostic(SerializableModel):
    level: str
    message: str
    code: str = ""


@dataclass(slots=True)
class ImportedSkillManifest(SerializableModel):
    name: str
    description: str
    license: str | None = None
    compatibility: JsonObject = field(default_factory=dict)
    metadata: JsonObject = field(default_factory=dict)
    allowed_tools: list[str] = field(default_factory=list)
    host_extensions: JsonObject = field(default_factory=dict)
    skill_root: str = ""
    skill_file: str = ""
    source: str = "file"
    diagnostics: list[SkillDiagnostic] = field(default_factory=list)


@dataclass(slots=True)
class DiscoveredSkillRef(SerializableModel):
    skill_id: str
    source: str
    scope: str
    skill_root: str
    skill_file: str
    trust_level: str = "trusted"
    diagnostics: list[SkillDiagnostic] = field(default_factory=list)


@dataclass(slots=True)
class SkillContextBinding(SerializableModel):
    skill_name: str
    activation_ref: str
    skill_root: str = ""
    listed_resources: list[str] = field(default_factory=list)
    activation_mode: str = "model"
    protected_from_compaction: bool = True


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
    disclosure: str = "catalog"
    activation_mode: str = "prompt"
    skill_root: str = ""
    skill_file: str = ""
    diagnostics: list[str] = field(default_factory=list)
    invocable_by_model: bool = True
    invocable_by_user: bool = False
    metadata: JsonObject = field(default_factory=dict)
    imported_manifest: ImportedSkillManifest | None = None
    discovered_ref: DiscoveredSkillRef | None = None
    frontmatter: JsonObject = field(default_factory=dict)
    host_extensions: JsonObject = field(default_factory=dict)
    listed_resources: list[str] = field(default_factory=list)
    frontmatter_mode: str = "stripped"


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
    frontmatter_mode: str = "stripped"
    skill_root: str = ""
    listed_resources: list[str] = field(default_factory=list)
    wrapped: bool = True
    activation_mode: str = "model"
    metadata: JsonObject = field(default_factory=dict)


class SkillContextManager:
    """Track activation identity, dedupe, and resource allowlisting."""

    def __init__(self) -> None:
        self._bindings: dict[str, SkillContextBinding] = {}

    def mark_activated(
        self,
        skill_name: str,
        activation_ref: str,
        *,
        skill_root: str = "",
        listed_resources: list[str] | None = None,
        activation_mode: str = "model",
    ) -> SkillContextBinding:
        existing = self._bindings.get(skill_name)
        if existing is not None:
            return existing
        binding = SkillContextBinding(
            skill_name=skill_name,
            activation_ref=activation_ref,
            skill_root=skill_root,
            listed_resources=list(listed_resources or []),
            activation_mode=activation_mode,
            protected_from_compaction=True,
        )
        self._bindings[skill_name] = binding
        return binding

    def is_already_active(self, skill_name: str) -> bool:
        return skill_name in self._bindings

    def protect_from_compaction(self, activation_ref: str) -> None:
        for binding in self._bindings.values():
            if binding.activation_ref == activation_ref:
                binding.protected_from_compaction = True
                return

    def list_bound_resources(self, skill_name: str) -> list[str]:
        binding = self._bindings.get(skill_name)
        if binding is None:
            return []
        return list(binding.listed_resources)


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
            for skill_ref in self._scan_root(root):
                candidate = self._load_from_ref(skill_ref)
                if candidate is None:
                    continue
                existing = discovered.get(candidate.id)
                if existing is None:
                    discovered[candidate.id] = candidate
                    continue
                winner, loser = self._resolve_shadow(existing, candidate)
                winner_diag = f"shadowed skill '{loser.id}' from {loser.scope}:{loser.skill_root}"
                winner.diagnostics = list(winner.diagnostics) + [winner_diag]
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
                        "disclosure": skill.disclosure,
                        "activation_mode": skill.activation_mode,
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

    def _scan_root(self, root: SkillDiscoveryRoot) -> list[DiscoveredSkillRef]:
        skill_refs: list[DiscoveredSkillRef] = []
        for skill_file in Path(root.path).rglob("SKILL.md"):
            skill_refs.append(
                DiscoveredSkillRef(
                    skill_id=skill_file.parent.name,
                    source=root.source,
                    scope=root.scope,
                    skill_root=str(skill_file.parent),
                    skill_file=str(skill_file),
                    trust_level=root.trust_level,
                )
            )
        return skill_refs

    def _load_from_ref(self, skill_ref: DiscoveredSkillRef) -> SkillDefinition | None:
        raw = Path(skill_ref.skill_file).read_text(encoding="utf-8")
        frontmatter, body, diagnostics = _parse_skill_document(raw)
        if any(d.level == "error" for d in diagnostics):
            return None

        manifest = _build_manifest(frontmatter, body, skill_ref, diagnostics)
        name = manifest.name or skill_ref.skill_id
        description = manifest.description.strip()
        if not description:
            manifest.diagnostics.append(
                SkillDiagnostic(
                    level="error",
                    code="skill_missing_description",
                    message=f"skill '{skill_ref.skill_id}' is missing a description",
                )
            )
            return None

        if name != skill_ref.skill_id:
            manifest.diagnostics.append(
                SkillDiagnostic(
                    level="warning",
                    code="skill_name_mismatch",
                    message=(
                        f"frontmatter name '{name}' differs from directory "
                        f"'{skill_ref.skill_id}'"
                    ),
                )
            )

        host_extensions = dict(manifest.host_extensions)
        arguments = _resolve_arguments(body, host_extensions)
        listed_resources = _list_skill_resources(skill_ref.skill_root)
        model_invocation_disabled = _coerce_bool(
            host_extensions.get("disable-model-invocation"),
            default=False,
        )
        invocable_by_model = not model_invocation_disabled
        if skill_ref.trust_level == "untrusted":
            invocable_by_model = False
            manifest.diagnostics.append(
                SkillDiagnostic(
                    level="warning",
                    code="trust_blocked",
                    message=(
                        f"skill '{skill_ref.skill_id}' is not model-invocable "
                        "because its source is untrusted"
                    ),
                )
            )
        invocable_by_user = _coerce_bool(host_extensions.get("user-invocable"), default=False)
        frontmatter_mode = _coerce_frontmatter_mode(host_extensions.get("model"))
        return SkillDefinition(
            id=skill_ref.skill_id,
            name=name,
            description=description,
            content=body,
            arguments=arguments,
            when_to_use=_coerce_str(host_extensions.get("when_to_use")),
            allowed_tools=list(manifest.allowed_tools),
            source=skill_ref.source,
            scope=skill_ref.scope,
            trust_level=skill_ref.trust_level,
            disclosure="catalog",
            activation_mode="prompt",
            skill_root=skill_ref.skill_root,
            skill_file=skill_ref.skill_file,
            diagnostics=[_format_diagnostic(item) for item in manifest.diagnostics],
            invocable_by_model=invocable_by_model,
            invocable_by_user=invocable_by_user,
            metadata={
                "source_path": skill_ref.skill_file,
                "scope": skill_ref.scope,
                "trust_level": skill_ref.trust_level,
                "source": skill_ref.source,
                "disclosure": "catalog",
                "activation_mode": "prompt",
                **manifest.metadata,
            },
            imported_manifest=manifest,
            discovered_ref=skill_ref,
            frontmatter=dict(frontmatter),
            host_extensions=host_extensions,
            listed_resources=listed_resources,
            frontmatter_mode=frontmatter_mode,
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
    """Render skill content with explicit args, context, and activation tracking."""

    def __init__(self, context_manager: SkillContextManager | None = None) -> None:
        self._context_manager = context_manager or SkillContextManager()

    def activate_skill(
        self,
        skill_id: str,
        args: JsonObject,
        context: JsonObject,
        registry: FileSkillRegistry,
        activation_mode: str = "model",
    ) -> SkillActivationResult:
        skill = registry.load_skill(skill_id)
        already_active = self._context_manager.is_already_active(skill.id)
        rendered = self.render_skill_prompt(skill_id, args, context, registry)
        activation_ref = f"{skill.id}:{activation_mode}"
        binding = self._context_manager.mark_activated(
            skill.id,
            activation_ref,
            skill_root=skill.skill_root,
            listed_resources=skill.listed_resources,
            activation_mode=activation_mode,
        )
        self._context_manager.protect_from_compaction(binding.activation_ref)
        bound_resources: JsonValue = list(self._context_manager.list_bound_resources(skill.id))
        activation_metadata: JsonObject = {
            "skill_id": skill.id,
            "scope": skill.scope,
            "source": skill.source,
            "trust_level": skill.trust_level,
            "diagnostics": list(skill.diagnostics),
            "activation_ref": binding.activation_ref,
            "already_active": already_active,
            "bound_resources": bound_resources,
            "compaction_protected": binding.protected_from_compaction,
            "metadata": dict(skill.metadata),
        }
        return SkillActivationResult(
            skill_name=skill.name,
            body=rendered,
            frontmatter_mode=skill.frontmatter_mode,
            skill_root=skill.skill_root,
            listed_resources=list(skill.listed_resources),
            wrapped=True,
            activation_mode=activation_mode,
            metadata=activation_metadata,
        )

    def render_skill_prompt(
        self,
        skill_id: str,
        args: JsonObject,
        context: JsonObject,
        registry: FileSkillRegistry,
    ) -> str:
        skill = registry.load_skill(skill_id)
        render_context: dict[str, JsonValue] = dict(context)
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
                    "disclosure": skill.disclosure,
                    "listed_resources": list(skill.listed_resources),
                    "diagnostics": list(skill.diagnostics),
                    "metadata": dict(skill.metadata),
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


def _parse_skill_document(raw: str) -> tuple[JsonObject, str, list[SkillDiagnostic]]:
    if not raw.startswith("---\n") and not raw.startswith("---\r\n"):
        return {}, raw, []
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, raw, []
    closing_index = -1
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            closing_index = index
            break
    if closing_index < 0:
        return {}, raw, [
            SkillDiagnostic(
                level="warning",
                code="frontmatter_unclosed",
                message=(
                    "frontmatter start marker found but closing marker is missing; "
                    "treating document as body-only"
                ),
            )
        ]

    block = "\n".join(lines[1:closing_index])
    body = "\n".join(lines[closing_index + 1 :]).lstrip()
    parsed, warnings, error = _parse_frontmatter_block(block)
    if error is None:
        return parsed, body, warnings
    normalized_lines: list[str] = []
    previous_key_opens_block = False
    for line in block.replace("\t", "  ").splitlines():
        stripped = line.strip()
        if not stripped:
            normalized_lines.append("")
            continue
        if (
            line.startswith(" ")
            and not previous_key_opens_block
            and ":" in stripped
            and not stripped.startswith("- ")
        ):
            normalized_lines.append(stripped)
        else:
            normalized_lines.append(line)
        previous_key_opens_block = stripped.endswith(":") and not stripped.startswith("- ")
    normalized_block = "\n".join(normalized_lines)
    parsed_retry, retry_warnings, retry_error = _parse_frontmatter_block(normalized_block)
    if retry_error is None:
        retry_warnings.insert(
            0,
            SkillDiagnostic(
                level="warning",
                code="frontmatter_lenient_retry",
                message="frontmatter required normalization before it could be parsed",
            ),
        )
        return parsed_retry, body, warnings + retry_warnings
    return {}, body, warnings + [
        SkillDiagnostic(
            level="error",
            code="frontmatter_parse_failed",
            message=f"unable to parse frontmatter: {retry_error}",
        )
    ]


def _parse_frontmatter_block(block: str) -> tuple[JsonObject, list[SkillDiagnostic], str | None]:
    lines = [line.rstrip() for line in block.splitlines() if line.strip()]
    diagnostics: list[SkillDiagnostic] = []
    result: JsonObject = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith(" ") or line.startswith("\t"):
            return {}, diagnostics, f"unexpected indentation at line: {line}"
        if ":" not in line:
            diagnostics.append(
                SkillDiagnostic(
                    level="warning",
                    code="frontmatter_ignored_line",
                    message=f"ignored malformed frontmatter line: {line}",
                )
            )
            index += 1
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if value:
            result[key] = _parse_scalar(value)
            index += 1
            continue

        nested_lines: list[str] = []
        index += 1
        while index < len(lines):
            nested = lines[index]
            if not nested.startswith(" "):
                break
            nested_lines.append(nested[2:] if nested.startswith("  ") else nested.lstrip())
            index += 1
        nested_value, nested_warning = _parse_nested_block(nested_lines)
        if nested_warning is not None:
            diagnostics.append(nested_warning)
        result[key] = nested_value
    return result, diagnostics, None


def _parse_nested_block(lines: list[str]) -> tuple[JsonValue, SkillDiagnostic | None]:
    if not lines:
        return "", None
    if all(line.startswith("- ") for line in lines):
        return [_parse_scalar(line[2:].strip()) for line in lines], None
    mapping: JsonObject = {}
    for line in lines:
        if ":" not in line:
            return "", SkillDiagnostic(
                level="warning",
                code="frontmatter_nested_ignored",
                message=f"ignored malformed nested frontmatter line: {line}",
            )
        key, raw_value = line.split(":", 1)
        mapping[key.strip()] = _parse_scalar(raw_value.strip())
    return mapping, None


def _parse_scalar(raw: str) -> JsonValue:
    text = raw.strip()
    if not text:
        return ""
    if text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1]
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part.strip()) for part in inner.split(",")]
    return text


def _build_manifest(
    frontmatter: JsonObject,
    body: str,
    skill_ref: DiscoveredSkillRef,
    diagnostics: list[SkillDiagnostic],
) -> ImportedSkillManifest:
    metadata = _coerce_json_object(frontmatter.get("metadata"))
    compatibility = _coerce_json_object(frontmatter.get("compatibility"))
    allowed_tools = _coerce_string_list(frontmatter.get("allowed-tools"))
    host_extensions = {
        key: value
        for key, value in frontmatter.items()
        if key
        not in {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
    }
    return ImportedSkillManifest(
        name=_coerce_str(frontmatter.get("name")) or skill_ref.skill_id,
        description=_coerce_str(frontmatter.get("description")) or _extract_description(body),
        license=_coerce_optional_str(frontmatter.get("license")),
        compatibility=compatibility,
        metadata=metadata,
        allowed_tools=allowed_tools,
        host_extensions=host_extensions,
        skill_root=skill_ref.skill_root,
        skill_file=skill_ref.skill_file,
        source=skill_ref.source,
        diagnostics=list(diagnostics) + list(skill_ref.diagnostics),
    )


def _resolve_arguments(body: str, host_extensions: JsonObject) -> list[str]:
    explicit_arguments = _coerce_string_list(host_extensions.get("arguments"))
    if explicit_arguments:
        return explicit_arguments
    return sorted(
        {
            field_name
            for _, field_name, _, _ in Formatter().parse(body)
            if field_name is not None and field_name.isidentifier()
        }
    )


def _extract_description(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped
    return ""


def _coerce_str(value: JsonValue | None) -> str:
    return value if isinstance(value, str) else ""


def _coerce_optional_str(value: JsonValue | None) -> str | None:
    return value if isinstance(value, str) else None


def _coerce_bool(value: JsonValue | None, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _coerce_json_object(value: JsonValue | None) -> JsonObject:
    if isinstance(value, dict):
        return {str(key): _coerce_json_value(item) for key, item in value.items()}
    return {}


def _coerce_string_list(value: JsonValue | None) -> list[str]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    if isinstance(value, str) and value:
        return [value]
    return []


def _coerce_json_value(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return {str(key): _coerce_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_coerce_json_value(item) for item in value]
    return value


def _coerce_frontmatter_mode(value: JsonValue | None) -> str:
    mode = _coerce_str(value)
    if mode in {"full", "stripped"}:
        return mode
    return "stripped"


def _format_diagnostic(diagnostic: SkillDiagnostic) -> str:
    prefix = f"{diagnostic.level}"
    if diagnostic.code:
        prefix += f"[{diagnostic.code}]"
    return f"{prefix}: {diagnostic.message}"
