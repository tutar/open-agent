"""Feishu gateway/runtime/host assembly helpers."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from openagent.context_governance import ContextGovernance
from openagent.gateway.channels.feishu import FeishuChannelAdapter
from openagent.gateway.hosts.feishu import (
    FeishuHostRunLock,
    FeishuLongConnectionHost,
    OfficialFeishuBotClient,
)
from openagent.gateway.hosts.feishu_dedupe import FileFeishuInboundDedupeStore
from openagent.harness import ModelProviderAdapter, SimpleHarness
from openagent.harness.model_io import FileModelIoCapture
from openagent.harness.providers import load_model_from_env
from openagent.object_model import JsonObject
from openagent.observability import AgentObservability
from openagent.session import FileSessionStore
from openagent.tools import (
    SimpleToolExecutor,
    StaticToolRegistry,
    ToolDefinition,
    create_builtin_toolset,
)

from ..binding_store import FileSessionBindingStore
from ..core import Gateway
from ..session_adapter import InProcessSessionAdapter


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
        workspace_root = os.getenv("OPENAGENT_WORKSPACE_ROOT", os.getcwd())
        lock_root = os.getenv(
            "OPENAGENT_FEISHU_LOCK_ROOT",
            str(Path("/tmp") / "openagent-feishu-locks"),
        )
        mention_required = os.getenv("OPENAGENT_FEISHU_GROUP_AT_ONLY", "true").lower() != "false"
        return cls(
            app_id=app_id,
            app_secret=app_secret,
            session_root=session_root,
            binding_root=binding_root,
            workspace_root=workspace_root,
            lock_root=lock_root,
            mention_required_in_group=mention_required,
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

    registry = StaticToolRegistry(
        cast(list[ToolDefinition], list(tools))
        if tools is not None
        else cast(list[ToolDefinition], create_builtin_toolset(root=workspace_root or os.getcwd()))
    )
    return SimpleHarness(
        model=model,
        sessions=FileSessionStore(session_root),
        tools=registry,
        executor=SimpleToolExecutor(registry),
        context_governance=ContextGovernance(storage_dir=session_root),
        observability=observability,
        model_io_capture=FileModelIoCapture(
            str(
                model_io_root
                or os.getenv(
                    "OPENAGENT_MODEL_IO_ROOT",
                    str(Path(session_root).parent / "data" / "model-io"),
                )
            )
        ),
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
