"""OpenClaw-compatible dreaming state storage."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from openagent.durable_memory.dreaming.models import DreamingPhase, ShortTermRecallEntry
from openagent.object_model import JsonObject


class DreamingStateStore:
    """Persist machine-readable dreaming state under ``memory/.dreams``."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.dreams_dir = self.root / "memory" / ".dreams"
        self.dreams_dir.mkdir(parents=True, exist_ok=True)

    @property
    def short_term_path(self) -> Path:
        return self.dreams_dir / "short-term-recall.json"

    @property
    def phase_signals_path(self) -> Path:
        return self.dreams_dir / "phase-signals.json"

    @property
    def lock_path(self) -> Path:
        return self.dreams_dir / "dreaming.lock"

    def read_short_term_entries(self) -> list[ShortTermRecallEntry]:
        data = self._read_json_list(self.short_term_path)
        return [
            ShortTermRecallEntry.from_dict(cast(JsonObject, item))
            for item in data
            if isinstance(item, dict)
        ]

    def write_short_term_entries(self, entries: list[ShortTermRecallEntry]) -> None:
        self._write_json(
            self.short_term_path,
            [entry.to_dict() for entry in sorted(entries, key=lambda item: item.key)],
        )

    def upsert_short_term_entries(self, entries: list[ShortTermRecallEntry]) -> None:
        current = {entry.key: entry for entry in self.read_short_term_entries()}
        for entry in entries:
            existing = current.get(entry.key)
            if existing is None:
                current[entry.key] = entry
                continue
            existing.recall_count += entry.recall_count
            existing.relevance_total += entry.relevance_total
            existing.grounded_count += entry.grounded_count
            existing.daily_count += entry.daily_count
            existing.query_hashes = sorted(set([*existing.query_hashes, *entry.query_hashes]))
            existing.recall_days = sorted(set([*existing.recall_days, *entry.recall_days]))
            existing.concept_tags = sorted(set([*existing.concept_tags, *entry.concept_tags]))
            existing.updated_at = entry.updated_at
        self.write_short_term_entries(list(current.values()))

    def read_phase_signals(self) -> dict[str, JsonObject]:
        data = self._read_json_object(self.phase_signals_path)
        return {
            key: value
            for key, value in data.items()
            if isinstance(value, dict)
        }

    def record_phase_signals(
        self,
        phase: DreamingPhase,
        keys: list[str],
        day: str,
    ) -> None:
        signals = self.read_phase_signals()
        phase_key = phase.value
        for key in keys:
            item = dict(signals.get(key, {}))
            raw_phase_signal = item.get(phase_key, {})
            phase_signal = dict(raw_phase_signal) if isinstance(raw_phase_signal, dict) else {}
            phase_signal["count"] = _int_value(phase_signal.get("count", 0)) + 1
            phase_signal["last_seen_day"] = day
            item[phase_key] = phase_signal
            signals[key] = item
        self._write_json(self.phase_signals_path, signals)

    def read_checkpoint(self, name: str) -> JsonObject:
        return self._read_json_object(self.dreams_dir / f"{name}.json")

    def write_checkpoint(self, name: str, payload: JsonObject) -> None:
        self._write_json(self.dreams_dir / f"{name}.json", payload)

    def is_locked(self) -> bool:
        return self.lock_path.exists()

    @contextmanager
    def acquire_lock(self) -> Iterator[None]:
        if self.lock_path.exists():
            raise RuntimeError("dreaming state is locked")
        self.lock_path.write_text("locked\n", encoding="utf-8")
        try:
            yield
        finally:
            if self.lock_path.exists():
                self.lock_path.unlink()

    def _read_json_list(self, path: Path) -> list[object]:
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        return data

    def _read_json_object(self, path: Path) -> JsonObject:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return cast(JsonObject, data)

    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temp_path.replace(path)


def _int_value(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float | str):
        return int(value)
    return 0
