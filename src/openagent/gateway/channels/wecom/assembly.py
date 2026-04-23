"""WeCom private-chat channel assembly helpers."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from openagent.gateway.binding_store import FileSessionBindingStore
from openagent.gateway.core import Gateway
from openagent.gateway.session_adapter import InProcessSessionAdapter
from openagent.harness.assemblies import create_file_runtime_assembly
from openagent.harness.runtime.core.agent_runtime import SimpleHarness
from openagent.harness.runtime.io import ModelProviderAdapter
from openagent.object_model import JsonObject
from openagent.observability import AgentObservability
from openagent.shared import normalize_workspace_root
from openagent.tools import ToolDefinition

from .adapter import WeComChannelAdapter
from .client import WeComAiBotClient
from .dedupe import FileWeComInboundDedupeStore
from .host import WeComPrivateChatHost


@dataclass(slots=True)
class WeComAppConfig:
    """Configuration for the WeCom AI Bot private-chat channel."""

    bot_id: str
    secret: str
    ws_url: str = "wss://openws.work.weixin.qq.com"
    ping_interval_seconds: float = 30.0
    session_root: str = str(Path(".openagent") / "wecom" / "sessions")
    binding_root: str = str(Path(".openagent") / "wecom" / "sessions" / "bindings")
    allowed_users: tuple[str, ...] = ()
    workspace_root: str = field(default_factory=os.getcwd)

    @classmethod
    def from_env(cls) -> WeComAppConfig:
        bot_id = os.getenv("OPENAGENT_WECOM_BOT_ID", "").strip()
        secret = os.getenv("OPENAGENT_WECOM_SECRET", "").strip()
        if not bot_id:
            raise RuntimeError("OPENAGENT_WECOM_BOT_ID is required")
        if not secret:
            raise RuntimeError("OPENAGENT_WECOM_SECRET is required")
        session_root = os.getenv(
            "OPENAGENT_SESSION_ROOT",
            str(Path(".openagent") / "wecom" / "sessions"),
        )
        binding_root = os.getenv(
            "OPENAGENT_BINDING_ROOT",
            str(Path(session_root) / "bindings"),
        )
        ping_interval = float(os.getenv("OPENAGENT_WECOM_PING_INTERVAL_SECONDS", "30"))
        return cls(
            bot_id=bot_id,
            secret=secret,
            ws_url=os.getenv("OPENAGENT_WECOM_WS_URL", "wss://openws.work.weixin.qq.com"),
            ping_interval_seconds=ping_interval,
            session_root=session_root,
            binding_root=binding_root,
            allowed_users=_parse_allowed_users(os.getenv("OPENAGENT_WECOM_ALLOWED_USERS", "")),
            workspace_root=normalize_workspace_root(
                os.getenv("OPENAGENT_WORKSPACE_ROOT"),
                default=os.getcwd(),
            ),
        )


def create_wecom_runtime(
    model: ModelProviderAdapter,
    session_root: str,
    tools: list[ToolDefinition] | None = None,
    observability: AgentObservability | None = None,
    workspace_root: str | None = None,
) -> SimpleHarness:
    return create_file_runtime_assembly(
        model=model,
        session_root=session_root,
        tools=tools,
        observability=observability,
        workspace_root=workspace_root,
    )


def create_wecom_gateway(
    config: WeComAppConfig,
    model: ModelProviderAdapter,
    tools: list[ToolDefinition] | None = None,
) -> tuple[Gateway, SimpleHarness]:
    runtime = create_wecom_runtime(
        model=model,
        session_root=config.session_root,
        tools=tools,
        workspace_root=config.workspace_root,
    )
    gateway = Gateway(
        InProcessSessionAdapter(runtime),
        binding_store=FileSessionBindingStore(config.binding_root),
    )
    gateway.register_channel(WeComChannelAdapter())
    return gateway, runtime


def create_wecom_host(
    gateway: Gateway,
    config: WeComAppConfig,
    management_handler: Callable[[str], list[JsonObject]] | None = None,
) -> WeComPrivateChatHost:
    adapter = gateway.get_channel_adapter("wecom")
    if not isinstance(adapter, WeComChannelAdapter):
        raise TypeError("wecom channel adapter is not registered")
    client = WeComAiBotClient(
        bot_id=config.bot_id,
        secret=config.secret,
        ws_url=config.ws_url,
        ping_interval_seconds=config.ping_interval_seconds,
    )
    return WeComPrivateChatHost(
        gateway=gateway,
        adapter=adapter,
        client=client,
        management_handler=management_handler,
        dedupe_store=FileWeComInboundDedupeStore(
            str(Path(config.session_root) / "dedupe" / "wecom-message-ids.json")
        ),
        allowed_users=set(config.allowed_users) if config.allowed_users else None,
    )


def create_wecom_host_from_env(
    model: ModelProviderAdapter,
    tools: list[ToolDefinition] | None = None,
) -> WeComPrivateChatHost:
    """Build a WeCom private-chat host from environment variables."""

    config = WeComAppConfig.from_env()
    gateway, _ = create_wecom_gateway(config=config, model=model, tools=tools)
    return create_wecom_host(gateway, config)


def _parse_allowed_users(value: str | Iterable[str]) -> tuple[str, ...]:
    if isinstance(value, str):
        parts = value.split(",")
    else:
        parts = list(value)
    return tuple(part.strip() for part in parts if part.strip())
