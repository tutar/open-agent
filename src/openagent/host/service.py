"""Single-host, multi-channel OpenAgent runtime service."""

from __future__ import annotations

import json
import os
import socketserver
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from openagent.gateway import (
    ChannelIdentity,
    EgressEnvelope,
    FeishuAppConfig,
    FeishuChannelAdapter,
    InboundEnvelope,
    TerminalChannelAdapter,
    create_feishu_host,
)
from openagent.harness import (
    ModelProviderAdapter,
    ModelProviderExchange,
    ModelTurnRequest,
    ModelTurnResponse,
)
from openagent.harness.providers import ProviderConfigurationError, load_model_from_env
from openagent.local import create_file_runtime, create_gateway_for_runtime
from openagent.object_model import JsonObject, JsonValue, ToolResult
from openagent.tools import (
    PermissionDecision,
    ToolCall,
    ToolDefinition,
    create_builtin_toolset,
)


@dataclass(slots=True)
class EchoTool:
    name: str = "echo"
    input_schema: dict[str, str] = field(default_factory=lambda: {"type": "object"})
    aliases: list[str] = field(default_factory=list)

    def description(self) -> str:
        return "Echo the provided text."

    def call(self, arguments: dict[str, object]) -> ToolResult:
        return ToolResult(
            tool_name=self.name,
            success=True,
            content=[str(arguments.get("text", ""))],
        )

    def check_permissions(self, arguments: dict[str, object]) -> str:
        del arguments
        return PermissionDecision.ALLOW.value

    def is_concurrency_safe(self) -> bool:
        return True


@dataclass(slots=True)
class AdminTool:
    name: str = "admin"
    input_schema: dict[str, str] = field(default_factory=lambda: {"type": "object"})
    aliases: list[str] = field(default_factory=list)

    def description(self) -> str:
        return "A permission-gated administrative action."

    def call(self, arguments: dict[str, object]) -> ToolResult:
        action = str(arguments.get("text", ""))
        return ToolResult(
            tool_name=self.name,
            success=True,
            content=[f"admin action completed: {action}"],
        )

    def check_permissions(self, arguments: dict[str, object]) -> str:
        del arguments
        return PermissionDecision.ASK.value

    def is_concurrency_safe(self) -> bool:
        return True


@dataclass(slots=True)
class DemoModel:
    provider_family = "demo"

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        return self.generate_with_exchange(request).response

    def generate_with_exchange(self, request: ModelTurnRequest) -> ModelProviderExchange:
        latest = request.messages[-1]
        role = str(latest.get("role", "user"))
        content = str(latest.get("content", ""))

        if role == "tool":
            response = ModelTurnResponse(assistant_message=f"Tool completed: {content}")
            return ModelProviderExchange(response=response)

        if content.startswith("tool "):
            response = ModelTurnResponse(
                tool_calls=[ToolCall(tool_name="echo", arguments={"text": content[5:]})]
            )
            return ModelProviderExchange(response=response)

        if content.startswith("admin "):
            response = ModelTurnResponse(
                tool_calls=[ToolCall(tool_name="admin", arguments={"text": content[6:]})]
            )
            return ModelProviderExchange(response=response)

        response = ModelTurnResponse(assistant_message=f"Echo: {content}")
        return ModelProviderExchange(response=response)


@dataclass(slots=True)
class OpenAgentHostConfig:
    session_root: str
    binding_root: str
    terminal_host: str = "127.0.0.1"
    terminal_port: int = 8765
    data_root: str = field(default_factory=lambda: str(Path(".openagent") / "data"))
    model_io_root: str = field(
        default_factory=lambda: str(Path(".openagent") / "data" / "model-io")
    )
    workspace_root: str = field(default_factory=os.getcwd)
    preload_channels: tuple[str, ...] = ()

    @classmethod
    def from_env(
        cls,
        preload_channels: Iterable[str] = (),
    ) -> OpenAgentHostConfig:
        root = Path(os.getenv("OPENAGENT_HOST_ROOT", str(Path(".openagent") / "host")))
        data_root = os.getenv("OPENAGENT_DATA_ROOT", str(root.parent / "data"))
        model_io_root = os.getenv("OPENAGENT_MODEL_IO_ROOT", str(Path(data_root) / "model-io"))
        session_root = os.getenv("OPENAGENT_SESSION_ROOT", str(root / "sessions"))
        binding_root = os.getenv("OPENAGENT_BINDING_ROOT", str(root / "bindings"))
        workspace_root = os.getenv("OPENAGENT_WORKSPACE_ROOT", os.getcwd())
        terminal_host = os.getenv("OPENAGENT_TERMINAL_HOST", "127.0.0.1")
        terminal_port = int(os.getenv("OPENAGENT_TERMINAL_PORT", "8765"))
        return cls(
            session_root=session_root,
            binding_root=binding_root,
            data_root=data_root,
            model_io_root=model_io_root,
            workspace_root=workspace_root,
            terminal_host=terminal_host,
            terminal_port=terminal_port,
            preload_channels=tuple(preload_channels),
        )


class _ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = False
    app: Any


