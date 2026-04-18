"""Terminal/TUI local transport server for the host."""

from __future__ import annotations

import json
import socketserver
from typing import TYPE_CHECKING, Any, cast

from openagent.gateway.models import ChannelIdentity, EgressEnvelope, InboundEnvelope
from openagent.object_model import JsonObject, JsonValue

if TYPE_CHECKING:
    from openagent.host.app import OpenAgentHost


class _ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = False
    app: Any


class _TerminalConnectionHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        server = cast(_ThreadingTCPServer, self.server)
        app = cast("OpenAgentHost", server.app)
        app.ensure_channel_loaded("terminal")
        sessions: dict[str, ChannelIdentity] = {}
        current_session_name = "main"
        _, current_session_id = app.bind_terminal_session(sessions, current_session_name)
        self._emit(
            {
                "type": "status",
                "message": "ready",
                "session_name": current_session_name,
                "session_id": current_session_id,
            }
        )

        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode("utf-8").strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                self._emit({"type": "error", "message": "invalid_json"})
                continue
            if not isinstance(message, dict):
                self._emit({"type": "error", "message": "invalid_message"})
                continue
            kind = message.get("kind")
            if kind == "bind":
                session_name = str(message.get("session_name", "")).strip()
                if not session_name:
                    self._emit({"type": "error", "message": "missing_session_name"})
                    continue
                _, current_session_id = app.bind_terminal_session(sessions, session_name)
                current_session_name = session_name
                self._emit(
                    {
                        "type": "status",
                        "message": "bound",
                        "session_name": current_session_name,
                        "session_id": current_session_id,
                    }
                )
                for item in app.gateway.observe_session(sessions[current_session_name]):
                    self._emit_event(item)
                continue
            if kind == "list_sessions":
                self._emit(
                    {
                        "type": "sessions",
                        "current_session_name": current_session_name,
                        "sessions": cast(list[JsonValue], sorted(sessions)),
                    }
                )
                continue
            if kind == "message":
                channel = sessions[current_session_name]
                egress = app.gateway.process_user_message(
                    InboundEnvelope(
                        channel_identity=channel.to_dict(),
                        input_kind="user_message",
                        payload={"content": str(message.get("content", ""))},
                    )
                )
                for item in egress:
                    self._emit_event(item)
                continue
            if kind == "management":
                command = str(message.get("command", ""))
                for response in app.handle_management_command(command):
                    self._emit(response)
                continue
            if kind == "control":
                subtype = str(message.get("subtype", ""))
                if subtype not in {"permission_response", "interrupt", "resume"}:
                    self._emit({"type": "error", "message": "unknown_control_subtype"})
                    continue
                control_payload: JsonObject = {"subtype": subtype}
                if subtype == "permission_response":
                    control_payload["approved"] = bool(message.get("approved", False))
                if subtype == "resume" and message.get("after") is not None:
                    after = message.get("after")
                    if isinstance(after, (str, int, float)) and not isinstance(after, bool):
                        control_payload["after"] = after
                egress = app.gateway.process_control_message(
                    sessions[current_session_name],
                    control_payload,
                )
                for item in egress:
                    self._emit_event(item)
                continue
            self._emit({"type": "error", "message": f"unknown_message_kind:{kind}"})

    def _emit(self, payload: JsonObject) -> None:
        self.wfile.write((json.dumps(payload) + "\n").encode("utf-8"))
        self.wfile.flush()

    def _emit_event(self, item: EgressEnvelope) -> None:
        self._emit(
            {
                "type": "event",
                "event_type": item.event["event_type"],
                "payload": item.event["payload"],
                "session_id": item.session_id,
            }
        )
