"""Shared package utilities and constants."""

from openagent.shared.layout import (
    DEFAULT_AGENT_DIRECTORY,
    ensure_directory,
    ensure_session_workspace,
    ensure_subagent_workspace,
    normalize_openagent_root,
    resolve_agent_directory,
    resolve_agent_root,
    resolve_agent_root_from_session_root,
    resolve_session_workspace,
    resolve_subagent_root,
    resolve_subagent_workspace,
    write_subagent_ref,
)
from openagent.shared.paths import resolve_path_env
from openagent.shared.version import SPEC_VERSION, __version__

__all__ = [
    "DEFAULT_AGENT_DIRECTORY",
    "SPEC_VERSION",
    "__version__",
    "ensure_directory",
    "ensure_session_workspace",
    "ensure_subagent_workspace",
    "normalize_openagent_root",
    "resolve_path_env",
    "resolve_agent_directory",
    "resolve_agent_root",
    "resolve_agent_root_from_session_root",
    "resolve_session_workspace",
    "resolve_subagent_root",
    "resolve_subagent_workspace",
    "write_subagent_ref",
]