class _TerminalConnectionHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        server = cast(_ThreadingTCPServer, self.server)
        app = cast(OpenAgentHost, server.app)
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


class OpenAgentHost:
    """Single-host runtime that can preload and lazily attach channels."""

    def __init__(
        self,
        config: OpenAgentHostConfig,
        *,
        model: ModelProviderAdapter | None = None,
        tools: list[ToolDefinition] | None = None,
    ) -> None:
        self.config = config
        self._model_summary = "custom"
        self.model = model or self._load_default_model()
        self.tools = list(tools) if tools is not None else self._default_tools()
        self.runtime = create_file_runtime(
            self.model,
            session_root=self.config.session_root,
            tools=self.tools,
            workspace_root=self.config.workspace_root,
            model_io_root=self.config.model_io_root,
        )
        self.gateway = create_gateway_for_runtime(
            self.runtime,
            binding_root=self.config.binding_root,
        )
        self._loaded_channels: set[str] = set()
        self._available_channels: tuple[str, ...] = ("terminal", "feishu")
        self._channel_runtime_config: dict[str, dict[str, str]] = {}
        self._lock = threading.Lock()
        self._terminal_server: _ThreadingTCPServer | None = None
        self._terminal_thread: threading.Thread | None = None
        self._channel_threads: list[threading.Thread] = []

    def start(self) -> None:
        self._start_terminal_transport()
        for channel in self.config.preload_channels:
            self.ensure_channel_loaded(channel)
        print(
            "openagent-host> ready"
            f" terminal={self.config.terminal_host}:{self.config.terminal_port}"
            f" channels={','.join(sorted(self._loaded_channels)) or 'none'}",
            flush=True,
        )
        print(f"openagent-host> model={self._model_summary}", flush=True)
        if self._terminal_thread is None:
            raise RuntimeError("Terminal transport did not start")
        self._terminal_thread.join()

    def ensure_channel_loaded(self, channel: str) -> None:
        with self._lock:
            if channel in self._loaded_channels:
                return
            if channel == "terminal":
                self.gateway.register_channel(TerminalChannelAdapter())
                self._loaded_channels.add(channel)
                return
            if channel == "feishu":
                self._load_feishu_channel()
                self._loaded_channels.add(channel)
                return
            raise ValueError(f"Unsupported channel: {channel}")

    def describe_channels(self) -> JsonObject:
        usage = [
            "/channel",
            "/channel <name>",
            "/channel-config feishu app_id <value>",
            "/channel-config feishu app_secret <value>",
        ]
        return {
            "loaded": cast(list[JsonValue], sorted(self._loaded_channels)),
            "available": cast(list[JsonValue], list(self._available_channels)),
            "usage": cast(list[JsonValue], usage),
            "message": (
                f"loaded={','.join(sorted(self._loaded_channels)) or 'none'} "
                f"available={','.join(self._available_channels)} "
                f"usage={'; '.join(usage)}"
            ),
        }

    def set_channel_config(self, channel_name: str, key: str, value: str) -> JsonObject:
        channel = channel_name.strip().lower()
        if channel != "feishu":
            return {"type": "error", "message": f"unsupported channel config target: {channel}"}
        if key not in {"app_id", "app_secret"}:
            return {"type": "error", "message": f"unsupported feishu config key: {key}"}
        self._channel_runtime_config.setdefault(channel, {})[key] = value
        return {
            "type": "status",
            "message": f"stored runtime config for {channel}.{key}",
        }

    def handle_management_command(self, command: str) -> list[JsonObject]:
        normalized = command.strip()
        if not normalized:
            return [self._management_response("error", "missing management command")]
        if normalized == "/channel":
            description = self.describe_channels()
            return [
                self._management_response(
                    "status",
                    str(description["message"]),
                    loaded=description["loaded"],
                    available=description["available"],
                    usage=description["usage"],
                )
            ]
        if normalized.startswith("/channel-config "):
            parts = normalized.split(maxsplit=3)
            if len(parts) != 4:
                return [
                    self._management_response(
                        "error",
                        "usage: /channel-config feishu app_id <value> | "
                        "/channel-config feishu app_secret <value>",
                    )
                ]
            _, channel_name, key, value = parts
            result = self.set_channel_config(channel_name, key, value)
            return [result]
        if normalized.startswith("/channel "):
            parts = normalized.split(maxsplit=1)
            if len(parts) != 2:
                return [self._management_response("error", "usage: /channel <name>")]
            return [self._load_channel_from_command(parts[1].strip())]
        return [self._management_response("error", f"unknown management command: {normalized}")]

    def bind_terminal_session(
        self,
        sessions: dict[str, ChannelIdentity],
        session_name: str,
    ) -> tuple[str, str]:
        conversation_id = f"terminal-{session_name}"
        session_id = f"{conversation_id}-session"
        channel = ChannelIdentity(
            channel_type="terminal",
            user_id="local-user",
            conversation_id=conversation_id,
        )
        try:
            self.gateway.bind_session(channel, session_id, adapter_name="terminal-tui")
        except ValueError:
            pass
        sessions[session_name] = channel
        return conversation_id, session_id

    def _start_terminal_transport(self) -> None:
        self._terminal_server = _ThreadingTCPServer(
            (self.config.terminal_host, self.config.terminal_port),
            _TerminalConnectionHandler,
        )
        self._terminal_server.app = self  # type: ignore[attr-defined]
        self.config.terminal_port = int(self._terminal_server.server_address[1])
        self._terminal_thread = threading.Thread(
            target=self._terminal_server.serve_forever,
            name="openagent-terminal-server",
            daemon=False,
        )
        self._terminal_thread.start()

    def _load_feishu_channel(self) -> None:
        config = self._resolve_feishu_config()
        try:
            self.gateway.get_channel_adapter("feishu")
        except KeyError:
            self.gateway.register_channel(
                FeishuChannelAdapter(
                    mention_required_in_group=config.mention_required_in_group,
                )
            )
        host = create_feishu_host(
            self.gateway,
            config,
            management_handler=self.handle_management_command,
        )
        thread = threading.Thread(
            target=host.run,
            name="openagent-feishu-host",
            daemon=True,
        )
        thread.start()
        self._channel_threads.append(thread)

    def _default_tools(self) -> list[ToolDefinition]:
        tools = cast(
            list[ToolDefinition],
            create_builtin_toolset(root=self.config.workspace_root),
        )
        tools.extend([EchoTool(), AdminTool()])
        return tools

    def _load_channel_from_command(self, channel_name: str) -> JsonObject:
        channel = channel_name.strip().lower()
        if channel not in self._available_channels:
            return self._management_response("error", f"unknown channel: {channel}")
        if channel == "terminal":
            self.ensure_channel_loaded("terminal")
            return self._management_response("status", "terminal channel is loaded")
        if channel == "feishu":
            if "feishu" in self._loaded_channels:
                return self._management_response("status", "feishu channel is already loaded")
            missing = self._missing_feishu_config_fields()
            if missing:
                return self._management_response(
                    "error",
                    "feishu config missing: "
                    + ", ".join(missing)
                    + " | use /channel-config feishu app_id <value> and "
                    "/channel-config feishu app_secret <value>",
                    missing_fields=cast(JsonValue, missing),
                )
            self.ensure_channel_loaded("feishu")
            return self._management_response("status", "feishu channel loaded")
        return self._management_response("error", f"unsupported channel: {channel}")

    def _missing_feishu_config_fields(self) -> list[str]:
        runtime_config = self._channel_runtime_config.get("feishu", {})
        missing: list[str] = []
        if not (runtime_config.get("app_id") or os.getenv("OPENAGENT_FEISHU_APP_ID")):
            missing.append("app_id")
        if not (runtime_config.get("app_secret") or os.getenv("OPENAGENT_FEISHU_APP_SECRET")):
            missing.append("app_secret")
        return missing

    def _resolve_feishu_config(self) -> FeishuAppConfig:
        runtime_config = self._channel_runtime_config.get("feishu", {})
        app_id = runtime_config.get("app_id") or os.getenv("OPENAGENT_FEISHU_APP_ID")
        app_secret = runtime_config.get("app_secret") or os.getenv("OPENAGENT_FEISHU_APP_SECRET")
        if not app_id:
            raise RuntimeError("OPENAGENT_FEISHU_APP_ID is required")
        if not app_secret:
            raise RuntimeError("OPENAGENT_FEISHU_APP_SECRET is required")
        session_root = os.getenv(
            "OPENAGENT_SESSION_ROOT",
            str(Path(".openagent") / "feishu" / "sessions"),
        )
        binding_root = os.getenv(
            "OPENAGENT_BINDING_ROOT",
            str(Path(session_root) / "bindings"),
        )
        lock_root = os.getenv(
            "OPENAGENT_FEISHU_LOCK_ROOT",
            str(Path("/tmp") / "openagent-feishu-locks"),
        )
        mention_required = os.getenv("OPENAGENT_FEISHU_GROUP_AT_ONLY", "true").lower() != "false"
        return FeishuAppConfig(
            app_id=app_id,
            app_secret=app_secret,
            session_root=session_root,
            binding_root=binding_root,
            workspace_root=self.config.workspace_root,
            lock_root=lock_root,
            mention_required_in_group=mention_required,
        )

    def _management_response(
        self,
        response_type: str,
        message: str,
        **extra: JsonValue,
    ) -> JsonObject:
        response: JsonObject = {"type": response_type, "message": message}
        response.update(extra)
        return response

    def _load_default_model(self) -> ModelProviderAdapter:
        if os.getenv("OPENAGENT_MODEL") is None:
            self._model_summary = "demo (OPENAGENT_MODEL not set)"
            return DemoModel()
        try:
            provider = os.getenv("OPENAGENT_PROVIDER", "openai").strip().lower()
            model_name = os.getenv("OPENAGENT_MODEL", "").strip()
            base_url = os.getenv("OPENAGENT_BASE_URL", "").strip()
            self._model_summary = (
                f"{provider}:{model_name} via {base_url or '<missing-base-url>'}"
            )
            return load_model_from_env()
        except ProviderConfigurationError as exc:
            self._model_summary = f"demo (provider config fallback: {exc})"
            return DemoModel()
