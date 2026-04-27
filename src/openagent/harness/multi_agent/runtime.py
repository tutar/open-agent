"""Local multi-agent runtime facade."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from openagent.harness.multi_agent.delegation import (
    LocalDelegationRuntime,
    build_delegated_identity,
)
from openagent.harness.multi_agent.models import (
    DelegatedAgentInvocation,
    DirectViewInput,
    TaskNotificationEnvelope,
    ViewedTranscript,
)
from openagent.harness.multi_agent.projection import ViewedTranscriptProjector
from openagent.harness.multi_agent.routing import TaskNotificationRouter
from openagent.harness.task.background import LocalBackgroundAgentOrchestrator
from openagent.harness.task.interfaces import TaskManager
from openagent.harness.task.retention import TaskRetentionRuntime
from openagent.object_model import JsonObject, TerminalStatus
from openagent.tools import ToolExecutionContext


@dataclass(slots=True)
class LocalMultiAgentRuntime:
    task_manager: TaskManager
    background_orchestrator: LocalBackgroundAgentOrchestrator
    retention: TaskRetentionRuntime
    notification_router: TaskNotificationRouter
    parent_agent_ref: str = "agent_default"
    _workspace_preparer: Callable[[str, str | None, JsonObject | None], str] | None = None
    _delegation: LocalDelegationRuntime | None = None
    _projection: ViewedTranscriptProjector | None = None

    def __post_init__(self) -> None:
        self._delegation = LocalDelegationRuntime(self.background_orchestrator)
        self._projection = ViewedTranscriptProjector(self.task_manager, self.retention)

    def configure_workspace_runtime(
        self,
        workspace_preparer: Callable[[str, str | None, JsonObject | None], str],
        *,
        parent_agent_ref: str,
    ) -> None:
        self._workspace_preparer = workspace_preparer
        self.parent_agent_ref = parent_agent_ref

    def delegate(self, invocation: DelegatedAgentInvocation) -> JsonObject:
        identity = build_delegated_identity(invocation)
        identity.parent_agent_ref = self.parent_agent_ref
        if self._workspace_preparer is not None:
            identity.workspace = self._workspace_preparer(
                identity.agent_id,
                invocation.parent_session_id,
                {
                    "agent_type": identity.agent_type,
                    "parent_agent_ref": self.parent_agent_ref,
                    "invoking_request_id": identity.invoking_request_id,
                },
            )
        assert self._delegation is not None
        if invocation.run_in_background:
            return self._delegation.delegate_background(invocation, identity)
        return self._delegation.delegate_synchronous(invocation, identity)

    def as_agent_handler(
        self,
    ) -> Callable[[dict[str, object], ToolExecutionContext | None], JsonObject]:
        def _handler(
            arguments: dict[str, object],
            context: ToolExecutionContext | None = None,
        ) -> JsonObject:
            prompt = str(arguments.get("task") or arguments.get("prompt") or "").strip()
            if not prompt:
                raise ValueError("Agent tool requires task or prompt")
            request_id = None
            if context is not None and isinstance(context.audit_metadata.get("request_id"), str):
                request_id = str(context.audit_metadata["request_id"])
            elif isinstance(arguments.get("request_id"), str):
                request_id = str(arguments["request_id"])
            invocation = DelegatedAgentInvocation(
                prompt=prompt,
                description=str(arguments.get("description") or prompt),
                agent_type=str(arguments.get("agent_type") or "delegate"),
                run_in_background=bool(arguments.get("run_in_background", False)),
                parent_session_id=context.session_id if context is not None else None,
                invoking_request_id=request_id,
                parent_task_id=context.task_id if context is not None else None,
            )
            return self.delegate(invocation)

        return _handler

    def sync_task_notifications(self, recipient_id: str) -> list[JsonObject]:
        for task in self.task_manager.list():
            if task.notified or str(task.status) not in {
                TerminalStatus.COMPLETED.value,
                TerminalStatus.FAILED.value,
                TerminalStatus.KILLED.value,
            }:
                continue
            if (task.metadata or {}).get("parent_session_id") != recipient_id:
                continue
            self.notification_router.publish_task_notification(
                TaskNotificationEnvelope(
                    recipient_id=recipient_id,
                    task_id=task.task_id,
                    event_type=str(task.status),
                    summary=task.description,
                    payload={
                        "output_ref": task.output_ref,
                        "agent_id": task.agent_id,
                    },
                )
            )
            self.task_manager.mark_notified(task.task_id)
        notifications = self.notification_router.read_task_notifications(recipient_id)
        return [message.to_dict() for message in notifications]

    def send_direct_view_input(self, direct_input: DirectViewInput) -> None:
        self.notification_router.record_direct_view_input(direct_input)

    def read_direct_view_inputs(self, recipient_id: str) -> list[JsonObject]:
        inputs = self.notification_router.read_direct_view_inputs(recipient_id)
        return [message.to_dict() for message in inputs]

    def open_view(self, task_id: str, binding_id: str) -> ViewedTranscript:
        assert self._projection is not None
        self._projection.attach_view(task_id, binding_id)
        return self._projection.project(task_id)

    def close_view(self, task_id: str, binding_id: str) -> None:
        assert self._projection is not None
        self._projection.release_view(task_id, binding_id)

    def project_view(self, task_id: str) -> ViewedTranscript:
        assert self._projection is not None
        return self._projection.project(task_id)
