"""Local runtime assembly helpers used by the public local facade."""

from __future__ import annotations

import os
from typing import cast

from openagent.gateway.binding_store import FileSessionBindingStore
from openagent.gateway.core import Gateway
from openagent.gateway.interfaces import ChannelAdapter
from openagent.gateway.session_adapter import InProcessSessionAdapter
from openagent.harness.context import ContextGovernance
from openagent.harness.model_io import FileModelIoCapture, NoOpModelIoCapture
from openagent.harness.models import ModelProviderAdapter
from openagent.harness.simple import SimpleHarness
from openagent.observability import AgentObservability
from openagent.session import FileSessionStore, InMemorySessionStore
from openagent.tools import (
    SimpleToolExecutor,
    StaticToolRegistry,
    ToolDefinition,
    create_builtin_toolset,
)


def create_in_memory_runtime_assembly(
    model: ModelProviderAdapter,
    tools: list[ToolDefinition] | None = None,
    observability: AgentObservability | None = None,
    workspace_root: str | None = None,
) -> SimpleHarness:
    registry = StaticToolRegistry(
        _resolve_runtime_tools(root=_default_workspace_root(workspace_root), tools=tools)
    )
    return SimpleHarness(
        model=model,
        sessions=InMemorySessionStore(),
        tools=registry,
        executor=SimpleToolExecutor(registry),
        context_governance=ContextGovernance(),
        observability=observability,
        model_io_capture=NoOpModelIoCapture(),
    )


def create_file_runtime_assembly(
    model: ModelProviderAdapter,
    session_root: str,
    tools: list[ToolDefinition] | None = None,
    observability: AgentObservability | None = None,
    workspace_root: str | None = None,
    model_io_root: str | None = None,
) -> SimpleHarness:
    registry = StaticToolRegistry(
        _resolve_runtime_tools(root=_default_workspace_root(workspace_root), tools=tools)
    )
    return SimpleHarness(
        model=model,
        sessions=FileSessionStore(session_root),
        tools=registry,
        executor=SimpleToolExecutor(registry),
        context_governance=ContextGovernance(storage_dir=session_root),
        observability=observability,
        model_io_capture=FileModelIoCapture(_default_model_io_root(session_root, model_io_root)),
    )


def create_gateway_for_runtime_assembly(
    runtime: SimpleHarness,
    channel_adapters: list[ChannelAdapter] | None = None,
    binding_root: str | None = None,
) -> Gateway:
    binding_store = FileSessionBindingStore(binding_root) if binding_root is not None else None
    gateway = Gateway(
        InProcessSessionAdapter(runtime),
        binding_store=binding_store,
        observability=runtime.observability,
    )
    for adapter in channel_adapters or []:
        gateway.register_channel(adapter)
    return gateway


def _resolve_runtime_tools(root: str, tools: list[ToolDefinition] | None) -> list[ToolDefinition]:
    if tools is not None:
        return tools
    return cast(list[ToolDefinition], create_builtin_toolset(root=root))


def _default_workspace_root(workspace_root: str | None) -> str:
    if workspace_root is not None:
        return workspace_root
    return os.getenv("OPENAGENT_WORKSPACE_ROOT", os.getcwd())


def _default_model_io_root(session_root: str, model_io_root: str | None) -> str:
    if model_io_root is not None:
        return model_io_root
    if os.getenv("OPENAGENT_MODEL_IO_ROOT") is not None:
        return str(os.getenv("OPENAGENT_MODEL_IO_ROOT"))
    if os.getenv("OPENAGENT_DATA_ROOT") is not None:
        return str(os.path.join(str(os.getenv("OPENAGENT_DATA_ROOT")), "model-io"))
    session_path = os.path.abspath(session_root)
    session_dir = os.path.basename(session_path)
    parent_dir = os.path.basename(os.path.dirname(session_path))
    if session_dir == "sessions" and parent_dir == "host":
        return os.path.join(os.path.dirname(os.path.dirname(session_path)), "data", "model-io")
    if session_dir == "sessions":
        return os.path.join(os.path.dirname(session_path), "data", "model-io")
    return os.path.join(os.path.dirname(session_path), "model-io")
