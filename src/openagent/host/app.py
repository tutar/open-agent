"""Host application lifecycle and channel management."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import cast

from openagent.gateway import (
    ChannelIdentity,
    FeishuAppConfig,
    FeishuChannelAdapter,
    TerminalChannelAdapter,
    create_feishu_host,
)
from openagent.gateway.channels.tui import _TerminalConnectionHandler, _ThreadingTCPServer
from openagent.harness import ModelProviderAdapter
from openagent.harness.providers import ProviderConfigurationError, load_model_from_env
from openagent.host.config import OpenAgentHostConfig
from openagent.host.demo import AdminTool, DemoModel, EchoTool
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
        self._terminal_server.app = self
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
