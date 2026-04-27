"""Delegation helpers for local multi-agent execution."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import count

from openagent.harness.multi_agent.models import (
    DelegatedAgentIdentity,
    DelegatedAgentInvocation,
)
from openagent.harness.task import BackgroundTaskContext
from openagent.harness.task.background import LocalBackgroundAgentOrchestrator
from openagent.object_model import JsonObject

_AGENT_COUNTER = count(1)


def build_delegated_identity(invocation: DelegatedAgentInvocation) -> DelegatedAgentIdentity:
    return DelegatedAgentIdentity(
        agent_id=f"delegated-agent-{next(_AGENT_COUNTER)}",
        agent_type=invocation.agent_type,
        parent_session_id=invocation.parent_session_id,
        invoking_request_id=invocation.invoking_request_id,
        invocation_kind="background" if invocation.run_in_background else "synchronous",
    )


@dataclass(slots=True)
class LocalDelegationRuntime:
    orchestrator: LocalBackgroundAgentOrchestrator

    def delegate_background(
        self,
        invocation: DelegatedAgentInvocation,
        identity: DelegatedAgentIdentity,
    ) -> JsonObject:
        description = invocation.description or invocation.prompt
        metadata: JsonObject = {
            "delegated_agent_id": identity.agent_id,
            "delegated_agent_type": identity.agent_type,
            "parent_agent_ref": identity.parent_agent_ref,
            "parent_session_id": identity.parent_session_id,
            "invoking_request_id": identity.invoking_request_id,
            "workspace": identity.workspace,
        }
        handle = self.orchestrator.start_background_task(
            description,
            lambda context: _run_background_delegate(context, invocation, identity),
            metadata=metadata,
            session_id=identity.parent_session_id,
            agent_id=identity.agent_id,
            parent_task_id=invocation.parent_task_id,
        )
        return {
            "mode": "background",
            "task_id": handle.task_id,
            "agent": identity.to_dict(),
            "status": handle.status,
            "workspace": identity.workspace,
        }

    def delegate_synchronous(
        self,
        invocation: DelegatedAgentInvocation,
        identity: DelegatedAgentIdentity,
    ) -> JsonObject:
        return {
            "mode": "synchronous",
            "agent": identity.to_dict(),
            "summary": f"Delegated worker accepted task: {invocation.prompt}",
            "output_ref": f"memory://delegates/{identity.agent_id}/result",
            "workspace": identity.workspace,
        }


def _run_background_delegate(
    context: BackgroundTaskContext,
    invocation: DelegatedAgentInvocation,
    identity: DelegatedAgentIdentity,
) -> JsonObject:
    context.output(
        {
            "summary": invocation.prompt,
            "worker_agent_id": identity.agent_id,
        }
    )
    return {
        "summary": f"Background delegate finished: {invocation.prompt}",
        "worker_agent_id": identity.agent_id,
        "worker_task_id": context.task_id,
    }
