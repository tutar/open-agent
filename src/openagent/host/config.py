"""Host configuration models."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class OpenAgentHostConfig:
    session_root: str
    binding_root: str
    terminal_host: str = "127.0.0.1"
    terminal_port: int = 8765
    data_root: str = field(default_factory=lambda: str(Path(".openagent") / "data"))
    model_io_root: str = field(
        default_factory=lambda: str(Path(".openagent") / "data" / "model-io")
    )
    workspace_root: str = field(default_factory=os.getcwd)
    preload_channels: tuple[str, ...] = ()

    @classmethod
    def from_env(
        cls,
        preload_channels: Iterable[str] = (),
    ) -> OpenAgentHostConfig:
        root = Path(os.getenv("OPENAGENT_HOST_ROOT", str(Path(".openagent") / "host")))
        data_root = os.getenv("OPENAGENT_DATA_ROOT", str(root.parent / "data"))
        model_io_root = os.getenv("OPENAGENT_MODEL_IO_ROOT", str(Path(data_root) / "model-io"))
        session_root = os.getenv("OPENAGENT_SESSION_ROOT", str(root / "sessions"))
        binding_root = os.getenv("OPENAGENT_BINDING_ROOT", str(root / "bindings"))
        workspace_root = os.getenv("OPENAGENT_WORKSPACE_ROOT", os.getcwd())
        terminal_host = os.getenv("OPENAGENT_TERMINAL_HOST", "127.0.0.1")
        terminal_port = int(os.getenv("OPENAGENT_TERMINAL_PORT", "8765"))
        return cls(
            session_root=session_root,
            binding_root=binding_root,
            data_root=data_root,
            model_io_root=model_io_root,
            workspace_root=workspace_root,
            terminal_host=terminal_host,
            terminal_port=terminal_port,
            preload_channels=tuple(preload_channels),
        )
