"""Provider-side projection helpers for transcript tool results."""

from __future__ import annotations

import json
from typing import cast

from openagent.object_model import JsonObject, JsonValue, normalize_tool_result_content, render_tool_result_content


def tool_result_content_to_text(content: JsonValue) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return render_tool_result_content(cast(list[JsonValue], content))
    return str(content)


def tool_result_content_to_anthropic(content: JsonValue) -> str | list[JsonObject]:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    normalized = normalize_tool_result_content(cast(list[JsonValue], content))
    blocks: list[JsonObject] = []
    for item in normalized:
        if not isinstance(item, dict):
            blocks.append({"type": "text", "text": str(item)})
            continue
        block_type = str(item.get("type", ""))
        if block_type == "text":
            blocks.append({"type": "text", "text": str(item.get("text", ""))})
            continue
        if block_type == "image":
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": str(item.get("media_type", "image/png")),
                        "data": str(item.get("data", "")),
                    },
                }
            )
            continue
        blocks.append({"type": "text", "text": tool_result_content_to_text([item])})
    if all(block["type"] == "text" for block in blocks):
        return "\n".join(str(block.get("text", "")) for block in blocks if str(block.get("text", "")))
    return blocks


def openai_tool_result_json_string(
    *,
    content: JsonValue,
    metadata: JsonObject,
) -> str:
    envelope: JsonObject = {
        "tool_name": str(metadata.get("tool_name", "")),
        "success": bool(metadata.get("success", True)),
        "content": (
            normalize_tool_result_content(cast(list[JsonValue], content))
            if isinstance(content, list)
            else content
        ),
        "structured_content": (
            dict(cast(JsonObject, metadata["structured_content"]))
            if isinstance(metadata.get("structured_content"), dict)
            else None
        ),
        "truncated": bool(metadata.get("truncated", False)),
        "persisted_ref": (
            str(metadata["persisted_ref"]) if metadata.get("persisted_ref") is not None else None
        ),
    }
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
