"""Shared text/rendering helpers for builtin tools."""

from __future__ import annotations

from openagent.object_model import JsonObject


def render_numbered_file_slice(
    content: str,
    *,
    offset: int,
    limit: int,
    max_lines: int,
) -> tuple[str, JsonObject]:
    lines = content.splitlines()
    total_lines = len(lines)
    start_index = min(total_lines, max(0, offset - 1))
    effective_limit = min(limit, max_lines)
    selected = lines[start_index : start_index + effective_limit]
    rendered = "\n".join(
        f"{line_number}\t{line}"
        for line_number, line in enumerate(selected, start=start_index + 1)
    )
    return (
        rendered,
        {
            "offset": offset,
            "limit": effective_limit,
            "total_lines": total_lines,
            "returned_lines": len(selected),
            "truncated": start_index + len(selected) < total_lines,
        },
    )


def merge_process_output(stdout: str, stderr: str) -> str:
    parts = [part.rstrip() for part in (stdout, stderr) if part.strip()]
    return "\n".join(parts)
