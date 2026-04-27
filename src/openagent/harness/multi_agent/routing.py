"""Message routing for local multi-agent coordination."""

from __future__ import annotations

from collections import defaultdict

from openagent.harness.multi_agent.models import (
    DirectViewInput,
    InterAgentChannel,
    InterAgentMessage,
    TaskNotificationEnvelope,
)


class TaskNotificationRouter:
    """Route task notifications and direct-view inputs to scoped recipients."""

    def __init__(self) -> None:
        self._task_notifications: dict[str, list[InterAgentMessage]] = defaultdict(list)
        self._direct_view_inputs: dict[str, list[InterAgentMessage]] = defaultdict(list)

    def publish_task_notification(self, envelope: TaskNotificationEnvelope) -> None:
        message = InterAgentMessage(
            channel=InterAgentChannel.TASK_NOTIFICATION,
            sender={"type": "task_runtime", "task_id": envelope.task_id},
            recipient={"type": "leader", "recipient_id": envelope.recipient_id},
            summary=envelope.summary,
            payload={"event_type": envelope.event_type, **envelope.payload},
        )
        self._task_notifications[envelope.recipient_id].append(message)

    def read_task_notifications(self, recipient_id: str) -> list[InterAgentMessage]:
        messages = list(self._task_notifications.get(recipient_id, []))
        self._task_notifications[recipient_id] = []
        return messages

    def record_direct_view_input(self, direct_input: DirectViewInput) -> None:
        message = InterAgentMessage(
            channel=InterAgentChannel.DIRECT_VIEW_INPUT,
            sender={"type": "leader", "sender_id": direct_input.sender_id},
            recipient={"type": "worker", "recipient_id": direct_input.recipient_id},
            payload={"content": direct_input.content, **dict(direct_input.metadata)},
        )
        self._direct_view_inputs[direct_input.recipient_id].append(message)

    def read_direct_view_inputs(self, recipient_id: str) -> list[InterAgentMessage]:
        messages = list(self._direct_view_inputs.get(recipient_id, []))
        self._direct_view_inputs[recipient_id] = []
        return messages
