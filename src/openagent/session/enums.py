"""Stable session lifecycle enums."""

from __future__ import annotations

from enum import StrEnum


class SessionStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    REQUIRES_ACTION = "requires_action"
