"""Feishu reply-card rendering and delivery persistence."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from time import time
from typing import cast
from uuid import uuid4

from openagent.object_model import JsonObject, JsonValue

_STABLE_CARD_STATUSES = {"requires_action", "completed", "failed", "interrupted"}


@dataclass(slots=True)
class FeishuReplyCardRecord:
    """Persist the latest rendered reply card for a single inbound turn."""

    request_message_id: str
    session_id: str
    conversation_id: str
    chat_id: str
    prompt_text: str
    thread_id: str | None = None
    reply_message_id: str | None = None
    card_id: str | None = None
    cardkit_supported: bool | None = None
    stream_uuid: str | None = None
    next_sequence: int = 1
    streaming_active: bool = False
    status: str = "running"
    assistant_message: str | None = None
    status_message: str | None = None
    approval_tool_name: str | None = None
    latest_card: JsonObject = field(default_factory=dict)
    delivery_pending: bool = False
    retry_count: int = 0
    next_retry_at: float = 0.0
    last_error: str | None = None
    dirty: bool = True
    last_flush_at: float = 0.0
    updated_at: float = field(default_factory=time)

    def to_dict(self) -> JsonObject:
        """Serialize a record for JSON persistence."""

        return asdict(self)

    @classmethod
    def from_dict(cls, data: JsonObject) -> FeishuReplyCardRecord:
        """Rebuild a record from persisted JSON."""

        latest_card = data.get("latest_card")
        return cls(
            request_message_id=str(data["request_message_id"]),
            session_id=str(data["session_id"]),
            conversation_id=str(data["conversation_id"]),
            chat_id=str(data["chat_id"]),
            prompt_text=str(data.get("prompt_text", "")),
            thread_id=str(data["thread_id"]) if data.get("thread_id") is not None else None,
            reply_message_id=(
                str(data["reply_message_id"]) if data.get("reply_message_id") is not None else None
            ),
            card_id=str(data["card_id"]) if data.get("card_id") is not None else None,
            cardkit_supported=(
                bool(data["cardkit_supported"])
                if data.get("cardkit_supported") is not None
                else None
            ),
            stream_uuid=str(data["stream_uuid"]) if data.get("stream_uuid") is not None else None,
            next_sequence=_int_value(data.get("next_sequence"), default=1),
            streaming_active=bool(data.get("streaming_active", False)),
            status=str(data.get("status", "running")),
            assistant_message=(
                str(data["assistant_message"])
                if data.get("assistant_message") is not None
                else None
            ),
            status_message=(
                str(data["status_message"]) if data.get("status_message") is not None else None
            ),
            approval_tool_name=(
                str(data["approval_tool_name"])
                if data.get("approval_tool_name") is not None
                else None
            ),
            latest_card=dict(latest_card) if isinstance(latest_card, dict) else {},
            delivery_pending=bool(data.get("delivery_pending", False)),
            retry_count=_int_value(data.get("retry_count"), default=0),
            next_retry_at=_float_value(data.get("next_retry_at"), default=0.0),
            last_error=str(data["last_error"]) if data.get("last_error") is not None else None,
            dirty=bool(data.get("dirty", True)),
            last_flush_at=_float_value(data.get("last_flush_at"), default=0.0),
            updated_at=_float_value(data.get("updated_at"), default=time()),
        )

    def mark_delivery_failure(self, error: str, retry_delay_seconds: float) -> None:
        """Record a failed card delivery attempt and schedule a retry."""

        self.delivery_pending = True
        self.retry_count += 1
        self.last_error = error
        self.next_retry_at = time() + retry_delay_seconds
        self.updated_at = time()

    def mark_delivery_success(
        self,
        reply_message_id: str | None = None,
        *,
        card_id: str | None = None,
    ) -> None:
        """Record a successful card delivery/update."""

        if reply_message_id is not None:
            self.reply_message_id = reply_message_id
        if card_id is not None:
            self.card_id = card_id
        self.delivery_pending = False
        self.last_error = None
        self.next_retry_at = 0.0
        self.updated_at = time()

    def is_stable(self) -> bool:
        """Return whether the card is in a stable terminal state."""

        return self.status in _STABLE_CARD_STATUSES

    def ensure_stream_uuid(self) -> str:
        """Return a stable stream uuid for the current turn."""

        if self.stream_uuid is None:
            self.stream_uuid = f"{self.request_message_id}:{uuid4().hex}"
        return self.stream_uuid

    def next_stream_sequence(self) -> int:
        """Return the next strictly increasing sequence number."""

        value = self.next_sequence
        self.next_sequence += 1
        self.updated_at = time()
        return value


@dataclass(slots=True)
class FileFeishuCardDeliveryStore:
    """File-backed delivery ledger for Feishu reply cards."""

    storage_path: str
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def upsert(self, record: FeishuReplyCardRecord) -> None:
        """Persist the latest record state."""

        path = Path(self.storage_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            state = self._load(path)
            state[record.request_message_id] = record
            self._save(path, state)

    def get_by_request_message_id(self, request_message_id: str) -> FeishuReplyCardRecord | None:
        """Load a record by the original inbound message id."""

        path = Path(self.storage_path)
        with self._lock:
            return self._load(path).get(request_message_id)

    def get_by_reply_message_id(self, reply_message_id: str) -> FeishuReplyCardRecord | None:
        """Load a record by the Feishu reply-card message id."""

        path = Path(self.storage_path)
        with self._lock:
            state = self._load(path)
            for record in state.values():
                if record.reply_message_id == reply_message_id:
                    return record
        return None

    def list_pending(
        self,
        now: float | None = None,
        *,
        conversation_id: str | None = None,
    ) -> list[FeishuReplyCardRecord]:
        """Return records whose next retry time is ready."""

        ready_at = time() if now is None else now
        path = Path(self.storage_path)
        with self._lock:
            state = self._load(path)
            return [
                record
                for record in state.values()
                if record.delivery_pending and record.next_retry_at <= ready_at
                and (conversation_id is None or record.conversation_id == conversation_id)
            ]

    def _load(self, path: Path) -> dict[str, FeishuReplyCardRecord]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        state: dict[str, FeishuReplyCardRecord] = {}
        for key, value in payload.items():
            if isinstance(key, str) and isinstance(value, dict):
                try:
                    state[key] = FeishuReplyCardRecord.from_dict(value)
                except (KeyError, TypeError, ValueError):
                    continue
        return state

    def _save(self, path: Path, state: dict[str, FeishuReplyCardRecord]) -> None:
        payload = {key: value.to_dict() for key, value in state.items()}
        path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def make_initial_card_record(
    *,
    request_message_id: str,
    session_id: str,
    conversation_id: str,
    chat_id: str,
    thread_id: str | None,
    prompt_text: str,
) -> FeishuReplyCardRecord:
    """Create a new running reply-card record for a turn."""

    record = FeishuReplyCardRecord(
        request_message_id=request_message_id,
        session_id=session_id,
        conversation_id=conversation_id,
        chat_id=chat_id,
        thread_id=thread_id,
        prompt_text=prompt_text,
        status="running",
        status_message="Processing your request...",
    )
    record.ensure_stream_uuid()
    record.latest_card = render_reply_card(record)
    record.dirty = True
    return record


def apply_runtime_event_to_card(
    record: FeishuReplyCardRecord,
    event_type: str,
    payload: JsonObject,
) -> None:
    """Fold a runtime event into the persisted card state."""

    if event_type == "assistant_delta":
        delta = str(payload.get("delta", ""))
        if delta:
            record.assistant_message = (record.assistant_message or "") + delta
            record.status = "running"
    elif event_type == "assistant_message":
        message = str(payload.get("message", "")).strip()
        if message:
            record.assistant_message = message
    elif event_type == "requires_action":
        tool_name = str(payload.get("tool_name", "tool"))
        record.status = "requires_action"
        record.approval_tool_name = tool_name
        record.status_message = f"Approval required for {tool_name}."
    elif event_type == "tool_started":
        tool_name = str(payload.get("tool_name", "tool"))
        record.status = "running"
        record.status_message = f"Running tool: {tool_name}"
    elif event_type == "tool_progress":
        tool_name = str(payload.get("tool_name", "tool"))
        record.status = "running"
        record.status_message = f"Tool {tool_name} is working..."
    elif event_type == "tool_result":
        tool_name = str(payload.get("tool_name", "tool"))
        record.status_message = f"Tool {tool_name} completed."
    elif event_type == "tool_failed":
        tool_name = str(payload.get("tool_name", "tool"))
        reason = (
            payload.get("reason")
            or payload.get("error")
            or payload.get("message")
            or "unknown error"
        )
        record.status_message = f"Tool {tool_name} failed: {reason}"
    elif event_type == "tool_cancelled":
        tool_name = str(payload.get("tool_name", "tool"))
        record.status = "interrupted"
        record.status_message = f"Tool {tool_name} was cancelled."
    elif event_type == "turn_failed":
        record.status = "failed"
        record.approval_tool_name = None
        summary = payload.get("summary") or payload.get("reason") or "unknown error"
        record.status_message = f"Turn failed: {summary}"
    elif event_type == "turn_completed":
        if record.status not in {"requires_action", "failed", "interrupted"}:
            record.status = "completed"
        if record.status_message in {None, "Processing your request..."}:
            record.status_message = "Completed."
    record.latest_card = render_reply_card(record)
    record.dirty = True
    record.updated_at = time()


def mark_card_action_running(record: FeishuReplyCardRecord, action_name: str) -> None:
    """Set the card back to a running state when a button action is triggered."""

    record.status = "running"
    record.approval_tool_name = None
    record.status_message = "Applying your decision..."
    record.latest_card = render_reply_card(record)
    record.dirty = True
    record.updated_at = time()


def render_reply_card(record: FeishuReplyCardRecord) -> JsonObject:
    """Render a Feishu card payload from the current record state."""

    status_label = _status_label(record.status)
    elements: list[JsonObject] = []
    _append_markdown_section(
        elements,
        "Request",
        [_inline_markdown_text(record.prompt_text) or "_Empty_"],
    )
    _append_markdown_section(
        elements,
        "Status",
        [_inline_markdown_text(record.status_message or status_label)],
    )
    if record.assistant_message:
        _append_markdown_section(
            elements,
            "Reply",
            _split_markdown_blocks(record.assistant_message),
        )

    card: JsonObject = {
        "schema": "2.0",
        "config": {
            "wide_screen_mode": True,
        },
        "header": {
            "template": _header_template(record.status),
            "title": {
                "tag": "plain_text",
                "content": f"OpenAgent · {status_label}",
            },
        },
        "body": {
            "direction": "vertical",
            "elements": cast(JsonValue, elements),
        },
    }
    actions = _card_actions(record)
    if actions:
        body = cast(JsonObject, card["body"])
        elements = cast(list[JsonObject], body["elements"])
        elements.append(cast(JsonObject, {"tag": "action", "actions": actions}))
    return card


def _card_actions(record: FeishuReplyCardRecord) -> list[JsonObject]:
    if record.status == "requires_action":
        return [
            _button("Approve", {"subtype": "permission_response", "approved": True}, "primary"),
            _button("Reject", {"subtype": "permission_response", "approved": False}, "danger"),
        ]
    return []


def _button(
    text: str,
    value: JsonObject,
    button_type: str = "default",
) -> JsonObject:
    return {
        "tag": "button",
        "type": button_type,
        "text": {"tag": "plain_text", "content": text},
        "value": value,
    }


def _status_label(status: str) -> str:
    return {
        "running": "Running",
        "requires_action": "Needs Approval",
        "completed": "Completed",
        "failed": "Failed",
        "interrupted": "Interrupted",
    }.get(status, "Running")


def _header_template(status: str) -> str:
    return {
        "running": "blue",
        "requires_action": "orange",
        "completed": "green",
        "failed": "red",
        "interrupted": "grey",
    }.get(status, "blue")


def _markdown_text(text: str) -> str:
    normalized = _normalize_markdown_source(text)
    normalized = re.sub(r"\n{2,}", "\n", normalized)
    return normalized or ""


def _normalize_markdown_source(text: str) -> str:
    normalized = text.replace("\\r\\n", "\n").replace("\\n", "\n")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n").strip()
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized


def _inline_markdown_text(text: str) -> str:
    normalized = _normalize_markdown_source(text)
    normalized = re.sub(r"\n{2,}", "\n", normalized)
    return normalized or ""


def _split_markdown_blocks(text: str) -> list[str]:
    normalized = _normalize_markdown_source(text)
    if not normalized:
        return []

    blocks: list[str] = []
    current_lines: list[str] = []
    in_fence = False
    current_kind: str | None = None

    def flush() -> None:
        nonlocal current_lines, current_kind
        if current_lines:
            blocks.append("\n".join(current_lines).strip())
            current_lines = []
            current_kind = None

    for raw_line in normalized.split("\n"):
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("```"):
            if current_kind not in {None, "fence"}:
                flush()
            current_lines.append(line)
            current_kind = "fence"
            in_fence = not in_fence
            if not in_fence:
                flush()
            continue
        if in_fence:
            current_lines.append(line)
            continue
        if not stripped:
            flush()
            continue

        line_kind = _markdown_line_kind(stripped)
        if line_kind in {"heading", "table"}:
            if current_kind not in {None, line_kind}:
                flush()
        elif current_kind in {"heading", "table"} and line_kind != current_kind:
            flush()

        current_lines.append(line)
        current_kind = line_kind

    flush()
    return [block for block in blocks if block]


def _markdown_line_kind(stripped: str) -> str:
    if stripped.startswith("#"):
        return "heading"
    if stripped.startswith("|"):
        return "table"
    if re.match(r"^[-*+]\\s", stripped) or re.match(r"^\\d+\\.\\s", stripped):
        return "list"
    return "paragraph"


def _append_markdown_section(
    elements: list[JsonObject],
    title: str,
    body_blocks: list[str],
) -> None:
    elements.append({"tag": "markdown", "content": f"**{title}**"})
    for block in body_blocks:
        elements.append({"tag": "markdown", "content": block})


def _int_value(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _float_value(value: object, *, default: float) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default
