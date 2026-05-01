"""Workspace-bound content search tool."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import cast

from openagent.object_model import JsonValue, ToolResult, text_block, tool_reference_block
from openagent.tools.GrepTool.prompt import DESCRIPTION, GREP_TOOL_NAME
from openagent.tools.models import ToolExecutionContext
from openagent.tools.tool_base import BuiltinToolBase
from openagent.tools.tool_paths import effective_root, resolve_path
from openagent.tools.tool_schema import (
    boolean_property,
    integer_property,
    object_schema,
    string_property,
)
from openagent.tools.tool_validation import (
    require_bool_field,
    require_non_negative_int_field,
    require_positive_int_field,
    require_string_field,
)


class GrepTool(BuiltinToolBase):
    root: str = "."
    default_head_limit: int = 250
    output_modes: tuple[str, ...] = ("content", "files_with_matches", "count")

    def __init__(self, root: str = ".") -> None:
        super().__init__(
            name=GREP_TOOL_NAME,
            description_text=DESCRIPTION,
            input_schema=object_schema(
                {
                    "pattern": string_property(
                        "The regular expression pattern to search for in file contents.",
                        examples=["TODO", "log.*Error", "function\\s+\\w+"],
                    ),
                    "path": string_property(
                        "File or directory to search in. Defaults to the current workspace root.",
                        examples=["src", "tests", "src/app.py"],
                    ),
                    "glob": string_property(
                        "Glob pattern to filter files, for example *.js or **/*.tsx.",
                        examples=["*.py", "**/*.tsx", "src/**/*.ts"],
                    ),
                    "output_mode": string_property(
                        'Output mode: "content" shows matching lines, "files_with_matches" '
                        'shows only file paths, and "count" shows match counts. Defaults '
                        'to "files_with_matches".',
                        examples=["content", "files_with_matches", "count"],
                        enum=list(self.output_modes),
                    ),
                    "-B": integer_property(
                        "Number of lines to show before each match. Requires output_mode "
                        '"content".',
                        examples=[1, 3],
                        minimum=0,
                    ),
                    "-A": integer_property(
                        "Number of lines to show after each match. Requires output_mode "
                        '"content".',
                        examples=[1, 3],
                        minimum=0,
                    ),
                    "-C": integer_property(
                        "Number of lines to show before and after each match. Alias for "
                        "context.",
                        examples=[2, 4],
                        minimum=0,
                    ),
                    "context": integer_property(
                        "Number of lines to show before and after each match. Requires "
                        'output_mode "content".',
                        examples=[2, 5],
                        minimum=0,
                    ),
                    "-n": boolean_property(
                        "Show line numbers in output. Requires output_mode content and "
                        "defaults to true."
                    ),
                    "-i": boolean_property("Case insensitive search."),
                    "type": string_property(
                        "File type to search, for example js, py, rust, or go.",
                        examples=["js", "py", "rust"],
                    ),
                    "head_limit": integer_property(
                        "Limit output to the first N lines or entries. Defaults to 250 "
                        "when unspecified. Pass 0 for unlimited use sparingly.",
                        examples=[20, 100, 0],
                        minimum=0,
                    ),
                    "offset": integer_property(
                        "Skip the first N lines or entries before applying head_limit.",
                        examples=[0, 20],
                        minimum=0,
                    ),
                    "multiline": boolean_property(
                        "Enable multiline mode where patterns can span lines."
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
        if "output_mode" in normalized:
            output_mode = require_string_field(normalized, "output_mode")
            if output_mode not in self.output_modes:
                raise ValueError(
                    f"output_mode must be one of {', '.join(self.output_modes)}"
                )
            normalized["output_mode"] = output_mode
        if "type" in normalized:
            normalized["type"] = require_string_field(normalized, "type")
        if "head_limit" in normalized:
            normalized["head_limit"] = require_non_negative_int_field(normalized, "head_limit")
        if "limit" in normalized:
            normalized["limit"] = require_positive_int_field(normalized, "limit")
        if "offset" in normalized:
            normalized["offset"] = require_non_negative_int_field(normalized, "offset")
        for field_name in ("-A", "-B", "-C", "context"):
            if field_name in normalized:
                normalized[field_name] = require_non_negative_int_field(normalized, field_name)
        for field_name in ("-n", "-i", "multiline"):
            if field_name in normalized:
                normalized[field_name] = require_bool_field(normalized, field_name)
        if "context" in normalized and "-C" in normalized:
            normalized.pop("-C")
        if "context" in normalized or "-C" in normalized:
            normalized.pop("-A", None)
            normalized.pop("-B", None)
        return normalized

    def call(
        self,
        arguments: dict[str, object],
        context: ToolExecutionContext | None = None,
    ) -> ToolResult:
        root = Path(effective_root(self.root, context))
        pattern = str(arguments["pattern"])
        output_mode = cast(str, arguments.get("output_mode", "files_with_matches"))
        glob_filter = cast(str | None, arguments.get("glob"))
        type_filter = cast(str | None, arguments.get("type"))
        head_limit_raw = arguments.get("head_limit", arguments.get("limit"))
        head_limit = (
            self.default_head_limit
            if head_limit_raw is None
            else int(cast(int, head_limit_raw))
        )
        offset = int(arguments.get("offset", 0))
        search_target = self._resolve_search_target(root, cast(str | None, arguments.get("path")))
        relative_target = (
            str(search_target.relative_to(root))
            if search_target != root
            else "."
        )
        raw_results = self._run_ripgrep(
            pattern=pattern,
            root=root,
            target=relative_target,
            glob_filter=glob_filter,
            type_filter=type_filter,
            output_mode=output_mode,
            context_before=cast(int | None, arguments.get("-B")),
            context_after=cast(int | None, arguments.get("-A")),
            context_value=cast(int | None, arguments.get("context")),
            context_alias=cast(int | None, arguments.get("-C")),
            show_line_numbers=cast(bool, arguments.get("-n", True)),
            case_insensitive=cast(bool, arguments.get("-i", False)),
            multiline=cast(bool, arguments.get("multiline", False)),
        )
        results, truncated = self._apply_window(raw_results, head_limit, offset, output_mode)
        if output_mode in {"files_with_matches", "count"}:
            results = sorted(results)
        result_content = self._result_content_for_mode(output_mode, results)
        return ToolResult(
            tool_name=self.name,
            success=True,
            content=cast(list[JsonValue], result_content),
            structured_content={
                "mode": output_mode,
                "search_root": relative_target,
                "glob": glob_filter,
                "type": type_filter,
                "head_limit": None if head_limit == 0 else head_limit,
                "applied_limit": None if head_limit == 0 else head_limit,
                "offset": offset,
                "applied_offset": offset,
                "count": len(results),
                "truncated": truncated,
            },
            truncated=truncated,
        )

    def _resolve_search_target(self, root: Path, raw_path: str | None) -> Path:
        if raw_path is None or not raw_path.strip():
            return root
        candidate = resolve_path(str(root), raw_path)
        if not candidate.exists():
            raise FileNotFoundError(f"search path does not exist: {candidate}")
        return candidate

    def _run_ripgrep(
        self,
        *,
        pattern: str,
        root: Path,
        target: str,
        glob_filter: str | None,
        type_filter: str | None,
        output_mode: str,
        context_before: int | None,
        context_after: int | None,
        context_value: int | None,
        context_alias: int | None,
        show_line_numbers: bool,
        case_insensitive: bool,
        multiline: bool,
    ) -> list[str]:
        args = ["rg", "--color", "never", "--no-heading"]
        if multiline:
            args.extend(["-U", "--multiline-dotall"])
        if case_insensitive:
            args.append("-i")
        if output_mode == "files_with_matches":
            args.append("-l")
        elif output_mode == "count":
            args.append("-c")
        if output_mode == "content":
            if show_line_numbers:
                args.append("-n")
            if context_value is not None:
                args.extend(["-C", str(context_value)])
            elif context_alias is not None:
                args.extend(["-C", str(context_alias)])
            else:
                if context_before is not None:
                    args.extend(["-B", str(context_before)])
                if context_after is not None:
                    args.extend(["-A", str(context_after)])
        if pattern.startswith("-"):
            args.extend(["-e", pattern])
        else:
            args.append(pattern)
        if type_filter:
            args.extend(["--type", type_filter])
        if glob_filter:
            for glob_pattern in self._split_glob_patterns(glob_filter):
                args.extend(["--glob", glob_pattern])
        args.append(target)
        try:
            completed = subprocess.run(
                args,
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "ripgrep executable `rg` is required for GrepTool but was not found"
            ) from exc
        if completed.returncode not in {0, 1}:
            stderr = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"ripgrep failed: {stderr or f'exit code {completed.returncode}'}")
        output = completed.stdout.rstrip("\n")
        if not output:
            return []
        return output.splitlines()

    def _apply_window(
        self,
        results: list[str],
        head_limit: int,
        offset: int,
        output_mode: str,
    ) -> tuple[list[str], bool]:
        if output_mode == "content":
            filtered = [self._normalize_output_line(line) for line in results if line != "--"]
        else:
            filtered = [self._normalize_output_line(line) for line in results]
        if offset > len(filtered):
            return ([], False)
        if head_limit == 0:
            return (filtered[offset:], False)
        windowed = filtered[offset : offset + head_limit]
        truncated = len(filtered) - offset > head_limit
        return (windowed, truncated)

    def _split_glob_patterns(self, raw_glob: str) -> list[str]:
        patterns: list[str] = []
        for token in raw_glob.split():
            if "{" in token and "}" in token:
                patterns.append(token)
                continue
            patterns.extend(part for part in token.split(",") if part)
        return patterns

    def _normalize_output_line(self, line: str) -> str:
        if line.startswith("./"):
            return line[2:]
        return line

    def _result_content_for_mode(
        self,
        output_mode: str,
        results: list[str],
    ) -> list[JsonValue]:
        if not results:
            return [text_block("No matches found")]
        if output_mode == "files_with_matches":
            return [
                text_block(f"Found {len(results)} matching files"),
                *[
                    tool_reference_block(ref=item, title=item, preview=item, ref_kind="file")
                    for item in results
                ],
            ]
        return [text_block(item) for item in results]
