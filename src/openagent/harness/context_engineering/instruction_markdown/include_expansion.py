"""Instruction include expansion."""

from __future__ import annotations

from pathlib import Path


def expand_includes(text: str, *, base_dir: Path) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("!include ") or stripped.startswith("@include "):
            _, raw_path = stripped.split(maxsplit=1)
            include_path = (base_dir / raw_path.strip()).resolve()
            if include_path.exists():
                lines.append(include_path.read_text(encoding="utf-8"))
            continue
        lines.append(line)
    return "\n".join(lines)
