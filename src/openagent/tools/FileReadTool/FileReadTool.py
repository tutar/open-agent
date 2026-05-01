"""Workspace-bound text file reader."""

from __future__ import annotations

from typing import cast

from openagent.object_model import JsonValue, ToolResult
from openagent.tools.FileReadTool.prompt import DESCRIPTION, READ_TOOL_NAME
from openagent.tools.models import ToolExecutionContext
from openagent.tools.tool_base import BuiltinToolBase
from openagent.tools.tool_io import render_numbered_file_slice
from openagent.tools.tool_paths import effective_root, resolve_path
from openagent.tools.tool_schema import integer_property, object_schema, string_property
from openagent.tools.tool_validation import require_positive_int_field, require_string_field


class FileReadTool(BuiltinToolBase):
    root: str = "."
    max_lines: int = 2_000

    def __init__(self, root: str = ".") -> None:
        super().__init__(
            name=READ_TOOL_NAME,
            description_text=DESCRIPTION,
            input_schema=object_schema(
                {
                    "path": string_property(
                        "Path to the file, relative to the current workspace root.",
                        examples=["README.md", "src/openagent/tools/FileReadTool/FileReadTool.py"],
                    ),
                    "offset": integer_property(
                        "Optional 1-based line number to start reading from.",
                        examples=[1, 120],
                        minimum=1,
                    ),
                    "limit": integer_property(
                        "Optional maximum number of lines to read. Defaults to all lines up to "
                        "the per-call cap.",
                        examples=[20, 200],
                        minimum=1,
                    ),
                },
                required=["path"],
            ),
            aliases=["read"],
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
        normalized["path"] = require_string_field(normalized, "path")
        if "offset" in normalized:
            normalized["offset"] = require_positive_int_field(normalized, "offset")
        if "limit" in normalized:
            limit = require_positive_int_field(normalized, "limit")
            if limit > self.max_lines:
                raise ValueError(f"limit must be <= {self.max_lines}")
            normalized["limit"] = limit
        return normalized

    def call(
        self,
        arguments: dict[str, object],
        context: ToolExecutionContext | None = None,
    ) -> ToolResult:
        path = resolve_path(effective_root(self.root, context), str(arguments["path"]))
        if not path.is_file():
            raise IsADirectoryError(f"path is not a file: {path}")
        content = path.read_text(encoding="utf-8")
        numbered, metadata = render_numbered_file_slice(
            content,
            offset=int(arguments.get("offset", 1)),
            limit=int(arguments.get("limit", self.max_lines)),
            max_lines=self.max_lines,
        )
        return ToolResult(
            tool_name=self.name,
            success=True,
            content=cast(list[JsonValue], [numbered]),
            structured_content=metadata,
        )
