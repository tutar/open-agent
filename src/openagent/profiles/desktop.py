"""Desktop host profile baseline."""

from __future__ import annotations

from dataclasses import dataclass, field

from openagent.context_governance import ContextGovernance
from openagent.gateway import FileSessionBindingStore, Gateway, InProcessSessionAdapter
from openagent.harness import ModelProviderAdapter, SimpleHarness
from openagent.orchestration import FileTaskManager, InMemoryTaskManager
from openagent.session import FileSessionStore
from openagent.tools import SimpleToolExecutor, StaticToolRegistry, ToolDefinition


@dataclass(slots=True)
class DesktopExtension:
    extension_id: str
    label: str
    enabled: bool = True
    commands: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)


class DesktopExtensionManager:
    """Track desktop-local extension bundles without introducing IPC."""

    def __init__(self) -> None:
        self._extensions: dict[str, DesktopExtension] = {}

    def install(self, extension: DesktopExtension) -> None:
        self._extensions[extension.extension_id] = extension

    def enable(self, extension_id: str) -> None:
        self._extensions[extension_id].enabled = True

    def disable(self, extension_id: str) -> None:
        self._extensions[extension_id].enabled = False

    def list_extensions(self, enabled_only: bool = False) -> list[DesktopExtension]:
        extensions = list(self._extensions.values())
        if enabled_only:
            return [extension for extension in extensions if extension.enabled]
        return extensions


@dataclass(slots=True)
class DesktopProfile:
    """Local desktop profile using direct in-process runtime calls."""

    name: str = "desktop"
    binding_name: str = "in_process"

    def create_runtime(
        self,
        model: ModelProviderAdapter,
        session_root: str,
        tools: list[ToolDefinition] | None = None,
    ) -> SimpleHarness:
        registry = StaticToolRegistry(tools or [])
        return SimpleHarness(
            model=model,
            sessions=FileSessionStore(session_root),
            tools=registry,
            executor=SimpleToolExecutor(registry),
            context_governance=ContextGovernance(storage_dir=session_root),
        )

    def create_gateway(
        self,
        model: ModelProviderAdapter,
        session_root: str,
        tools: list[ToolDefinition] | None = None,
        binding_root: str | None = None,
    ) -> Gateway:
        runtime = self.create_runtime(model=model, session_root=session_root, tools=tools)
        resolved_binding_root = binding_root or f"{session_root}/bindings"
        return Gateway(
            InProcessSessionAdapter(runtime),
            binding_store=FileSessionBindingStore(resolved_binding_root),
        )

    def create_task_manager(self, root: str | None = None) -> InMemoryTaskManager | FileTaskManager:
        if root is not None:
            return FileTaskManager(root)
        return InMemoryTaskManager()

    def create_extension_manager(self) -> DesktopExtensionManager:
        return DesktopExtensionManager()
