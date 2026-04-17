"""Feishu inbound deduplication stores."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from time import time


@dataclass(slots=True)
class InMemoryFeishuInboundDedupeStore:
    """Simple in-memory dedupe store keyed by Feishu message id."""

    ttl_seconds: float = 6 * 60 * 60
    _seen: dict[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def check_and_mark(self, message_id: str | None) -> bool:
        """Return True when the message was already seen, else record it."""

        if message_id is None or not message_id.strip():
            return False
        now = time()
        with self._lock:
            self._prune(now)
            if message_id in self._seen:
                return True
            self._seen[message_id] = now
            return False

    def _prune(self, now: float) -> None:
        cutoff = now - self.ttl_seconds
        stale_keys = [key for key, seen_at in self._seen.items() if seen_at < cutoff]
        for key in stale_keys:
            self._seen.pop(key, None)


@dataclass(slots=True)
class FileFeishuInboundDedupeStore:
    """File-backed dedupe store for restart-safe short-term idempotency."""

    storage_path: str
    ttl_seconds: float = 6 * 60 * 60
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def check_and_mark(self, message_id: str | None) -> bool:
        """Return True when the message was already seen, else persist it."""

        if message_id is None or not message_id.strip():
            return False
        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        now = time()
        with self._lock:
            state = self._load(path)
            cutoff = now - self.ttl_seconds
            state = {
                key: value
                for key, value in state.items()
                if isinstance(value, (int, float)) and float(value) >= cutoff
            }
            if message_id in state:
                self._save(path, state)
                return True
            state[message_id] = now
            self._save(path, state)
            return False

    def _load(self, path: Path) -> dict[str, float]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        state: dict[str, float] = {}
        for key, value in payload.items():
            if isinstance(key, str) and isinstance(value, (int, float)):
                state[key] = float(value)
        return state

    def _save(self, path: Path, state: dict[str, float]) -> None:
        path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
