"""Runtime event and payload helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from openagent.object_model import JsonObject, RuntimeEvent, RuntimeEventType, ToolResult
from openagent.session import SessionRecord
from openagent.tools import ToolCall


def append_event(session: SessionRecord, event: RuntimeEvent) -> RuntimeEvent:
    session.events.append(event)
    return event


def new_event(
    *,
    session_id: str,
    event_type: RuntimeEventType,
    payload: JsonObject,
    event_index: int,
) -> RuntimeEvent:
    timestamp = datetime.now(UTC).isoformat()
    event_id = f"{event_type.value}:{event_index}"
    return RuntimeEvent(
        event_type=event_type,
        event_id=event_id,
        timestamp=timestamp,
        session_id=session_id,
        payload=payload,
    )


def tool_call_payload(tool_call: ToolCall) -> JsonObject:
    payload = tool_call.to_dict()
    tool_use_id = payload.pop("call_id", None)
    if tool_use_id is not None:
        payload["tool_use_id"] = tool_use_id
    return payload


def tool_result_payload(result: ToolResult) -> JsonObject:
    payload = result.to_dict()
    metadata = payload.get("metadata")
    if isinstance(metadata, dict) and "tool_use_id" in metadata:
        payload["tool_use_id"] = metadata["tool_use_id"]
    return payload


def requires_action_payload(requires_action: object) -> JsonObject:
    if not hasattr(requires_action, "to_dict"):
        raise TypeError("requires_action payload must support to_dict()")
    payload = cast(JsonObject, requires_action.to_dict())
    request_id = payload.get("request_id")
    if request_id is not None:
        payload["tool_use_id"] = request_id
    return payload
