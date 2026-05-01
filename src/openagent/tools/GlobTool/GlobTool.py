"""Workspace-bound file discovery tool."""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
from typing import cast

from openagent.object_model import JsonValue, ToolResult, text_block, tool_reference_block
from openagent.tools.GlobTool.prompt import DESCRIPTION, GLOB_TOOL_NAME
from openagent.tools.models import ToolExecutionContext
from openagent.tools.tool_base import BuiltinToolBase
from openagent.tools.tool_paths import effective_root, resolve_optional_directory
from openagent.tools.tool_schema import integer_property, object_schema, string_property
from openagent.tools.tool_validation import require_positive_int_field, require_string_field


class GlobTool(BuiltinToolBase):
    root: str = "."
    default_limit: int = 200

    def __init__(self, root: str = ".") -> None:
        super().__init__(
            name=GLOB_TOOL_NAME,
            description_text=DESCRIPTION,
            input_schema=object_schema(
                {
                    "pattern": string_property(
                        "Glob pattern to match files relative to the workspace root.",
                        examples=["*", "*.py", "src/**/*.py"],
                    ),
                    "path": string_property(
                        "Optional subdirectory relative to the workspace root to search within.",
                        examples=["sympy", "tests/tools"],
                    ),
                    "limit": integer_property(
                        "Optional maximum number of matching files to return.",
                        examples=[20, 100],
                        minimum=1,
                    ),
                },
                required=["pattern"],
            ),
            aliases=["glob"],
            supports_result_persistence=True,
        )
        self.root = root

    def is_read_only(self, arguments: dict[str, object]) -> bool:
        del arguments
        return True

    def is_concurrency_safe(self, arguments: dict[str, object]) -> bool:
        del arguments
        return True

    def validate_input(self, arguments: dict[str, object]) -> dict[str, object]:
        normalized = dict(arguments)
        normalized["pattern"] = require_string_field(normalized, "pattern")
        if "path" in normalized:
            normalized["path"] = require_string_field(normalized, "path")
        if "limit" in normalized:
            normalized["limit"] = require_positive_int_field(normalized, "limit")
        return normalized

    def call(
        self,
        arguments: dict[str, object],
        context: ToolExecutionContext | None = None,
    ) -> ToolResult:
        pattern = str(arguments["pattern"])
        root = Path(effective_root(self.root, context))
        search_root = resolve_optional_directory(root, cast(str | None, arguments.get("path")))
        limit = int(arguments.get("limit", self.default_limit))
        matches: list[str] = []
        for path in sorted(search_root.rglob("*")):
            if not path.is_file():
                continue
            relative = str(path.relative_to(root))
            if fnmatch(relative, pattern) or fnmatch(path.name, pattern):
                matches.append(relative)
            if len(matches) >= limit:
                break
        if matches:
            result_content = cast(
                list[JsonValue],
                [
                    text_block(f"Found {len(matches)} matching files"),
                    *[
                        tool_reference_block(ref=match, title=match, preview=match, ref_kind="file")
                        for match in matches
                    ],
                ],
            )
        else:
            result_content = cast(list[JsonValue], [text_block("No files found")])
        return ToolResult(
            tool_name=self.name,
            success=True,
            content=result_content,
            structured_content={
                "search_root": str(search_root.relative_to(root)) if search_root != root else ".",
                "limit": limit,
                "count": len(matches),
            },
        )
