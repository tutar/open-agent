"""Storage backends for local task state, events, output, and retention."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import cast

from openagent.object_model import JsonObject, JsonValue, TaskEvent, TaskRecord


class InMemoryTaskStorage:
    """In-memory task storage for local tests and ephemeral runtimes."""

    def __init__(self) -> None:
        self.tasks: dict[str, TaskRecord] = {}
        self.events: dict[str, list[TaskEvent]] = defaultdict(list)
        self.outputs: dict[str, list[JsonValue]] = defaultdict(list)
        self.observers: dict[str, set[str]] = defaultdict(set)
        self.counter = 0

    def next_task_id(self) -> str:
        self.counter += 1
        return f"task_{self.counter}"


class FileTaskStorage:
    """File-backed task storage for restart-safe local task workflows."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.tasks_dir = self.root / "tasks"
        self.events_dir = self.root / "events"
        self.outputs_dir = self.root / "outputs"
        self.observers_dir = self.root / "observers"
        self.counter_file = self.root / "counter.txt"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.events_dir.mkdir(parents=True, exist_ok=True)
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        self.observers_dir.mkdir(parents=True, exist_ok=True)

    def next_task_id(self) -> str:
        current = 0
        if self.counter_file.exists():
            current = int(self.counter_file.read_text(encoding="utf-8").strip() or "0")
        current += 1
        self.counter_file.write_text(str(current), encoding="utf-8")
        return f"task_{current}"

    def task_path(self, task_id: str) -> Path:
        return self.tasks_dir / f"{task_id}.json"

    def events_path(self, task_id: str) -> Path:
        return self.events_dir / f"{task_id}.json"

    def outputs_path(self, task_id: str) -> Path:
        return self.outputs_dir / f"{task_id}.json"

    def observers_path(self, task_id: str) -> Path:
        return self.observers_dir / f"{task_id}.json"

    def read_task(self, task_id: str) -> TaskRecord:
        payload = self._read_json(self.task_path(task_id))
        return TaskRecord.from_dict(payload)

    def write_task(self, record: TaskRecord) -> None:
        self.task_path(record.task_id).write_text(
            json.dumps(record.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def list_tasks(self) -> list[TaskRecord]:
        return [self.read_task(path.stem) for path in sorted(self.tasks_dir.glob("*.json"))]

    def read_events(self, task_id: str) -> list[TaskEvent]:
        path = self.events_path(task_id)
        if not path.exists():
            return []
        payload = self._read_json(path)
        events = payload.get("events")
        if not isinstance(events, list):
            return []
        return [TaskEvent.from_dict(item) for item in events if isinstance(item, dict)]

    def write_events(self, task_id: str, events: list[TaskEvent]) -> None:
        self.events_path(task_id).write_text(
            json.dumps({"events": [event.to_dict() for event in events]}, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def read_outputs(self, task_id: str) -> list[JsonValue]:
        path = self.outputs_path(task_id)
        if not path.exists():
            return []
        payload = self._read_json(path)
        items = payload.get("items")
        return list(items) if isinstance(items, list) else []

    def write_outputs(self, task_id: str, items: list[JsonValue]) -> None:
        self.outputs_path(task_id).write_text(
            json.dumps({"items": items}, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def read_observers(self, task_id: str) -> set[str]:
        path = self.observers_path(task_id)
        if not path.exists():
            return set()
        payload = self._read_json(path)
        bindings = payload.get("bindings")
        if not isinstance(bindings, list):
            return set()
        return {str(item) for item in bindings}

    def write_observers(self, task_id: str, bindings: set[str]) -> None:
        self.observers_path(task_id).write_text(
            json.dumps({"bindings": sorted(bindings)}, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def remove_task_state(self, task_id: str, *, remove_output: bool) -> None:
        self.task_path(task_id).unlink(missing_ok=True)
        self.events_path(task_id).unlink(missing_ok=True)
        self.observers_path(task_id).unlink(missing_ok=True)
        if remove_output:
            self.outputs_path(task_id).unlink(missing_ok=True)

    def _read_json(self, path: Path) -> JsonObject:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Expected JSON object in {path}")
        return cast(JsonObject, payload)
