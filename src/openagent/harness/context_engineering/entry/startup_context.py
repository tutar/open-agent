"""Startup and turn-zero context modeling."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from openagent.object_model import JsonObject, SerializableModel


class StartupContextKind(StrEnum):
    SESSION_START = "session_start"
    AGENT_START = "agent_start"
    TURN_ZERO = "turn_zero"
    RESUME_START = "resume_start"


@dataclass(slots=True)
class StartupContext(SerializableModel):
    kind: StartupContextKind
    payload: JsonObject
    first_use_only: bool = True
    reentry_policy: str = "once"
    transcript_visibility: str = "hidden"
    dedup_policy: str = "once"


def build_startup_contexts(
    *,
    session_id: str,
    has_prior_messages: bool,
    has_pending_action: bool,
    agent_id: str | None = None,
) -> list[StartupContext]:
    contexts: list[StartupContext] = []
    if not has_prior_messages:
        contexts.append(
            StartupContext(
                kind=StartupContextKind.SESSION_START,
                payload={"session_id": session_id},
            )
        )
        contexts.append(
            StartupContext(
                kind=StartupContextKind.TURN_ZERO,
                payload={
                    "content": "This is the first model-visible turn for the current session.",
                },
            )
        )
    if agent_id:
        contexts.append(
            StartupContext(
                kind=StartupContextKind.AGENT_START,
                payload={"agent_id": agent_id},
            )
        )
    if has_pending_action:
        contexts.append(
            StartupContext(
                kind=StartupContextKind.RESUME_START,
                payload={
                    "pending_action": True,
                    "session_id": session_id,
                },
                first_use_only=False,
                reentry_policy="resume",
            )
        )
    return contexts
