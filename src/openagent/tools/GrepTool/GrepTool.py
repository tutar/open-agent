"""Workspace-bound content search tool."""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
from typing import cast

from openagent.object_model import JsonValue, ToolResult
from openagent.tools.GrepTool.prompt import DESCRIPTION, GREP_TOOL_NAME
from openagent.tools.models import ToolExecutionContext
from openagent.tools.tool_base import BuiltinToolBase
from openagent.tools.tool_paths import effective_root, resolve_optional_directory
from openagent.tools.tool_schema import integer_property, object_schema, string_property
from openagent.tools.tool_validation import require_positive_int_field, require_string_field


class GrepTool(BuiltinToolBase):
    root: str = "."
    default_limit: int = 200

    def __init__(self, root: str = ".") -> None:
        super().__init__(
            name=GREP_TOOL_NAME,
            description_text=DESCRIPTION,
            input_schema=object_schema(
                {
                    "pattern": string_property(
                        "Substring or simple pattern to search for inside workspace files.",
                        examples=["TODO", "OpenAgent", "validation_failed"],
                    ),
                    "path": string_property(
                        "Optional subdirectory relative to the workspace root to search within.",
                        examples=["src", "tests"],
                    ),
                    "glob": string_property(
                        "Optional file glob used to restrict which files are searched.",
                        examples=["*.py", "src/**/*.ts"],
                    ),
                    "limit": integer_property(
                        "Optional maximum number of matching lines to return.",
                        examples=[20, 100],
                        minimum=1,
                    ),
                },
                required=["pattern"],
            ),
            aliases=["grep"],
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
        if "glob" in normalized:
            normalized["glob"] = require_string_field(normalized, "glob")
        if "limit" in normalized:
            normalized["limit"] = require_positive_int_field(normalized, "limit")
        return normalized

    def call(
        self,
        arguments: dict[str, object],
        context: ToolExecutionContext | None = None,
    ) -> ToolResult:
        needle = str(arguments["pattern"])
        glob_filter = cast(str | None, arguments.get("glob"))
        results: list[str] = []
        root = Path(effective_root(self.root, context))
        search_root = resolve_optional_directory(root, cast(str | None, arguments.get("path")))
        limit = int(arguments.get("limit", self.default_limit))
        for path in sorted(search_root.rglob("*")):
            if not path.is_file():
                continue
            relative = str(path.relative_to(root))
            if glob_filter is not None and not (
                fnmatch(relative, glob_filter) or fnmatch(path.name, glob_filter)
            ):
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for line_number, line in enumerate(content.splitlines(), start=1):
                if needle in line:
                    results.append(f"{relative}:{line_number}:{line}")
                    if len(results) >= limit:
                        return ToolResult(
                            tool_name=self.name,
                            success=True,
                            content=cast(list[JsonValue], results),
                            structured_content={
                                "search_root": (
                                    str(search_root.relative_to(root))
                                    if search_root != root
                                    else "."
                                ),
                                "glob": glob_filter,
                                "limit": limit,
                                "count": len(results),
                                "truncated": True,
                            },
                        )
        return ToolResult(
            tool_name=self.name,
            success=True,
            content=cast(list[JsonValue], results),
            structured_content={
                "search_root": str(search_root.relative_to(root)) if search_root != root else ".",
                "glob": glob_filter,
                "limit": limit,
                "count": len(results),
                "truncated": False,
            },
        )
