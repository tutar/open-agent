"""Host configuration models."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from openagent.shared import (
    DEFAULT_AGENT_DIRECTORY,
    DEFAULT_ROLE_ID,
    DEFAULT_RUNTIME_AGENT_ID,
    normalize_openagent_root,
    resolve_agent_instance_root,
    resolve_agent_root,
    resolve_roles_root,
    resolve_sessions_root,
)

DEFAULT_OPENAGENT_ROOT = ".openagent"


def _default_host_path(*parts: str) -> str:
    return str(Path(DEFAULT_OPENAGENT_ROOT, *parts))


DEFAULT_AGENT_ROOT = _default_host_path(DEFAULT_AGENT_DIRECTORY)
DEFAULT_DERIVED_ROOT_FIELDS = {
    "session_root": _default_host_path("sessions"),
    "binding_root": _default_host_path("sessions"),
    "data_root": _default_host_path("data"),
    "role_root": _default_host_path("roles"),
    "model_io_root": str(
        Path(
            DEFAULT_OPENAGENT_ROOT,
            DEFAULT_AGENT_DIRECTORY,
            DEFAULT_RUNTIME_AGENT_ID,
            "model-io",
        )
    ),
}


@dataclass(slots=True)
class OpenAgentHostConfig:
    openagent_root: str = DEFAULT_OPENAGENT_ROOT
    agent_root: str = DEFAULT_AGENT_ROOT
    session_root: str = DEFAULT_DERIVED_ROOT_FIELDS["session_root"]
    binding_root: str = DEFAULT_DERIVED_ROOT_FIELDS["binding_root"]
    terminal_host: str = "127.0.0.1"
    terminal_port: int = 8765
    data_root: str = DEFAULT_DERIVED_ROOT_FIELDS["data_root"]
    role_root: str = DEFAULT_DERIVED_ROOT_FIELDS["role_root"]
    model_io_root: str = DEFAULT_DERIVED_ROOT_FIELDS["model_io_root"]
    preload_channels: tuple[str, ...] = ()
    role_id: str = DEFAULT_ROLE_ID

    def __post_init__(self) -> None:
        self.openagent_root = normalize_openagent_root(self.openagent_root)

        if self.agent_root == DEFAULT_AGENT_ROOT:
            self.agent_root = resolve_agent_root(self.openagent_root)

        if self.session_root == DEFAULT_DERIVED_ROOT_FIELDS["session_root"]:
            self.session_root = resolve_sessions_root(self.openagent_root)
        if self.binding_root == DEFAULT_DERIVED_ROOT_FIELDS["binding_root"]:
            self.binding_root = resolve_sessions_root(self.openagent_root)
        if self.data_root == DEFAULT_DERIVED_ROOT_FIELDS["data_root"]:
            self.data_root = str(Path(self.openagent_root) / "data")
        if self.role_root == DEFAULT_DERIVED_ROOT_FIELDS["role_root"]:
            self.role_root = str(resolve_roles_root(self.openagent_root))
        if self.model_io_root == DEFAULT_DERIVED_ROOT_FIELDS["model_io_root"]:
            self.model_io_root = str(
                resolve_agent_instance_root(self.agent_root, DEFAULT_RUNTIME_AGENT_ID)
                / "model-io"
            )

    @classmethod
    def from_env(
        cls,
        preload_channels: Iterable[str] = (),
    ) -> OpenAgentHostConfig:
        openagent_root = normalize_openagent_root(os.getenv("OPENAGENT_ROOT"))
        role_id = os.getenv("OPENAGENT_ROLE_ID") or DEFAULT_ROLE_ID
        agent_root = resolve_agent_root(openagent_root, role_id)
        terminal_host = os.getenv("OPENAGENT_TERMINAL_HOST", "127.0.0.1")
        terminal_port = int(os.getenv("OPENAGENT_TERMINAL_PORT", "8765"))
        return cls(
            openagent_root=openagent_root,
            agent_root=str(agent_root),
            session_root=resolve_sessions_root(openagent_root),
            binding_root=resolve_sessions_root(openagent_root),
            data_root=str(Path(openagent_root) / "data"),
            role_root=str(resolve_roles_root(openagent_root)),
            model_io_root=str(
                resolve_agent_instance_root(str(agent_root), DEFAULT_RUNTIME_AGENT_ID)
                / "model-io"
            ),
            terminal_host=terminal_host,
            terminal_port=terminal_port,
            preload_channels=tuple(preload_channels),
            role_id=role_id,
        )
