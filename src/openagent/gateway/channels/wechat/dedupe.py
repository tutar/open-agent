"""WeChat inbound message dedupe stores."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class InMemoryWechatInboundDedupeStore:
    seen: set[str] = field(default_factory=set)

    def check_and_mark(self, message_id: str) -> bool:
        """Return True when the message was already seen."""

        if message_id in self.seen:
            return True
        self.seen.add(message_id)
        return False


@dataclass(slots=True)
class FileWechatInboundDedupeStore:
    storage_path: str
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def check_and_mark(self, message_id: str) -> bool:
        """Return True when the message was already seen."""

        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            seen = self._load(path)
            if message_id in seen:
                return True
            seen.add(message_id)
            path.write_text(
                json.dumps(sorted(seen), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return False

    def _load(self, path: Path) -> set[str]:
        if not path.exists():
            return set()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set()
        if not isinstance(payload, list):
            return set()
        return {str(item) for item in payload if isinstance(item, str)}
