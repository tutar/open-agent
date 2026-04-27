"""Minimal markdown frontmatter parsing for role assets."""

from __future__ import annotations

from openagent.object_model import JsonObject, JsonValue


def parse_markdown_frontmatter(raw: str) -> tuple[JsonObject, str]:
    if not raw.startswith("---\n") and not raw.startswith("---\r\n"):
        return {}, raw
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, raw
    closing_index = -1
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            closing_index = index
            break
    if closing_index < 0:
        return {}, raw
    frontmatter = _parse_block("\n".join(lines[1:closing_index]))
    body = "\n".join(lines[closing_index + 1 :]).lstrip("\n")
    return frontmatter, body


def _parse_block(block: str) -> JsonObject:
    result: JsonObject = {}
    current_key: str | None = None
    for raw_line in block.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line.startswith("  - ") and current_key is not None:
            current = result.setdefault(current_key, [])
            if isinstance(current, list):
                current.append(_parse_scalar(stripped.removeprefix("- ").strip()))
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not value:
            result[key] = []
            current_key = key
            continue
        result[key] = _parse_scalar(value)
        current_key = key
    return result


def _parse_scalar(value: str) -> JsonValue:
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(item.strip()) for item in inner.split(",")]
    if value.startswith("{") and value.endswith("}"):
        inner = value[1:-1].strip()
        payload: JsonObject = {}
        if not inner:
            return payload
        for part in inner.split(","):
            if ":" not in part:
                continue
            key, item_value = part.split(":", 1)
            payload[key.strip()] = _parse_scalar(item_value.strip())
        return payload
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        return int(value)
    except ValueError:
        return value
