"""Feishu channel assembly helpers."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from openagent.gateway.binding_store import FileSessionBindingStore
from openagent.gateway.core import Gateway
from openagent.gateway.session_adapter import InProcessSessionAdapter
from openagent.harness.assemblies import create_file_runtime_assembly
from openagent.harness.providers import load_model_from_env
from openagent.harness.runtime.core.agent_runtime import SimpleHarness
from openagent.harness.runtime.io import ModelProviderAdapter
from openagent.object_model import JsonObject
from openagent.observability import AgentObservability
from openagent.shared import normalize_workspace_root
from openagent.tools import ToolDefinition

from .adapter import FeishuChannelAdapter
from .cards import FileFeishuCardDeliveryStore
from .client import OfficialFeishuBotClient
from .dedupe import FileFeishuInboundDedupeStore
from .host import FeishuHostRunLock, FeishuLongConnectionHost


@dataclass(slots=True)
class FeishuAppConfig:
    """Configuration for Feishu runtime and host assembly."""

    app_id: str
    app_secret: str
    session_root: str
    binding_root: str
    workspace_root: str = field(default_factory=os.getcwd)
    lock_root: str = str(Path("/tmp") / "openagent-feishu-locks")
    mention_required_in_group: bool = True
    card_state_root: str = str(Path(".openagent") / "feishu" / "cards")

    @classmethod
    def from_env(cls) -> FeishuAppConfig:
        """Load Feishu host configuration from the process environment."""

        app_id = os.getenv("OPENAGENT_FEISHU_APP_ID")
        app_secret = os.getenv("OPENAGENT_FEISHU_APP_SECRET")
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
        workspace_root = normalize_workspace_root(
            os.getenv("OPENAGENT_WORKSPACE_ROOT"),
            default=os.getcwd(),
        )
        lock_root = os.getenv(
            "OPENAGENT_FEISHU_LOCK_ROOT",
            str(Path("/tmp") / "openagent-feishu-locks"),
        )
        mention_required = os.getenv("OPENAGENT_FEISHU_GROUP_AT_ONLY", "true").lower() != "false"
        card_state_root = os.getenv(
            "OPENAGENT_FEISHU_CARD_STATE_ROOT",
            str(Path(session_root) / "cards"),
        )
        return cls(
            app_id=app_id,
            app_secret=app_secret,
            session_root=session_root,
            binding_root=binding_root,
            workspace_root=workspace_root,
            lock_root=lock_root,
            mention_required_in_group=mention_required,
            card_state_root=card_state_root,
        )


def create_feishu_runtime(
    model: ModelProviderAdapter,
    session_root: str,
    tools: list[ToolDefinition] | None = None,
    observability: AgentObservability | None = None,
    workspace_root: str | None = None,
    model_io_root: str | None = None,
) -> SimpleHarness:
    """Create a file-backed runtime suitable for Feishu sessions."""

    return create_file_runtime_assembly(
        model=model,
        session_root=session_root,
        tools=tools,
        observability=observability,
        workspace_root=workspace_root,
        model_io_root=model_io_root,
    )


def create_feishu_gateway(
    config: FeishuAppConfig,
    model: ModelProviderAdapter,
    tools: list[ToolDefinition] | None = None,
) -> tuple[Gateway, SimpleHarness]:
    """Create the file-backed Feishu gateway/runtime pair."""

    runtime = create_feishu_runtime(
        model=model,
        session_root=config.session_root,
        tools=tools,
        workspace_root=config.workspace_root,
    )
    gateway = Gateway(
        InProcessSessionAdapter(runtime),
        binding_store=FileSessionBindingStore(config.binding_root),
    )
    gateway.register_channel(
        FeishuChannelAdapter(mention_required_in_group=config.mention_required_in_group)
    )
    return gateway, runtime


def create_feishu_host(
    gateway: Gateway,
    config: FeishuAppConfig,
    management_handler: Callable[[str], list[JsonObject]] | None = None,
) -> FeishuLongConnectionHost:
    """Create a Feishu host bound to an existing gateway."""

    client = OfficialFeishuBotClient(config.app_id, config.app_secret)
    adapter = cast(FeishuChannelAdapter, gateway.get_channel_adapter("feishu"))
    adapter.client = client
    adapter.mention_required_in_group = config.mention_required_in_group
    return FeishuLongConnectionHost(
        gateway=gateway,
        adapter=adapter,
        client=client,
        run_lock=FeishuHostRunLock(config.app_id, config.lock_root),
        management_handler=management_handler,
        dedupe_store=FileFeishuInboundDedupeStore(
            str(Path(config.session_root) / "dedupe" / "feishu-message-ids.json")
        ),
        card_delivery_store=FileFeishuCardDeliveryStore(
            str(Path(config.card_state_root) / "delivery.json")
        ),
    )


def create_feishu_host_from_env() -> FeishuLongConnectionHost:
    """Build a Feishu long-connection host from environment variables."""

    config = FeishuAppConfig.from_env()
    gateway, _ = create_feishu_gateway(config=config, model=load_model_from_env())
    return create_feishu_host(gateway, config)


def main() -> None:
    """Start the Feishu long-connection gateway host."""

    host = create_feishu_host_from_env()
    host.run()
