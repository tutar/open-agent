"""Shared path normalization helpers."""

from __future__ import annotations

import os
from pathlib import Path


def normalize_workspace_root(
    workspace_root: str | None,
    *,
    default: str | None = None,
) -> str:
    """Resolve a workspace root from env-like input into an absolute path."""

    raw_value = workspace_root if workspace_root is not None else default
    candidate = raw_value if raw_value else os.getcwd()
    expanded = os.path.expanduser(os.path.expandvars(candidate))
    return str(Path(expanded).resolve())
