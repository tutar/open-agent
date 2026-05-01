"""Shared JSON-schema helpers for tool input definitions."""

from __future__ import annotations


def string_property(
    description: str,
    *,
    examples: list[str] | None = None,
    enum: list[str] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "type": "string",
        "description": description,
    }
    if examples:
        payload["examples"] = examples
    if enum:
        payload["enum"] = enum
    return payload


def integer_property(
    description: str,
    *,
    examples: list[int] | None = None,
    minimum: int | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "type": "integer",
        "description": description,
    }
    if examples:
        payload["examples"] = examples
    if minimum is not None:
        payload["minimum"] = minimum
    return payload


def boolean_property(description: str) -> dict[str, object]:
    return {
        "type": "boolean",
        "description": description,
    }


def object_schema(
    properties: dict[str, dict[str, object]],
    *,
    required: list[str],
) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }
