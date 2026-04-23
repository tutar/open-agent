"""Context editing helpers."""

from __future__ import annotations

from pathlib import Path

from openagent.harness.context_engineering.governance.models import (
    ContentExternalizationResult,
)
from openagent.object_model import ToolResult


def externalize_tool_result(
    result: ToolResult,
    *,
    externalize_threshold_chars: int,
    storage_dir: str | None,
) -> ToolResult:
    content_text = "\n".join(str(item) for item in result.content)
    if len(content_text) <= externalize_threshold_chars:
        return result

    preview = content_text[:externalize_threshold_chars]
    persisted_ref: str | None = None
    if storage_dir is not None:
        storage_path = Path(storage_dir)
        storage_path.mkdir(parents=True, exist_ok=True)
        result_path = storage_path / f"{result.tool_name}.txt"
        result_path.write_text(content_text, encoding="utf-8")
        persisted_ref = str(result_path)

    result.content = [preview]
    result.persisted_ref = persisted_ref
    result.truncated = True
    metadata = dict(result.metadata or {})
    metadata["preview"] = preview
    if persisted_ref is not None:
        metadata["externalized"] = True
        metadata["persisted_ref"] = persisted_ref
    result.metadata = metadata
    return result


def externalize_payload(
    payload: str,
    *,
    threshold_chars: int,
    storage_dir: str | None,
    name: str,
) -> ContentExternalizationResult:
    if len(payload) <= threshold_chars:
        return ContentExternalizationResult(
            preview=payload,
            persisted_ref=None,
            externalized=False,
        )
    preview = payload[:threshold_chars]
    persisted_ref: str | None = None
    if storage_dir is not None:
        storage_path = Path(storage_dir)
        storage_path.mkdir(parents=True, exist_ok=True)
        payload_path = storage_path / f"{name}.txt"
        payload_path.write_text(payload, encoding="utf-8")
        persisted_ref = str(payload_path)
    return ContentExternalizationResult(
        preview=preview,
        persisted_ref=persisted_ref,
        externalized=True,
    )


def tool_result_message_content(result: ToolResult) -> str:
    content_text = "\n".join(str(item) for item in result.content)
    if result.persisted_ref is None:
        return f"{result.tool_name}: {content_text}"
    return (
        f"{result.tool_name}: {content_text}\n"
        "[tool result externalized to internal storage; this is not a workspace file path "
        "and should not be read with local file tools]"
    )
