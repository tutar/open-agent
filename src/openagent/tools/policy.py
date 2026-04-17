"""Rule-based policy engine baseline for tool execution."""

from __future__ import annotations

from dataclasses import dataclass, field

from openagent.tools.compat import tool_check_permissions, tool_is_read_only
from openagent.tools.interfaces import ToolDefinition
from openagent.tools.models import (
    DenialFallbackPolicy,
    DenialTrackingState,
    PermissionDecision,
    ToolCall,
    ToolExecutionContext,
    ToolPolicyOutcome,
)


@dataclass(slots=True)
class ToolPolicyRule:
    decision: PermissionDecision
    tool_name: str | None = None
    session_id_prefix: str | None = None
    working_directory_prefix: str | None = None
    read_only: bool | None = None
    reason: str | None = None
    explanation: str | None = None
    policy_source: str | None = None

    def matches(
        self,
        tool: ToolDefinition,
        tool_call: ToolCall,
        context: ToolExecutionContext,
    ) -> bool:
        if self.tool_name is not None and tool.name != self.tool_name:
            return False
        if self.session_id_prefix is not None and not context.session_id.startswith(
            self.session_id_prefix
        ):
            return False
        if self.working_directory_prefix is not None:
            working_directory = context.working_directory or ""
            if not working_directory.startswith(self.working_directory_prefix):
                return False
        if (
            self.read_only is not None
            and tool_is_read_only(tool, tool_call.arguments) != self.read_only
        ):
            return False
        return True


@dataclass(slots=True)
class RuleBasedToolPolicyEngine:
    rules: list[ToolPolicyRule] = field(default_factory=list)
    fallback_to_tool_policy: bool = True
    denial_fallback_policy: DenialFallbackPolicy = field(default_factory=DenialFallbackPolicy)
    _denial_tracking: dict[str, DenialTrackingState] = field(default_factory=dict)

    def evaluate(
        self,
        tool: ToolDefinition,
        tool_call: ToolCall,
        context: ToolExecutionContext,
    ) -> ToolPolicyOutcome:
        for rule in self.rules:
            if not rule.matches(tool, tool_call, context):
                continue
            if rule.decision is PermissionDecision.PASSTHROUGH:
                continue
            outcome = ToolPolicyOutcome(
                decision=rule.decision,
                reason=rule.reason,
                explanation=rule.explanation,
                policy_source=rule.policy_source or "rule",
                audit_metadata={"matched_rule": rule.tool_name or "*"},
            )
            return self._track(context.session_id, outcome)

        if self.fallback_to_tool_policy:
            outcome = ToolPolicyOutcome(
                decision=tool_check_permissions(tool, tool_call.arguments, context),
                policy_source="tool.check_permissions",
            )
            return self._track(context.session_id, outcome)

        return self._track(
            context.session_id,
            ToolPolicyOutcome(
                decision=PermissionDecision.ASK,
                reason="No matching policy rule",
                policy_source="policy.default",
            ),
        )

    def _track(self, session_id: str, outcome: ToolPolicyOutcome) -> ToolPolicyOutcome:
        state = self._denial_tracking.get(session_id, DenialTrackingState())
        if outcome.decision is PermissionDecision.ALLOW:
            state = self.denial_fallback_policy.record_success(state)
        elif outcome.decision is PermissionDecision.DENY:
            state = self.denial_fallback_policy.record_denial(state)
            if self.denial_fallback_policy.should_fallback_to_prompting(state):
                outcome = ToolPolicyOutcome(
                    decision=PermissionDecision.ASK,
                    reason=outcome.reason or "Repeated denials fallback to ask",
                    explanation=outcome.explanation,
                    requires_action_ref=outcome.requires_action_ref,
                    policy_source=outcome.policy_source,
                    audit_metadata={
                        **outcome.audit_metadata,
                        "fallback_to_prompting": True,
                        "consecutive_denials": state.consecutive_denials,
                        "total_denials": state.total_denials,
                    },
                )
        self._denial_tracking[session_id] = state
        if outcome.audit_metadata:
            outcome.audit_metadata = {
                **outcome.audit_metadata,
                "consecutive_denials": state.consecutive_denials,
                "total_denials": state.total_denials,
            }
        return outcome
