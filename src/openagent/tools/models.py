"""Tool execution models."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from openagent.object_model import JsonObject, RuntimeEvent, SerializableModel
from openagent.object_model.models import ToolResult


class PermissionDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    PASSTHROUGH = "passthrough"


class ToolSource(StrEnum):
    BUILTIN = "builtin"
    PLUGIN = "plugin"
    MCP_ADAPTER = "mcp_adapter"
    GENERATED = "generated"


class ToolVisibility(StrEnum):
    USER = "user"
    MODEL = "model"
    BOTH = "both"


class ToolExecutionEventType(StrEnum):
    STARTED = "started"
    PROGRESS = "progress"
    RESULT = "result"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ToolExecutionAbortReason(StrEnum):
    SIBLING_ERROR = "sibling_error"
    USER_INTERRUPTED = "user_interrupted"
    STREAMING_FALLBACK = "streaming_fallback"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class ToolPolicyOutcome(SerializableModel):
    decision: PermissionDecision
    reason: str | None = None
    explanation: str | None = None
    requires_action_ref: str | None = None
    policy_source: str | None = None
    audit_metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class DenialTrackingState(SerializableModel):
    consecutive_denials: int = 0
    total_denials: int = 0


@dataclass(slots=True)
class DenialFallbackPolicy:
    threshold: int = 3

    def record_denial(self, state: DenialTrackingState) -> DenialTrackingState:
        return DenialTrackingState(
            consecutive_denials=state.consecutive_denials + 1,
            total_denials=state.total_denials + 1,
        )

    def record_success(self, state: DenialTrackingState) -> DenialTrackingState:
        return DenialTrackingState(
            consecutive_denials=0,
            total_denials=state.total_denials,
        )

    def should_fallback_to_prompting(self, state: DenialTrackingState) -> bool:
        return state.consecutive_denials >= self.threshold


@dataclass(slots=True)
class PersistedToolResultRef(SerializableModel):
    ref: str
    media_type: str = "text/plain"
    preview: str | None = None


@dataclass(slots=True)
class ToolCall(SerializableModel):
    tool_name: str
    arguments: JsonObject = field(default_factory=dict)
    call_id: str | None = None

    @property
    def tool_use_id(self) -> str | None:
        return self.call_id


@dataclass(slots=True)
class ToolExecutionContext(SerializableModel):
    session_id: str
    approved_tool_names: list[str] = field(default_factory=list)
    cancellation_check: Callable[[], bool] | None = None
    agent_id: str | None = None
    task_id: str | None = None
    working_directory: str | None = None
    audit_metadata: JsonObject = field(default_factory=dict)
    result_persistence_dir: str | None = None


@dataclass(slots=True)
class ToolProgressUpdate(SerializableModel):
    tool_name: str
    message: str
    progress: float | None = None
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class ContextModifierCommit(SerializableModel):
    execution_id: str
    tool_use_id: str
    stage: str
    order: int
    modifier_ref: str | None = None


@dataclass(slots=True)
class ToolStreamItem(SerializableModel):
    progress: ToolProgressUpdate | None = None
    result: ToolResult | None = None
    context_modifier: ContextModifierCommit | None = None
    persisted_ref: PersistedToolResultRef | None = None


@dataclass(slots=True)
class ToolStreamResult(SerializableModel):
    events: list[RuntimeEvent] = field(default_factory=list)


@dataclass(slots=True)
class ToolRecord(SerializableModel):
    tool_name: str
    source: ToolSource = ToolSource.GENERATED
    visibility: ToolVisibility = ToolVisibility.BOTH
    aliases: list[str] = field(default_factory=list)
    provenance: JsonObject = field(default_factory=dict)
    feature_gate: str | None = None
    host_requirements: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ToolExecutionHandle(SerializableModel):
    execution_id: str
    tool_use_ids: list[str] = field(default_factory=list)
    session_id: str | None = None
    task_id: str | None = None
    started_at: str | None = None


@dataclass(slots=True)
class ToolExecutionEvent(SerializableModel):
    execution_id: str
    tool_use_id: str | None
    type: ToolExecutionEventType
    timestamp: str
    payload: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class ToolExecutionSummary(SerializableModel):
    handle: ToolExecutionHandle
    events: list[ToolExecutionEvent] = field(default_factory=list)
    results: list[ToolResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    context_modifiers: list[ContextModifierCommit] = field(default_factory=list)
