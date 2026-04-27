"""Feishu host driver and host-side coordination."""

from __future__ import annotations

import fcntl
import json
import os
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from time import time
from typing import Any, cast

from openagent.object_model import JsonObject

from ...core import Gateway
from ...models import ChannelIdentity, EgressEnvelope
from .adapter import FeishuBotClient, FeishuChannelAdapter
from .cards import (
    FeishuReplyCardRecord,
    FileFeishuCardDeliveryStore,
    apply_runtime_event_to_card,
    make_initial_card_record,
    mark_card_action_running,
    render_reply_card,
)
from .dedupe import FileFeishuInboundDedupeStore, InMemoryFeishuInboundDedupeStore

FEISHU_REACTION_IN_PROGRESS = "OneSecond"
FEISHU_REACTION_COMPLETED = "DONE"
_APPROVAL_CARDKIT_REQUIRED_ERROR = (
    "Feishu approval card requires CardKit; patch fallback is unsupported"
)


@dataclass(slots=True)
class FeishuHostRunLock:
    """Prevent multiple local hosts from consuming the same Feishu app stream."""

    app_id: str
    lock_root: str
    _handle: Any | None = None

    def acquire(self) -> None:
        """Acquire a non-blocking local process lock for this Feishu app."""

        Path(self.lock_root).mkdir(parents=True, exist_ok=True)
        handle = open(Path(self.lock_root) / f"{self.app_id}.lock", "w", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise RuntimeError(
                "Another local Feishu host is already running for this app_id. "
                "Stop the existing process before starting a second host."
            ) from exc
        handle.write(str(os.getpid()))
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        """Release the local process lock when the host stops."""

        if self._handle is None:
            return
        handle = self._handle
        with suppress(OSError):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
        self._handle = None


@dataclass(slots=True)
class FeishuLongConnectionHost:
    """Glue the Feishu long-connection client to the gateway runtime."""

    gateway: Gateway
    adapter: FeishuChannelAdapter
    client: FeishuBotClient
    run_lock: FeishuHostRunLock | None = None
    management_handler: Callable[[str], list[JsonObject]] | None = None
    dedupe_store: InMemoryFeishuInboundDedupeStore | FileFeishuInboundDedupeStore | None = None
    card_delivery_store: FileFeishuCardDeliveryStore | None = None
    retry_interval_seconds: float = 5.0
    stream_flush_interval_seconds: float = 0.15
    current_time: Callable[[], float] = time
    _in_progress_reactions: dict[str, str | None] = field(default_factory=dict, init=False)
    _card_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _retry_stop: threading.Event = field(default_factory=threading.Event, init=False)
    _retry_thread: threading.Thread | None = field(default=None, init=False)

    def run(self) -> None:
        """Start the underlying Feishu long-connection client."""

        if self.run_lock is not None:
            self.run_lock.acquire()
        print("feishu-host> starting long connection", flush=True)

        def _dispatch(raw_event: JsonObject) -> JsonObject | None:
            result = self.handle_event(raw_event)
            if isinstance(result, dict):
                return result
            return None

        try:
            self._start_retry_loop()
            self.client.start(_dispatch)
        finally:
            self.close()
            if self.run_lock is not None:
                self.run_lock.release()

    def close(self) -> None:
        """Close the underlying Feishu long-connection client."""

        self._retry_stop.set()
        if self._retry_thread is not None:
            self._retry_thread.join(timeout=5)
            self._retry_thread = None
        self.client.close()

    def handle_event(self, raw_event: JsonObject) -> JsonObject | list[JsonObject]:
        """Handle a single Feishu event and emit projected outbound messages."""

        print(
            "feishu-host> received raw event",
            json.dumps(raw_event, ensure_ascii=False),
            flush=True,
        )
        if self._is_card_action_event(raw_event):
            return self._handle_long_connection_card_action(raw_event)
        message_id = self._extract_message_id(raw_event)
        if message_id is None:
            print("feishu-host> inbound event missing message_id; dedupe skipped", flush=True)
        elif self.dedupe_store is not None and self.dedupe_store.check_and_mark(message_id):
            print(f"feishu-host> skipped duplicate inbound message_id={message_id}", flush=True)
            return []
        inbound = self.adapter.normalize_inbound(raw_event)
        if inbound is None:
            print("feishu-host> ignored event after normalization", flush=True)
            return []
        self._mark_message_in_progress(message_id)

        channel_identity = ChannelIdentity.from_dict(inbound.channel_identity)
        print(
            "feishu-host> normalized input"
            f" kind={inbound.input_kind} conversation={channel_identity.conversation_id}",
            flush=True,
        )
        if inbound.input_kind == "management":
            try:
                command = str(inbound.payload.get("command", ""))
                responses = (
                    self.management_handler(command)
                    if self.management_handler is not None
                    else [
                        cast(
                            JsonObject,
                            {"type": "error", "message": "host management is unavailable"},
                        )
                    ]
                )
                return self._dispatch_management_responses(channel_identity, responses)
            finally:
                self._mark_message_completed(message_id)
        if inbound.input_kind == "control":
            try:
                try:
                    egress = self.gateway.process_control_message(channel_identity, inbound.payload)
                except KeyError:
                    message = self._missing_session_message(channel_identity)
                    print(
                        "feishu-host> no bound session for control input; sending hint",
                        flush=True,
                    )
                    self.adapter.send(message)
                    return [message]
                return self._dispatch_egress(egress)
            finally:
                self._mark_message_completed(message_id)

        prompt_text = str(inbound.payload.get("content", "")).strip()
        try:
            session_id = self._ensure_binding(channel_identity)
            card_record = self._ensure_turn_card(
                session_id=session_id,
                channel_identity=channel_identity,
                delivery_metadata=inbound.delivery_metadata,
                prompt_text=prompt_text,
                request_message_id=message_id,
            )
            egress = self.gateway.process_input(inbound)
            outbound = self._dispatch_egress(
                egress,
                card_record=card_record,
                completion_message_id=message_id,
            )
            if card_record is None:
                self._mark_message_completed(message_id)
            return outbound
        finally:
            self._retry_pending_cards(channel_identity.conversation_id)

    def handle_card_action(self, raw_card: object) -> JsonObject:
        """Handle a Feishu card button callback."""

        reply_message_id = self._card_reply_message_id(raw_card)
        if not reply_message_id or self.card_delivery_store is None:
            return {"toast": {"type": "warning", "content": "No active reply card was found."}}

        record = self.card_delivery_store.get_by_reply_message_id(reply_message_id)
        if record is None:
            return {"toast": {"type": "warning", "content": "No active reply card was found."}}

        value = self._card_action_value(raw_card)
        if not isinstance(value, dict):
            return {"toast": {"type": "warning", "content": "Unsupported card action."}}
        parsed = self.adapter.parse_card_action(cast(JsonObject, value))
        if parsed is None:
            return {"toast": {"type": "warning", "content": "Unsupported card action."}}
        _, control_payload = parsed
        action_name = str(control_payload.get("subtype", "")).strip()

        with self._card_lock:
            mark_card_action_running(record, action_name)
            self._sync_card_delivery(record)
            self.card_delivery_store.upsert(record)

        channel_identity = ChannelIdentity(
            channel_type="feishu",
            user_id=self._card_open_id(raw_card),
            conversation_id=record.conversation_id,
        )
        try:
            egress = self.gateway.process_control_message(
                channel_identity,
                control_payload,
                session_id_override=record.session_id,
            )
        except Exception as exc:
            with self._card_lock:
                record.status = "failed"
                record.status_message = str(exc)
                record.latest_card = render_reply_card(record)
                record.dirty = True
                self._sync_card_delivery(record)
                if self.card_delivery_store is not None:
                    self.card_delivery_store.upsert(record)
            raise
        self._dispatch_egress(egress, card_record=record, completion_message_id=None)
        return {"toast": {"type": "info", "content": "Action received."}}

    def retry_pending_cards(self, conversation_id: str | None = None) -> None:
        """Expose delivery retry for deterministic tests."""

        self._retry_pending_cards(conversation_id)

    def _handle_long_connection_card_action(self, raw_event: JsonObject) -> JsonObject:
        event = raw_event.get("event")
        if not isinstance(event, dict):
            return {"toast": {"type": "warning", "content": "Unsupported card action."}}
        event_wrapper = type(
            "CardActionEvent",
            (),
            {"event": _DictWrapper(event)},
        )()
        return self.handle_card_action(event_wrapper)

    def _ensure_binding(self, channel_identity: ChannelIdentity) -> str:
        conversation_id = channel_identity.conversation_id or "default"
        try:
            binding = self.gateway.get_binding(channel_identity.channel_type, conversation_id)
            return binding.session_id
        except KeyError:
            pass

        session_id = f"feishu-session:{conversation_id}"
        self.gateway.bind_session(channel_identity, session_id, adapter_name="feishu")
        return session_id

    def _dispatch_egress(
        self,
        egress_events: list[EgressEnvelope],
        *,
        card_record: FeishuReplyCardRecord | None = None,
        completion_message_id: str | None = None,
    ) -> list[JsonObject]:
        outbound_messages: list[JsonObject] = []
        for event in egress_events:
            event_type = str(event.event.get("event_type", ""))
            payload = event.event.get("payload")
            normalized_payload = payload if isinstance(payload, dict) else {}
            if card_record is not None and self._should_use_reply_card(event_type):
                with self._card_lock:
                    apply_runtime_event_to_card(card_record, event_type, normalized_payload)
                    delivered = False
                    if self._should_flush_card(card_record, event_type):
                        delivered = self._sync_card_delivery(card_record)
                    if self.card_delivery_store is not None:
                        self.card_delivery_store.upsert(card_record)
                outbound_messages.append(
                    {
                        "chat_id": card_record.chat_id,
                        "thread_id": card_record.thread_id,
                        "delivery": "card",
                        "status": card_record.status,
                        "message_id": card_record.reply_message_id,
                    }
                )
                if delivered and completion_message_id is not None and card_record.is_stable():
                    self._mark_message_completed(completion_message_id)
                    completion_message_id = None
                continue
            projected = self.adapter.project_outbound(event)
            if projected is not None:
                print(
                    "feishu-host> sending outbound"
                    f" event={event.event.get('event_type')} chat={projected['chat_id']}",
                    flush=True,
                )
                self.adapter.send(projected)
                outbound_messages.append(projected)
        return outbound_messages

    def _ensure_turn_card(
        self,
        *,
        session_id: str,
        channel_identity: ChannelIdentity,
        delivery_metadata: JsonObject,
        prompt_text: str,
        request_message_id: str | None,
    ) -> FeishuReplyCardRecord | None:
        if request_message_id is None or self.card_delivery_store is None:
            return None
        chat_id = str(delivery_metadata.get("chat_id", "")).strip()
        if not chat_id:
            return None
        thread_id = (
            str(delivery_metadata["thread_id"]).strip()
            if delivery_metadata.get("thread_id") is not None
            else None
        )
        record = make_initial_card_record(
            request_message_id=request_message_id,
            session_id=session_id,
            conversation_id=channel_identity.conversation_id or "default",
            chat_id=chat_id,
            thread_id=thread_id,
            prompt_text=prompt_text,
        )
        with self._card_lock:
            existing = self.card_delivery_store.get_by_request_message_id(request_message_id)
            if existing is not None:
                return existing
            self._sync_card_delivery(record)
            self.card_delivery_store.upsert(record)
        return record

    def _extract_message_id(self, raw_event: JsonObject) -> str | None:
        event = raw_event.get("event")
        if not isinstance(event, dict):
            return None
        message = event.get("message")
        if not isinstance(message, dict):
            return None
        raw_message_id = message.get("message_id")
        if raw_message_id is None:
            return None
        message_id = str(raw_message_id).strip()
        return message_id or None

    def _missing_session_message(self, channel_identity: ChannelIdentity) -> JsonObject:
        conversation_id = channel_identity.conversation_id or "default"
        chat_id, thread_id = self.adapter.parse_conversation_id(conversation_id)
        return {
            "chat_id": chat_id,
            "thread_id": thread_id,
            "text": "No active session is bound for this chat yet. Send a normal message first.",
        }

    def _dispatch_management_responses(
        self,
        channel_identity: ChannelIdentity,
        responses: list[JsonObject],
    ) -> list[JsonObject]:
        conversation_id = channel_identity.conversation_id or "default"
        chat_id, thread_id = self.adapter.parse_conversation_id(conversation_id)
        outbound_messages: list[JsonObject] = []
        for response in responses:
            text = str(response.get("message", "")).strip()
            if not text:
                continue
            projected: JsonObject = {"chat_id": chat_id, "thread_id": thread_id, "text": text}
            print(
                "feishu-host> sending management outbound"
                f" chat={chat_id} text={text}",
                flush=True,
            )
            self.adapter.send(projected)
            outbound_messages.append(projected)
        return outbound_messages

    def _mark_message_in_progress(self, message_id: str | None) -> None:
        if message_id is None or message_id in self._in_progress_reactions:
            return
        try:
            reaction_id = self.client.add_reaction(message_id, FEISHU_REACTION_IN_PROGRESS)
        except Exception as exc:  # pragma: no cover
            print(
                f"feishu-host> failed to add in-progress reaction message_id={message_id}: {exc}",
                flush=True,
            )
            reaction_id = None
        self._in_progress_reactions[message_id] = reaction_id

    def _mark_message_completed(self, message_id: str | None) -> None:
        if message_id is None:
            return
        reaction_id = self._in_progress_reactions.pop(message_id, None)
        if reaction_id is not None:
            try:
                self.client.remove_reaction(message_id, reaction_id)
            except Exception as exc:  # pragma: no cover
                print(
                    "feishu-host> failed to remove in-progress reaction"
                    f" message_id={message_id}: {exc}",
                    flush=True,
                )
        try:
            self.client.add_reaction(message_id, FEISHU_REACTION_COMPLETED)
        except Exception as exc:  # pragma: no cover
            print(
                f"feishu-host> failed to add completed reaction message_id={message_id}: {exc}",
                flush=True,
            )

    def _should_use_reply_card(self, event_type: str) -> bool:
        return event_type in {
            "turn_started",
            "assistant_delta",
            "assistant_message",
            "requires_action",
            "tool_started",
            "tool_progress",
            "tool_result",
            "tool_failed",
            "tool_cancelled",
            "turn_failed",
            "turn_completed",
        }

    def _sync_card_delivery(self, record: FeishuReplyCardRecord) -> bool:
        try:
            if record.reply_message_id is None:
                print(
                    "feishu-host> sending reply card"
                    f" request_message_id={record.request_message_id}"
                    f" status={record.status}",
                    flush=True,
                )
                reply_message_id = self.client.send_card(
                    record.chat_id,
                    record.latest_card,
                    thread_id=record.thread_id,
                )
                record.mark_delivery_success(reply_message_id)

            if (
                record.cardkit_supported is not False
                and record.card_id is None
                and record.reply_message_id is not None
            ):
                try:
                    print(
                        "feishu-host> resolving card id"
                        f" message_id={record.reply_message_id}",
                        flush=True,
                    )
                    card_id = self.client.resolve_card_id(record.reply_message_id)
                    record.cardkit_supported = True
                    record.mark_delivery_success(card_id=card_id)
                except Exception as exc:
                    if self._requires_cardkit_approval(record) and self._should_fallback_to_message_patch(
                        exc
                    ):
                        record.cardkit_supported = False
                        record.card_id = None
                        record.streaming_active = False
                        raise RuntimeError(_APPROVAL_CARDKIT_REQUIRED_ERROR) from exc
                    if not self._should_fallback_to_message_patch(exc):
                        raise
                    record.cardkit_supported = False
                    record.card_id = None
                    record.streaming_active = False
                    print(
                        "feishu-host> cardkit permission unavailable;"
                        " falling back to message patch"
                        f" request_message_id={record.request_message_id}",
                        flush=True,
                    )

            approval_synced = False
            if self._requires_cardkit_approval(record) and record.card_id is not None:
                self._sync_approval_card_via_cardkit(record)
                approval_synced = True

            if approval_synced:
                pass
            elif record.cardkit_supported is False:
                if self._requires_cardkit_approval(record):
                    raise RuntimeError(_APPROVAL_CARDKIT_REQUIRED_ERROR)
                if record.reply_message_id is None:
                    raise RuntimeError("reply card is missing message_id for patch fallback")
                print(
                    "feishu-host> syncing reply card"
                    f" request_message_id={record.request_message_id}"
                    f" status={record.status} mode=patch",
                    flush=True,
                )
                self.client.update_card(record.reply_message_id, record.latest_card)
            elif not record.is_stable():
                if record.card_id is None:
                    raise RuntimeError("reply card is missing card_id after creation")
                try:
                    if not record.streaming_active:
                        self.client.enable_card_stream(
                            record.card_id,
                            uuid=record.ensure_stream_uuid(),
                            sequence=record.next_stream_sequence(),
                        )
                        record.streaming_active = True
                    print(
                        "feishu-host> syncing reply card"
                        f" request_message_id={record.request_message_id}"
                        f" status={record.status} mode=stream",
                        flush=True,
                    )
                    self.client.stream_update_card(
                        record.card_id,
                        record.latest_card,
                        uuid=record.ensure_stream_uuid(),
                        sequence=record.next_stream_sequence(),
                    )
                except Exception as exc:
                    if self._requires_cardkit_approval(record) and self._should_fallback_to_message_patch(
                        exc
                    ):
                        record.cardkit_supported = False
                        record.streaming_active = False
                        raise RuntimeError(_APPROVAL_CARDKIT_REQUIRED_ERROR) from exc
                    if not self._should_fallback_to_message_patch(exc):
                        raise
                    record.cardkit_supported = False
                    record.streaming_active = False
                    if record.reply_message_id is None:
                        raise RuntimeError("reply card is missing message_id for patch fallback")
                    print(
                        "feishu-host> cardkit streaming unavailable;"
                        " falling back to message patch"
                        f" request_message_id={record.request_message_id}",
                        flush=True,
                    )
                    print(
                        "feishu-host> syncing reply card"
                        f" request_message_id={record.request_message_id}"
                        f" status={record.status} mode=patch",
                        flush=True,
                    )
                    self.client.update_card(record.reply_message_id, record.latest_card)
            else:
                if record.card_id is None:
                    raise RuntimeError("reply card is missing card_id after creation")
                if record.streaming_active:
                    print(
                        "feishu-host> syncing reply card"
                        f" request_message_id={record.request_message_id}"
                        f" status={record.status} mode=stream-finalize",
                        flush=True,
                    )
                    self.client.stream_update_card(
                        record.card_id,
                        record.latest_card,
                        uuid=record.ensure_stream_uuid(),
                        sequence=record.next_stream_sequence(),
                    )
                    self.client.disable_card_stream(
                        record.card_id,
                        uuid=record.ensure_stream_uuid(),
                        sequence=record.next_stream_sequence(),
                    )
                    record.streaming_active = False
                elif record.reply_message_id is not None:
                    print(
                        "feishu-host> syncing reply card"
                        f" request_message_id={record.request_message_id}"
                        f" status={record.status} mode=patch",
                        flush=True,
                    )
                    self.client.update_card(record.reply_message_id, record.latest_card)

            record.mark_delivery_success()
            record.dirty = False
            record.last_flush_at = self.current_time()
            return True
        except Exception as exc:  # pragma: no cover
            print(
                "feishu-host> failed to sync reply card"
                f" request_message_id={record.request_message_id}: {exc}",
                flush=True,
            )
            record.mark_delivery_failure(
                str(exc),
                retry_delay_seconds=self.retry_interval_seconds,
            )
            return False

    def _sync_approval_card_via_cardkit(self, record: FeishuReplyCardRecord) -> None:
        if record.card_id is None:
            raise RuntimeError("reply card is missing card_id for approval delivery")
        print(
            "feishu-host> syncing reply card"
            f" request_message_id={record.request_message_id}"
            f" status={record.status} mode=cardkit",
            flush=True,
        )
        self.client.stream_update_card(
            record.card_id,
            record.latest_card,
            uuid=record.ensure_stream_uuid(),
            sequence=record.next_stream_sequence(),
        )
        record.cardkit_supported = True
        if record.streaming_active:
            self.client.disable_card_stream(
                record.card_id,
                uuid=record.ensure_stream_uuid(),
                sequence=record.next_stream_sequence(),
            )
            record.streaming_active = False

    def _should_flush_card(self, record: FeishuReplyCardRecord, event_type: str) -> bool:
        if record.reply_message_id is None or record.delivery_pending:
            return True
        if event_type != "assistant_delta":
            return True
        if not record.dirty:
            return False
        return (self.current_time() - record.last_flush_at) >= self.stream_flush_interval_seconds

    def _is_cardkit_permission_error(self, exc: Exception) -> bool:
        message = str(exc)
        return "cardkit:card:read" in message or "code=99991672" in message

    def _should_fallback_to_message_patch(self, exc: Exception) -> bool:
        message = str(exc)
        return (
            self._is_cardkit_permission_error(exc)
            or "code=99992402" in message
            or "code=200740" in message
        )

    def _requires_cardkit_approval(self, record: FeishuReplyCardRecord) -> bool:
        return record.status == "requires_action"

    def _should_suspend_card_retry(self, record: FeishuReplyCardRecord) -> bool:
        return self._requires_cardkit_approval(record) and (
            record.last_error == _APPROVAL_CARDKIT_REQUIRED_ERROR
        )

    def _retry_pending_cards(self, conversation_id: str | None = None) -> None:
        if self.card_delivery_store is None:
            return
        for record in self.card_delivery_store.list_pending(conversation_id=conversation_id):
            if self._should_suspend_card_retry(record):
                print(
                    "feishu-host> approval card retry suspended"
                    f" request_message_id={record.request_message_id}"
                    f" reason={record.last_error}",
                    flush=True,
                )
                continue
            print(
                "feishu-host> retrying pending reply card"
                f" request_message_id={record.request_message_id}"
                f" retry_count={record.retry_count}",
                flush=True,
            )
            with self._card_lock:
                delivered = self._sync_card_delivery(record)
                self.card_delivery_store.upsert(record)
            if delivered and record.is_stable():
                self._mark_message_completed(record.request_message_id)

    def _start_retry_loop(self) -> None:
        if self.card_delivery_store is None or self._retry_thread is not None:
            return
        self._retry_stop.clear()

        def _worker() -> None:
            while not self._retry_stop.wait(self.retry_interval_seconds):
                self._retry_pending_cards()

        self._retry_thread = threading.Thread(
            target=_worker,
            name="openagent-feishu-card-retry",
            daemon=True,
        )
        self._retry_thread.start()

    def _is_card_action_event(self, raw_event: JsonObject) -> bool:
        header = raw_event.get("header")
        if not isinstance(header, dict):
            return False
        return str(header.get("event_type", "")).strip() == "card.action.trigger"

    def _card_reply_message_id(self, raw_card: object) -> str:
        direct = str(getattr(raw_card, "open_message_id", "")).strip()
        if direct:
            return direct
        event = getattr(raw_card, "event", None)
        context = getattr(event, "context", None)
        return str(getattr(context, "open_message_id", "")).strip()

    def _card_action_value(self, raw_card: object) -> object:
        action = getattr(raw_card, "action", None)
        direct = getattr(action, "value", None)
        if isinstance(direct, _DictWrapper):
            return direct._payload
        if direct is not None:
            return direct
        event = getattr(raw_card, "event", None)
        nested_action = getattr(event, "action", None)
        nested_value = getattr(nested_action, "value", None)
        if isinstance(nested_value, _DictWrapper):
            return nested_value._payload
        return nested_value

    def _card_open_id(self, raw_card: object) -> str | None:
        direct = str(getattr(raw_card, "open_id", "")).strip()
        if direct:
            return direct
        event = getattr(raw_card, "event", None)
        operator = getattr(event, "operator", None)
        value = str(getattr(operator, "open_id", "")).strip()
        return value or None

class _DictWrapper:
    """Attribute-style adapter for long-connection callback dictionaries."""

    def __init__(self, payload: JsonObject) -> None:
        self._payload = payload

    def __getattr__(self, name: str) -> object:
        value = self._payload.get(name)
        if isinstance(value, dict):
            return _DictWrapper(value)
        return value
