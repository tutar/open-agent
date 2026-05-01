"""Workspace-bound shell execution tool."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from subprocess import TimeoutExpired
from typing import cast

from openagent.object_model import JsonValue, ToolResult
from openagent.tools.BashTool.prompt import BASH_TOOL_NAME, DESCRIPTION
from openagent.tools.models import PermissionDecision, ToolExecutionContext
from openagent.tools.tool_base import BuiltinToolBase
from openagent.tools.tool_io import merge_process_output
from openagent.tools.tool_paths import effective_root
from openagent.tools.tool_schema import integer_property, object_schema, string_property
from openagent.tools.tool_validation import require_positive_int_field, require_string_field


class BashTool(BuiltinToolBase):
    root: str = "."
    default_timeout_ms: int = 60_000
    max_timeout_ms: int = 600_000

    def __init__(self, root: str = ".") -> None:
        super().__init__(
            name=BASH_TOOL_NAME,
            description_text=DESCRIPTION,
            input_schema=object_schema(
                {
                    "command": string_property(
                        "Full shell command to execute in the current workspace root.",
                        examples=["ls -la", "pwd", "pytest -q tests/tools/test_tools_alignment.py"],
                    ),
                    "timeout_ms": integer_property(
                        "Optional timeout in milliseconds for the command.",
                        examples=[1000, 30_000],
                        minimum=1,
                    ),
                },
                required=["command"],
            ),
            aliases=["bash"],
            max_result_size_chars=64_000,
            supports_result_persistence=True,
        )
        self.root = root

    def validate_input(self, arguments: dict[str, object]) -> dict[str, object]:
        normalized = dict(arguments)
        normalized["command"] = require_string_field(normalized, "command")
        if "timeout_ms" in normalized:
            timeout_ms = require_positive_int_field(normalized, "timeout_ms")
            if timeout_ms > self.max_timeout_ms:
                raise ValueError(f"timeout_ms must be <= {self.max_timeout_ms}")
            normalized["timeout_ms"] = timeout_ms
        return normalized

    def check_permissions(
        self,
        arguments: dict[str, object],
        tool_use_context: ToolExecutionContext | None = None,
    ) -> str:
        command = str(arguments.get("command", ""))
        root = effective_root(self.root, tool_use_context)
        return bash_permission_decision(command, root)

    def call(
        self,
        arguments: dict[str, object],
        context: ToolExecutionContext | None = None,
    ) -> ToolResult:
        command = str(arguments["command"])
        root = effective_root(self.root, context)
        timeout_ms = int(arguments.get("timeout_ms", self.default_timeout_ms))
        timeout_seconds = timeout_ms / 1000
        try:
            completed = subprocess.run(
                command,
                cwd=root,
                shell=True,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds,
            )
        except TimeoutExpired as exc:
            raise RuntimeError(f"command timed out after {timeout_ms}ms") from exc
        output = merge_process_output(completed.stdout, completed.stderr)
        if completed.returncode != 0:
            raise RuntimeError(output.strip() or f"command failed with exit code {completed.returncode}")
        return ToolResult(
            tool_name=self.name,
            success=True,
            content=cast(list[JsonValue], [output.rstrip()]),
            structured_content={
                "cwd": root,
                "exit_code": completed.returncode,
                "timeout_ms": timeout_ms,
            },
        )


def bash_permission_decision(command: str, workspace_root: str) -> str:
    workspace = Path(workspace_root).resolve()
    try:
        tokens = shlex.split(command)
    except ValueError:
        return PermissionDecision.ASK.value
    if not tokens:
        return PermissionDecision.ALLOW.value
    destructive = tokens[0] in {"rm", "rmdir", "mv", "chmod", "chown"}
    for token in tokens[1:]:
        if not token or token.startswith("-"):
            continue
        normalized = expand_workspace_token(token, workspace)
        if normalized is None:
            continue
        resolved = command_target_path(workspace, normalized)
        if resolved is None:
            return PermissionDecision.ASK.value
        if resolved == workspace and destructive:
            return PermissionDecision.DENY.value
        try:
            resolved.relative_to(workspace)
        except ValueError:
            return PermissionDecision.ASK.value
    return PermissionDecision.ALLOW.value


def expand_workspace_token(token: str, workspace: Path) -> str | None:
    stripped = token.strip()
    if stripped in {"|", "||", "&&", ";", ">", ">>", "<"}:
        return None
    return (
        stripped.replace("${PWD}", str(workspace))
        .replace("$PWD", str(workspace))
        .replace("~", str(Path.home()), 1)
    )


def command_target_path(workspace: Path, token: str) -> Path | None:
    candidate = token.strip()
    if not candidate:
        return None
    if candidate in {".", "./"}:
        return workspace
    if candidate.startswith("/"):
        return Path(candidate).resolve()
    if candidate.startswith("..") or candidate.startswith("./"):
        return (workspace / candidate).resolve()
    if "/" in candidate:
        return (workspace / candidate).resolve()
    return (workspace / candidate).resolve()
