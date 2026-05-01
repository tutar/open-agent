"""Shared validation helpers for builtin tool inputs."""

from __future__ import annotations


def require_string_field(arguments: dict[str, object], field_name: str) -> str:
    value = arguments.get(field_name)
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def require_positive_int_field(arguments: dict[str, object], field_name: str) -> int:
    value = arguments.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    if value < 1:
        raise ValueError(f"{field_name} must be >= 1")
    return value
