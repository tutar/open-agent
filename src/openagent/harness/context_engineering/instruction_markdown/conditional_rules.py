"""Conditional instruction helpers."""

from __future__ import annotations

from openagent.object_model import JsonObject


def rule_matches(condition: str | None, runtime_state: JsonObject | None) -> bool:
    if condition is None or not condition.strip():
        return True
    if not isinstance(runtime_state, dict):
        return False
    if "=" not in condition:
        return False
    key, expected = [item.strip() for item in condition.split("=", 1)]
    actual = runtime_state.get(key)
    return str(actual) == expected
