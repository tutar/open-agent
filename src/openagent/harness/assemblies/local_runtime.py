"""Local runtime assembly helpers used by the public local facade."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import cast

from openagent.gateway.binding_store import FileSessionBindingStore
from openagent.gateway.core import Gateway
from openagent.gateway.interfaces import ChannelAdapter
from openagent.gateway.session_adapter import InProcessSessionAdapter
from openagent.harness.context_engineering import ContextGovernance
from openagent.harness.multi_agent import LocalMultiAgentRuntime, TaskNotificationRouter
from openagent.harness.runtime import FileModelIoCapture
from openagent.harness.runtime.core.agent_runtime import SimpleHarness
from openagent.harness.runtime.io import ModelProviderAdapter
from openagent.harness.task import (
    FileTaskManager,
    LocalBackgroundAgentOrchestrator,
    TaskRetentionPolicy,
    TaskRetentionRuntime,
)
from openagent.object_model import JsonObject
from openagent.observability import AgentObservability
from openagent.session import FileSessionStore
from openagent.shared import (
    DEFAULT_RUNTIME_AGENT_ID,
    normalize_openagent_root,
    resolve_agent_instance_root,
    resolve_agent_root_from_session_root,
    resolve_path_env,
)
from openagent.tools import (
    SimpleToolExecutor,
    StaticToolRegistry,
    ToolDefinition,
    ToolExecutionContext,
    create_builtin_toolset,
)


def create_file_runtime_assembly(
    model: ModelProviderAdapter,
    session_root: str,
    tools: list[ToolDefinition] | None = None,
    observability: AgentObservability | None = None,
    model_io_root: str | None = None,
    openagent_root: str | None = None,
    role_id: str | None = None,
) -> SimpleHarness:
    resolved_openagent_root = _default_openagent_root(openagent_root)
    agent_root = resolve_agent_root_from_session_root(session_root, role_id)
    runtime_agent_root = resolve_agent_instance_root(agent_root, DEFAULT_RUNTIME_AGENT_ID)
    task_manager = FileTaskManager(
        str(runtime_agent_root / "tasks"),
        retention_policy=TaskRetentionPolicy(),
    )
    multi_agent = LocalMultiAgentRuntime(
        task_manager=task_manager,
        background_orchestrator=LocalBackgroundAgentOrchestrator(task_manager),
        retention=TaskRetentionRuntime(task_manager, TaskRetentionPolicy()),
        notification_router=TaskNotificationRouter(),
    )
    registry = StaticToolRegistry(
        _resolve_runtime_tools(
            tools=tools,
            agent_handler=multi_agent.as_agent_handler(),
        )
    )
    harness = SimpleHarness(
        model=model,
        sessions=FileSessionStore(session_root),
        tools=registry,
        executor=SimpleToolExecutor(registry),
        context_governance=ContextGovernance(storage_dir=session_root),
        observability=observability,
        model_io_capture=FileModelIoCapture(
            _default_model_io_root(
                session_root,
                model_io_root,
                runtime_agent_root=str(runtime_agent_root),
            )
        ),
        session_root_dir=session_root,
        openagent_root=resolved_openagent_root,
        agent_root_dir=agent_root,
        role_id=role_id,
    )
    multi_agent.configure_workspace_runtime(
        harness.prepare_delegated_agent_workspace,
        parent_agent_ref=harness.parent_agent_ref,
    )
    return harness


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


def _resolve_runtime_tools(
    tools: list[ToolDefinition] | None,
    agent_handler: (
        Callable[[dict[str, object], ToolExecutionContext | None], JsonObject] | None
    ) = None,
) -> list[ToolDefinition]:
    if tools is not None:
        resolved = list(tools)
        if agent_handler is not None and not any(tool.name == "Agent" for tool in resolved):
            resolved.extend(
                cast(
                    list[ToolDefinition],
                    create_builtin_toolset(agent_handler=agent_handler),
                )
            )
            deduped: dict[str, ToolDefinition] = {}
            for tool in resolved:
                deduped[tool.name] = tool
            return list(deduped.values())
        return resolved
    return cast(
        list[ToolDefinition],
        create_builtin_toolset(agent_handler=agent_handler),
    )


def _default_openagent_root(openagent_root: str | None) -> str:
    return normalize_openagent_root(
        openagent_root,
        default=".openagent",
    )


def _default_model_io_root(
    session_root: str,
    model_io_root: str | None,
    *,
    runtime_agent_root: str | None = None,
) -> str:
    if model_io_root is not None:
        return model_io_root
    if runtime_agent_root is not None:
        return os.path.join(runtime_agent_root, "model-io")
    if resolved_model_io_root := resolve_path_env("OPENAGENT_MODEL_IO_ROOT"):
        return resolved_model_io_root
    if resolved_data_root := resolve_path_env("OPENAGENT_DATA_ROOT"):
        return str(os.path.join(resolved_data_root, "model-io"))
    agent_root = resolve_agent_root_from_session_root(session_root)
    return str(resolve_agent_instance_root(agent_root, DEFAULT_RUNTIME_AGENT_ID) / "model-io")
