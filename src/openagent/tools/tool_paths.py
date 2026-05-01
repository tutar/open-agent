"""Shared workspace/path helpers for local builtin tools."""

from __future__ import annotations

from pathlib import Path

from openagent.tools.models import ToolExecutionContext


def effective_root(root: str, context: ToolExecutionContext | None) -> str:
    if (
        context is not None
        and isinstance(context.working_directory, str)
        and context.working_directory
    ):
        return str(Path(context.working_directory).resolve())
    raise RuntimeError("tool execution requires an explicit working_directory")


def resolve_path(root: str, raw_path: str) -> Path:
    # All local file tools stay bound to the current workspace. Centralizing the
    # check keeps their escape-prevention behavior aligned.
    root_path = Path(root).resolve()
    path = (root_path / raw_path).resolve()
    try:
        path.relative_to(root_path)
    except ValueError as exc:
        raise PermissionError("path escapes current workspace") from exc
    return path


def resolve_optional_directory(root: Path, raw_path: str | None) -> Path:
    if raw_path is None or not raw_path.strip():
        return root
    candidate = resolve_path(str(root), raw_path)
    if not candidate.exists():
        raise FileNotFoundError(f"search path does not exist: {candidate}")
    if not candidate.is_dir():
        raise NotADirectoryError(f"search path is not a directory: {candidate}")
    return candidate
