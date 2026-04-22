"""Runtime terminal control, errors, and protocols."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Protocol

from openagent.object_model import RuntimeEvent, TerminalState


@dataclass(slots=True)
class TurnControl:
    timeout_seconds: float | None = None
    max_retries: int = 0
    cancellation_check: Callable[[], bool] | None = None


class AgentRuntime(Protocol):
    def run_turn_stream(
        self,
        input: str,
        session_handle: str,
        control: TurnControl | None = None,
    ) -> Iterator[RuntimeEvent]:
        """Advance the current turn state machine as an event stream."""

    def continue_turn(
        self,
        session_handle: str,
        approved: bool,
    ) -> tuple[list[RuntimeEvent], TerminalState]:
        """Resume a previously blocked turn after a host decision."""


class CancelledTurn(Exception):
    """Raised when cooperative cancellation stops the current turn."""


class TimedOutTurn(Exception):
    """Raised when the configured turn timeout expires."""


class RetryExhaustedTurn(Exception):
    """Raised when model retries are exhausted."""
