"""Helpers for resolving OpenAgent local state layout."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

DEFAULT_AGENT_DIRECTORY = "agent_default"
DEFAULT_RUNTIME_AGENT_ID = "local-agent"
DEFAULT_ROLE_DIRECTORY = "roles"
DEFAULT_ROLE_ID = "default"


def normalize_openagent_root(root: str | None, *, default: str = ".openagent") -> str:
    candidate = root if root is not None else default
    expanded = os.path.expanduser(os.path.expandvars(candidate))
    return str(Path(expanded).resolve())


def resolve_agent_directory(role_id: str | None) -> str:
    if isinstance(role_id, str) and role_id.strip():
        return f"agent_{role_id.strip()}"
    return DEFAULT_AGENT_DIRECTORY


def resolve_agent_root(openagent_root: str, role_id: str | None = None) -> str:
    return str(Path(openagent_root).resolve() / resolve_agent_directory(role_id))


def resolve_roles_root(openagent_root: str) -> Path:
    return Path(openagent_root).resolve() / DEFAULT_ROLE_DIRECTORY


def resolve_role_root(openagent_root: str, role_id: str | None = None) -> Path:
    resolved_role_id = (
        role_id.strip()
        if isinstance(role_id, str) and role_id.strip()
        else DEFAULT_ROLE_ID
    )
    return resolve_roles_root(openagent_root) / resolved_role_id


def resolve_sessions_root(openagent_root: str) -> str:
    return str(Path(openagent_root).resolve() / "sessions")


def resolve_cards_root(openagent_root: str, channel: str | None = None) -> str:
    root = Path(openagent_root).resolve() / "cards"
    if isinstance(channel, str) and channel.strip():
        root = root / channel.strip()
    return str(root)


def resolve_agent_root_from_session_root(session_root: str, role_id: str | None = None) -> str:
    session_path = Path(session_root).resolve()
    if session_path.name != "sessions":
        return str(session_path)
    parent = session_path.parent
    if parent.name.startswith("agent_"):
        return str(parent)
    return resolve_agent_root(str(parent), role_id)


def resolve_session_root(session_root: str, session_id: str) -> Path:
    return Path(session_root).resolve() / session_id


def resolve_session_workspace(session_root: str, session_id: str) -> str:
    return str(resolve_session_root(session_root, session_id) / "workspace")


def resolve_agent_instance_root(agent_root: str, agent_id: str | None = None) -> Path:
    resolved_agent_id = agent_id or DEFAULT_RUNTIME_AGENT_ID
    return Path(agent_root).resolve() / resolved_agent_id


def resolve_agent_workspace(agent_root: str, agent_id: str | None = None) -> str:
    return str(resolve_agent_instance_root(agent_root, agent_id) / "workspace")


def resolve_agent_transcript_path(agent_root: str, agent_id: str | None = None) -> Path:
    return resolve_agent_instance_root(agent_root, agent_id) / "transcript.jsonl"


def resolve_agent_plugins_root(agent_root: str, agent_id: str | None = None) -> Path:
    return resolve_agent_instance_root(agent_root, agent_id) / "plugins"


def resolve_subagent_root(agent_root: str, subagent_id: str) -> Path:
    return resolve_agent_instance_root(agent_root, subagent_id)


def resolve_subagent_workspace(agent_root: str, subagent_id: str) -> str:
    return str(resolve_subagent_root(agent_root, subagent_id) / "workspace")


def ensure_directory(path: str | Path) -> str:
    resolved = Path(path).resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return str(resolved)


def ensure_session_workspace(session_root: str, session_id: str) -> str:
    return ensure_directory(resolve_session_workspace(session_root, session_id))


def ensure_agent_workspace(
    agent_root: str,
    agent_id: str | None = None,
    *,
    seed_workspace: str | None = None,
) -> str:
    workspace = Path(resolve_agent_workspace(agent_root, agent_id))
    if not workspace.exists():
        workspace.parent.mkdir(parents=True, exist_ok=True)
        if seed_workspace is not None and Path(seed_workspace).exists():
            shutil.copytree(seed_workspace, workspace)
        else:
            workspace.mkdir(parents=True, exist_ok=True)
    return str(workspace.resolve())


def ensure_agent_plugins_root(
    agent_root: str,
    agent_id: str | None = None,
) -> str:
    return ensure_directory(resolve_agent_plugins_root(agent_root, agent_id))


def ensure_subagent_workspace(
    agent_root: str,
    subagent_id: str,
    *,
    parent_workspace: str | None = None,
) -> str:
    return ensure_agent_workspace(
        agent_root,
        subagent_id,
        seed_workspace=parent_workspace,
    )


def write_subagent_ref(
    agent_root: str,
    subagent_id: str,
    *,
    parent_session_id: str | None,
    workspace: str,
    metadata: dict[str, object] | None = None,
    parent_agent_id: str | None = None,
) -> None:
    root = resolve_subagent_root(agent_root, subagent_id)
    root.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "subagent_id": subagent_id,
        "parent_agent_id": parent_agent_id or DEFAULT_RUNTIME_AGENT_ID,
        "parent_session_id": parent_session_id,
        "workspace": str(Path(workspace).resolve()),
        "metadata": metadata or {},
    }
    (root / "parent_agent").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    index_path = resolve_agent_instance_root(agent_root, parent_agent_id) / "subagents"
    children: dict[str, dict[str, object]] = {}
    if index_path.exists():
        raw = json.loads(index_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            children = {
                str(key): value for key, value in raw.items() if isinstance(value, dict)
            }
    children[subagent_id] = payload
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(children, indent=2), encoding="utf-8")
