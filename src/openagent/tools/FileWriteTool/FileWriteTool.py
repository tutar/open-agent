"""Workspace-bound file writer."""

from __future__ import annotations

from typing import cast

from openagent.object_model import JsonValue, ToolResult
from openagent.tools.FileWriteTool.prompt import DESCRIPTION, WRITE_TOOL_NAME
from openagent.tools.models import ToolExecutionContext
from openagent.tools.tool_base import BuiltinToolBase
from openagent.tools.tool_paths import effective_root, resolve_path
from openagent.tools.tool_schema import object_schema, string_property


class FileWriteTool(BuiltinToolBase):
    root: str = "."

    def __init__(self, root: str = ".") -> None:
        super().__init__(
            name=WRITE_TOOL_NAME,
            description_text=DESCRIPTION,
            input_schema=object_schema(
                {
                    "path": string_property(
                        "Path to the file to create or overwrite, relative to the current "
                        "workspace root.",
                        examples=["notes/todo.txt", "tmp/output.json"],
                    ),
                    "content": string_property(
                        "Full file contents to write.",
                        examples=["hello world\n", '{"ok": true}\n'],
                    ),
                },
                required=["path", "content"],
            ),
            aliases=["write"],
        )
        self.root = root

    def call(
        self,
        arguments: dict[str, object],
        context: ToolExecutionContext | None = None,
    ) -> ToolResult:
        path = resolve_path(effective_root(self.root, context), str(arguments["path"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        content = str(arguments.get("content", ""))
        existed = path.exists()
        path.write_text(content, encoding="utf-8")
        return ToolResult(
            tool_name=self.name,
            success=True,
            content=cast(
                list[JsonValue],
                [
                    f"{'Updated' if existed else 'Created'} {arguments['path']}"
                ],
            ),
            structured_content={
                "path": str(path),
                "operation": "update" if existed else "create",
                "bytes_written": len(content.encode("utf-8")),
            },
        )
