"""WeChat private-chat channel assembly helpers."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from openagent.gateway.binding_store import FileSessionBindingStore
from openagent.gateway.core import Gateway
from openagent.gateway.session_adapter import InProcessSessionAdapter
from openagent.harness.assemblies import create_file_runtime_assembly
from openagent.harness.runtime.core.agent_runtime import SimpleHarness
from openagent.harness.runtime.io import ModelProviderAdapter
from openagent.object_model import JsonObject
from openagent.observability import AgentObservability
from openagent.shared import (
    normalize_openagent_root,
    resolve_agent_root,
    resolve_path_env,
)
from openagent.tools import ToolDefinition

from .adapter import WechatChannelAdapter
from .client import WechatSdkClient
from .dedupe import FileWechatInboundDedupeStore
from .host import WechatPrivateChatHost


@dataclass(slots=True)
class WechatAppConfig:
    """Configuration for the WeChat private-chat SDK channel."""

    base_url: str = "https://ilinkai.weixin.qq.com"
    openagent_root: str = str(Path(".openagent"))
    agent_root: str = str(Path(".openagent") / "agent_default")
    cred_path: str = str(Path(".openagent") / "wechat" / "credentials.json")
    session_root: str = str(Path(".openagent") / "agent_default" / "sessions")
    binding_root: str = str(Path(".openagent") / "agent_default" / "bindings")
    allowed_senders: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> WechatAppConfig:
        openagent_root = normalize_openagent_root(os.getenv("OPENAGENT_ROOT"))
        role_id = os.getenv("OPENAGENT_ROLE_ID")
        agent_root = resolve_agent_root(openagent_root, role_id)
        session_root = resolve_path_env(
            "OPENAGENT_SESSION_ROOT",
            str(Path(agent_root) / "sessions"),
        ) or str(Path(agent_root) / "sessions")
        binding_root = resolve_path_env(
            "OPENAGENT_BINDING_ROOT",
            str(Path(agent_root) / "bindings"),
        ) or str(Path(agent_root) / "bindings")
        return cls(
            base_url=os.getenv("OPENAGENT_WECHAT_BASE_URL", "https://ilinkai.weixin.qq.com"),
            openagent_root=openagent_root,
            agent_root=agent_root,
            cred_path=os.getenv(
                "OPENAGENT_WECHAT_CRED_PATH",
                str(Path(".openagent") / "wechat" / "credentials.json"),
            ),
            session_root=session_root,
            binding_root=binding_root,
            allowed_senders=_parse_allowed_senders(
                os.getenv("OPENAGENT_WECHAT_ALLOWED_SENDERS", "")
            ),
        )


def create_wechat_runtime(
    model: ModelProviderAdapter,
    session_root: str,
    tools: list[ToolDefinition] | None = None,
    observability: AgentObservability | None = None,
) -> SimpleHarness:
    return create_file_runtime_assembly(
        model=model,
        session_root=session_root,
        tools=tools,
        observability=observability,
    )


def create_wechat_gateway(
    config: WechatAppConfig,
    model: ModelProviderAdapter,
    tools: list[ToolDefinition] | None = None,
) -> tuple[Gateway, SimpleHarness]:
    runtime = create_wechat_runtime(
        model=model,
        session_root=config.session_root,
        tools=tools,
    )
    gateway = Gateway(
        InProcessSessionAdapter(runtime),
        binding_store=FileSessionBindingStore(config.binding_root),
    )
    gateway.register_channel(WechatChannelAdapter())
    return gateway, runtime


def create_wechat_host(
    gateway: Gateway,
    config: WechatAppConfig,
    management_handler: Callable[[str], list[JsonObject]] | None = None,
) -> WechatPrivateChatHost:
    adapter = gateway.get_channel_adapter("wechat")
    if not isinstance(adapter, WechatChannelAdapter):
        raise TypeError("wechat channel adapter is not registered")
    client = WechatSdkClient(base_url=config.base_url, cred_path=config.cred_path)
    return WechatPrivateChatHost(
        gateway=gateway,
        adapter=adapter,
        client=client,
        management_handler=management_handler,
        dedupe_store=FileWechatInboundDedupeStore(
            str(Path(config.session_root) / "dedupe" / "wechat-message-ids.json")
        ),
        allowed_senders=set(config.allowed_senders) if config.allowed_senders else None,
    )


def create_wechat_host_from_env(
    model: ModelProviderAdapter,
    tools: list[ToolDefinition] | None = None,
) -> WechatPrivateChatHost:
    """Build a WeChat private-chat host from environment variables."""

    config = WechatAppConfig.from_env()
    gateway, _ = create_wechat_gateway(config=config, model=model, tools=tools)
    return create_wechat_host(gateway, config)


def _parse_allowed_senders(value: str | Iterable[str]) -> tuple[str, ...]:
    if isinstance(value, str):
        parts = value.split(",")
    else:
        parts = list(value)
    return tuple(part.strip() for part in parts if part.strip())
