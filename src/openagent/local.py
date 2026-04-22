"""Public local runtime assembly facade."""

from __future__ import annotations

from openagent.gateway import ChannelAdapter, Gateway
from openagent.harness.assemblies import (
    create_file_runtime_assembly,
    create_gateway_for_runtime_assembly,
    create_in_memory_runtime_assembly,
)
from openagent.harness.runtime.core.agent_runtime import SimpleHarness
from openagent.harness.runtime.io import ModelProviderAdapter
from openagent.observability import AgentObservability
from openagent.tools import ToolDefinition


def create_in_memory_runtime(
    model: ModelProviderAdapter,
    tools: list[ToolDefinition] | None = None,
    observability: AgentObservability | None = None,
    workspace_root: str | None = None,
) -> SimpleHarness:
    """Create a local in-memory runtime with the builtin tool baseline."""

    return create_in_memory_runtime_assembly(
        model=model,
        tools=tools,
        observability=observability,
        workspace_root=workspace_root,
    )


def create_file_runtime(
    model: ModelProviderAdapter,
    session_root: str,
    tools: list[ToolDefinition] | None = None,
    observability: AgentObservability | None = None,
    workspace_root: str | None = None,
    model_io_root: str | None = None,
) -> SimpleHarness:
    """Create a local file-backed runtime with the builtin tool baseline."""

    return create_file_runtime_assembly(
        model=model,
        session_root=session_root,
        tools=tools,
        observability=observability,
        workspace_root=workspace_root,
        model_io_root=model_io_root,
    )


def create_gateway_for_runtime(
    runtime: SimpleHarness,
    channel_adapters: list[ChannelAdapter] | None = None,
    binding_root: str | None = None,
) -> Gateway:
    """Create a gateway for an existing runtime and register channel adapters."""

    return create_gateway_for_runtime_assembly(
        runtime=runtime,
        channel_adapters=channel_adapters,
        binding_root=binding_root,
    )
