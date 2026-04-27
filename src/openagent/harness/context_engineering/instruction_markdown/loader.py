"""Instruction markdown loading."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openagent.harness.context_engineering.instruction_markdown.conditional_rules import (
    rule_matches,
)
from openagent.harness.context_engineering.instruction_markdown.include_expansion import (
    expand_includes,
)
from openagent.harness.context_engineering.instruction_markdown.models import (
    InstructionDocument,
    InstructionRule,
)
from openagent.object_model import JsonObject


@dataclass(slots=True)
class InstructionMarkdownLoader:
    def load(
        self,
        *,
        workspace_root: str,
        role_user_path: str | None = None,
        runtime_state: JsonObject | None = None,
    ) -> list[InstructionDocument]:
        candidates = self._candidate_paths(
            workspace_root=workspace_root,
            role_user_path=role_user_path,
            runtime_state=runtime_state,
        )
        documents: list[InstructionDocument] = []
        for path in candidates:
            if not path.exists():
                continue
            text = expand_includes(path.read_text(encoding="utf-8"), base_dir=path.parent)
            rules = self._parse_rules(path, text, runtime_state=runtime_state)
            documents.append(InstructionDocument(source_path=str(path), rules=rules))
        return documents

    def _parse_rules(
        self,
        path: Path,
        text: str,
        *,
        runtime_state: JsonObject | None = None,
    ) -> list[InstructionRule]:
        condition: str | None = None
        rules: list[InstructionRule] = []
        buffer: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("<!-- if:") and stripped.endswith("-->"):
                if buffer:
                    rendered = "\n".join(buffer).strip()
                    if rendered and rule_matches(condition, runtime_state):
                        rules.append(
                            InstructionRule(
                                source_path=str(path),
                                text=rendered,
                                condition=condition,
                            )
                        )
                    buffer = []
                condition = stripped.removeprefix("<!-- if:").removesuffix("-->").strip()
                continue
            buffer.append(line)
        rendered = "\n".join(buffer).strip()
        if rendered and rule_matches(condition, runtime_state):
            rules.append(
                InstructionRule(
                    source_path=str(path),
                    text=rendered,
                    condition=condition,
                )
            )
        return rules

    def _candidate_paths(
        self,
        *,
        workspace_root: str,
        role_user_path: str | None,
        runtime_state: JsonObject | None,
    ) -> list[Path]:
        root = Path(workspace_root).resolve()
        paths: list[Path] = []
        seen: set[Path] = set()

        def append_if_new(path: Path) -> None:
            resolved = path.resolve()
            if resolved in seen:
                return
            seen.add(resolved)
            paths.append(resolved)

        if isinstance(role_user_path, str) and role_user_path.strip():
            append_if_new(Path(role_user_path))
        append_if_new(Path.home() / ".openagent" / "AGENTS.md")
        append_if_new(root / "AGENTS.md")
        append_if_new(root / "RULES.md")

        target_path_value = (
            runtime_state.get("target_path") if isinstance(runtime_state, dict) else None
        )
        if isinstance(target_path_value, str) and target_path_value:
            target_path = Path(target_path_value)
            if not target_path.is_absolute():
                target_path = root / target_path
            target_path = target_path.resolve()
            target_dir = target_path if target_path.is_dir() else target_path.parent
            try:
                relative_parts = target_dir.relative_to(root).parts
            except ValueError:
                relative_parts = ()
            current = root
            for part in relative_parts:
                current = current / part
                append_if_new(current / "AGENTS.md")
                append_if_new(current / "RULES.md")
        return paths
