"""Local assembly helpers replacing profile-based wiring."""

from __future__ import annotations

from openagent.context_governance import ContextGovernance
from openagent.gateway import (
    ChannelAdapter,
    FileSessionBindingStore,
    Gateway,
    InProcessSessionAdapter,
)
from openagent.harness import ModelProviderAdapter, SimpleHarness
from openagent.observability import AgentObservability
from openagent.session import FileSessionStore, InMemorySessionStore
from openagent.tools import SimpleToolExecutor, StaticToolRegistry, ToolDefinition


def create_in_memory_runtime(
    model: ModelProviderAdapter,
    tools: list[ToolDefinition] | None = None,
    observability: AgentObservability | None = None,
) -> SimpleHarness:
    """Create a local in-memory runtime for tests and terminal hosts."""

    registry = StaticToolRegistry(tools or [])
    return SimpleHarness(
        model=model,
        sessions=InMemorySessionStore(),
        tools=registry,
        executor=SimpleToolExecutor(registry),
        context_governance=ContextGovernance(),
        observability=observability,
    )


def create_file_runtime(
    model: ModelProviderAdapter,
    session_root: str,
    tools: list[ToolDefinition] | None = None,
    observability: AgentObservability | None = None,
) -> SimpleHarness:
    """Create a local file-backed runtime for restart-safe sessions."""

    registry = StaticToolRegistry(tools or [])
    return SimpleHarness(
        model=model,
        sessions=FileSessionStore(session_root),
        tools=registry,
        executor=SimpleToolExecutor(registry),
        context_governance=ContextGovernance(storage_dir=session_root),
        observability=observability,
    )


def create_gateway_for_runtime(
    runtime: SimpleHarness,
    channel_adapters: list[ChannelAdapter] | None = None,
    binding_root: str | None = None,
) -> Gateway:
    """Create a gateway for an existing runtime and register channel adapters."""

    binding_store = FileSessionBindingStore(binding_root) if binding_root is not None else None
    gateway = Gateway(
        InProcessSessionAdapter(runtime),
        binding_store=binding_store,
        observability=runtime.observability,
    )
    for adapter in channel_adapters or []:
        gateway.register_channel(adapter)
    return gateway
