"""Local sandbox baseline with configurable allowed command prefixes."""

from __future__ import annotations

from dataclasses import dataclass, field
from subprocess import CompletedProcess, run

from openagent.sandbox.models import (
    SandboxCapabilityView,
    SandboxExecutionRequest,
    SandboxExecutionResult,
)


@dataclass(slots=True)
class LocalSandbox:
    """Minimal sandbox adapter for trusted local execution."""

    allowed_command_prefixes: list[str] = field(default_factory=list)
    supports_network: bool = False
    supports_filesystem_write: bool = False

    def execute(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        self._assert_allowed(request.command)
        completed: CompletedProcess[str] = run(  # noqa: S603
            request.command,
            capture_output=True,
            cwd=request.cwd,
            env=(
                None if not request.env else {key: str(value) for key, value in request.env.items()}
            ),
            text=True,
            check=False,
        )
        return SandboxExecutionResult(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def describe_capabilities(self) -> SandboxCapabilityView:
        return SandboxCapabilityView(
            supports_network=self.supports_network,
            supports_filesystem_write=self.supports_filesystem_write,
            allowed_command_prefixes=self.allowed_command_prefixes,
        )

    def _assert_allowed(self, command: list[str]) -> None:
        if not command:
            raise ValueError("Sandbox command cannot be empty")

        if not self.allowed_command_prefixes:
            return

        for prefix in self.allowed_command_prefixes:
            if command[0] == prefix:
                return

        raise PermissionError(f"Command is not allowed by sandbox policy: {command[0]}")
