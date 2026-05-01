"""Workspace-bound text replacement editor."""

from __future__ import annotations

from typing import cast

from openagent.object_model import JsonValue, ToolResult
from openagent.tools.FileEditTool.prompt import DESCRIPTION, EDIT_TOOL_NAME
from openagent.tools.models import ToolExecutionContext
from openagent.tools.tool_base import BuiltinToolBase
from openagent.tools.tool_paths import effective_root, resolve_path
from openagent.tools.tool_schema import boolean_property, object_schema, string_property


class FileEditTool(BuiltinToolBase):
    root: str = "."

    def __init__(self, root: str = ".") -> None:
        super().__init__(
            name=EDIT_TOOL_NAME,
            description_text=DESCRIPTION,
            input_schema=object_schema(
                {
                    "path": string_property(
                        "Path to the file to edit, relative to the current workspace root.",
                        examples=["sympy/printing/defaults.py"],
                    ),
                    "old": string_property(
                        "Exact text to replace. Read the file first and copy the target text exactly.",
                        examples=["DefaultPrinting = Printable"],
                    ),
                    "new": string_property(
                        "Replacement text.",
                        examples=["DefaultPrinting = Printable\nPrintable.__slots__ = ()"],
                    ),
                    "replace_all": boolean_property(
                        "When true, replace every exact occurrence. When false or omitted, "
                        "the old text must match exactly once."
                    ),
                },
                required=["path", "old", "new"],
            ),
            aliases=["edit"],
        )
        self.root = root

    def call(
        self,
        arguments: dict[str, object],
        context: ToolExecutionContext | None = None,
    ) -> ToolResult:
        path = resolve_path(effective_root(self.root, context), str(arguments["path"]))
        old = str(arguments.get("old", ""))
        new = str(arguments.get("new", ""))
        replace_all = bool(arguments.get("replace_all", False))
        if not old:
            raise ValueError("old must be a non-empty string")
        content = path.read_text(encoding="utf-8")
        occurrences = content.count(old)
        if occurrences == 0:
            raise ValueError("target text not found")
        if occurrences > 1 and not replace_all:
            raise ValueError(
                "target text matched multiple locations; provide more specific old text or "
                "set replace_all=true"
            )
        updated = content.replace(old, new) if replace_all else content.replace(old, new, 1)
        path.write_text(updated, encoding="utf-8")
        return ToolResult(
            tool_name=self.name,
            success=True,
            content=cast(
                list[JsonValue],
                [
                    f"Edited {path.name} with {occurrences if replace_all else 1} replacement"
                    f"{'' if (replace_all and occurrences == 1) or (not replace_all) else 's'}"
                ],
            ),
            structured_content={
                "path": str(path),
                "replacements": occurrences if replace_all else 1,
                "replace_all": replace_all,
            },
        )
