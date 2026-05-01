"""Helpers for richer tool-result content blocks."""

from __future__ import annotations

import json
from typing import cast

from openagent.object_model.base import JsonObject, JsonValue

TEXT_BLOCK_TYPE = "text"
IMAGE_BLOCK_TYPE = "image"
TOOL_REFERENCE_BLOCK_TYPE = "tool_reference"


def text_block(text: str) -> JsonObject:
    return {"type": TEXT_BLOCK_TYPE, "text": text}


def image_block(
    *,
    media_type: str,
    data: str,
    alt_text: str | None = None,
) -> JsonObject:
    payload: JsonObject = {
        "type": IMAGE_BLOCK_TYPE,
        "media_type": media_type,
        "data": data,
    }
    if alt_text is not None:
        payload["alt_text"] = alt_text
    return payload


def tool_reference_block(
    *,
    ref: str,
    title: str | None = None,
    preview: str | None = None,
    ref_kind: str = "file",
) -> JsonObject:
    payload: JsonObject = {
        "type": TOOL_REFERENCE_BLOCK_TYPE,
        "ref": ref,
        "ref_kind": ref_kind,
    }
    if title is not None:
        payload["title"] = title
    if preview is not None:
        payload["preview"] = preview
    return payload


def normalize_tool_result_content(content: list[JsonValue]) -> list[JsonValue]:
    normalized: list[JsonValue] = []
    for item in content:
        if item is None:
            continue
        if isinstance(item, str):
            normalized.append(text_block(item))
            continue
        if isinstance(item, dict) and isinstance(item.get("type"), str):
            normalized.append(cast(JsonValue, item))
            continue
        if isinstance(item, list):
            normalized.append(text_block(render_tool_result_content(cast(list[JsonValue], item))))
            continue
        normalized.append(text_block(json.dumps(item, ensure_ascii=False)))
    return normalized


def render_tool_result_content(content: list[JsonValue]) -> str:
    parts: list[str] = []
    for item in normalize_tool_result_content(content):
        if isinstance(item, dict):
            block_type = str(item.get("type", ""))
            if block_type == TEXT_BLOCK_TYPE:
                parts.append(str(item.get("text", "")))
                continue
            if block_type == IMAGE_BLOCK_TYPE:
                alt = str(item.get("alt_text", "")).strip()
                media_type = str(item.get("media_type", "")).strip()
                parts.append(f"[image: {alt or media_type or 'image'}]")
                continue
            if block_type == TOOL_REFERENCE_BLOCK_TYPE:
                preview = str(item.get("preview", "")).strip()
                title = str(item.get("title", "")).strip()
                ref = str(item.get("ref", "")).strip()
                parts.append(preview or title or ref)
                continue
            if "text" in item:
                parts.append(str(item.get("text", "")))
                continue
        parts.append(json.dumps(item, ensure_ascii=False))
    return "\n".join(part for part in parts if part).strip()


def has_non_textual_tool_result_content(content: list[JsonValue]) -> bool:
    for item in normalize_tool_result_content(content):
        if isinstance(item, dict) and str(item.get("type", "")) != TEXT_BLOCK_TYPE:
            return True
    return False
