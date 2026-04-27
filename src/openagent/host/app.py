"""Host application lifecycle and channel management."""

from __future__ import annotations

import os
import threading
from typing import cast

from openagent.gateway import (
    ChannelIdentity,
    TerminalChannelAdapter,
)
from openagent.gateway.assemblies.channel_manager import ChannelHostManager
from openagent.gateway.channels.tui import _TerminalConnectionHandler, _ThreadingTCPServer
from openagent.harness.providers import ProviderConfigurationError, load_model_from_env
from openagent.harness.runtime.io import ModelProviderAdapter
from openagent.host.config import OpenAgentHostConfig
from openagent.local import create_file_runtime, create_gateway_for_runtime
from openagent.object_model import JsonObject, JsonValue
from openagent.tools import ToolDefinition, create_builtin_toolset


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
            model_io_root=self.config.model_io_root,
        )
        self.gateway = create_gateway_for_runtime(
            self.runtime,
            binding_root=self.config.binding_root,
        )
        self._loaded_channels: set[str] = set()
        self._available_channels: tuple[str, ...] = ("terminal", "feishu", "wechat", "wecom")
        self._lock = threading.Lock()
        self._terminal_server: _ThreadingTCPServer | None = None
        self._terminal_thread: threading.Thread | None = None
        self._channel_manager = ChannelHostManager(
            self.gateway,
            self.config,
            management_handler=self.handle_management_command,
        )

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
            if channel in self._channel_manager.available_channels:
                self._channel_manager.ensure_channel_loaded(channel)
                self._loaded_channels.add(channel)
                return
            raise ValueError(f"Unsupported channel: {channel}")

    def describe_channels(self) -> JsonObject:
        usage = [
            "/channel",
            "/channel <name>",
        ]
        usage.extend(self._channel_manager.usage_lines[2:])
        loaded_channels = sorted(self._loaded_channels | self._channel_manager.loaded_channels)
        return {
            "loaded": cast(list[JsonValue], loaded_channels),
            "available": cast(list[JsonValue], list(self._available_channels)),
            "usage": cast(list[JsonValue], usage),
            "message": (
                f"loaded={','.join(loaded_channels) or 'none'} "
                f"available={','.join(self._available_channels)} "
                f"usage={'; '.join(usage)}"
            ),
        }

    def set_channel_config(self, channel_name: str, key: str, value: str) -> JsonObject:
        return self._channel_manager.set_channel_config(channel_name, key, value)

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
                        "usage: " + " | ".join(self._channel_manager.usage_lines[2:]),
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
        self._terminal_server.app = self
        self.config.terminal_port = int(self._terminal_server.server_address[1])
        self._terminal_thread = threading.Thread(
            target=self._terminal_server.serve_forever,
            name="openagent-terminal-server",
            daemon=False,
        )
        self._terminal_thread.start()

    def _default_tools(self) -> list[ToolDefinition]:
        return cast(list[ToolDefinition], create_builtin_toolset())

    def _load_channel_from_command(self, channel_name: str) -> JsonObject:
        channel = channel_name.strip().lower()
        if channel not in self._available_channels:
            return self._management_response("error", f"unknown channel: {channel}")
        if channel == "terminal":
            self.ensure_channel_loaded("terminal")
            return self._management_response("status", "terminal channel is loaded")
        result = self._channel_manager.load_channel_from_command(channel)
        if result.get("type") == "status" and channel in self._channel_manager.loaded_channels:
            self._loaded_channels.add(channel)
        return result

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
        provider = os.getenv("OPENAGENT_PROVIDER", "openai").strip().lower()
        model_name = os.getenv("OPENAGENT_MODEL", "").strip()
        base_url = os.getenv("OPENAGENT_BASE_URL", "").strip()
        try:
            adapter = load_model_from_env()
        except ProviderConfigurationError as exc:
            guidance = (
                "configure OPENAGENT_MODEL and OPENAGENT_BASE_URL before starting the host"
            )
            self._model_summary = f"invalid provider config: {exc}"
            print(f"openagent-host> invalid provider config: {exc}")
            print(
                "openagent-host> example: "
                "export OPENAGENT_PROVIDER=openai && "
                "export OPENAGENT_MODEL=<model-name> && "
                "export OPENAGENT_BASE_URL=http://127.0.0.1:8001"
            )
            print(f"openagent-host> guidance: {guidance}")
            raise
        resolved_model_name = getattr(adapter, "model", model_name)
        self._model_summary = (
            f"{provider}:{resolved_model_name} via {base_url or '<missing-base-url>'}"
        )
        return adapter
