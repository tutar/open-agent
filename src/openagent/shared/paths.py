"""Shared path normalization helpers."""

from __future__ import annotations

import os
from pathlib import Path


def _expand_env_path(value: str) -> str:
    expanded = value
    for _ in range(8):
        next_expanded = os.path.expandvars(expanded)
        if next_expanded == expanded:
            break
        expanded = next_expanded
    return os.path.expanduser(expanded)


def resolve_path_env(name: str, default: str | None = None) -> str | None:
    raw_value = os.getenv(name)
    if raw_value is None:
        if default is None:
            return None
        raw_value = default
    expanded = _expand_env_path(raw_value)
    return str(Path(expanded).resolve())
