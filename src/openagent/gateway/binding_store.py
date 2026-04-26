"""Binding store implementations for the gateway."""

from __future__ import annotations

import json
from pathlib import Path

from .models import SessionBinding


class FileSessionBindingStore:
    """Persist bindings under the owning session directory."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def save_binding(self, binding: SessionBinding) -> None:
        path = self._binding_path(
            binding.session_id,
            str(binding.channel_identity["channel_type"]),
            binding.conversation_id,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(binding.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def load_binding(self, channel_type: str, conversation_id: str) -> SessionBinding | None:
        path = self._find_binding_path(channel_type, conversation_id)
        if path is None:
            return None
        return SessionBinding.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def _find_binding_path(self, channel_type: str, conversation_id: str) -> Path | None:
        filename = self._binding_filename(channel_type, conversation_id)
        direct = self._root / filename
        if direct.exists():
            return direct
        for path in sorted(self._root.glob(f"*/bindings/{filename}")):
            return path
        return None

    def _binding_path(self, session_id: str, channel_type: str, conversation_id: str) -> Path:
        filename = self._binding_filename(channel_type, conversation_id)
        return self._root / session_id / "bindings" / filename

    def _binding_filename(self, channel_type: str, conversation_id: str) -> str:
        safe_name = f"{channel_type}__{conversation_id}".replace("/", "_")
        return f"{safe_name}.json"
