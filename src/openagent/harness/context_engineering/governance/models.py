"""Context engineering governance models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from openagent.object_model import JsonObject, SerializableModel


@dataclass(slots=True)
class ContextReport(SerializableModel):
    message_count: int
    estimated_tokens: int
    tool_result_count: int
    over_budget: bool
    warning_threshold_reached: bool
    budget_remaining: int
    compacted: bool = False
    recovered_from_overflow: bool = False
    externalized_tool_count: int = 0
    cache_stable_prefix_messages: int = 0
    cache_dynamic_suffix_messages: int = 0
    continuation_message_budget: int = 0
    recommended_max_output_tokens: int = 0
    provider_cache_key: str | None = None


@dataclass(slots=True)
class CompactResult(SerializableModel):
    messages: list[JsonObject]
    summary: str
    compacted_count: int = 0


@dataclass(slots=True)
class OverflowRecoveryResult(SerializableModel):
    messages: list[JsonObject]
    summary: str
    recovered: bool


@dataclass(slots=True)
class PromptCachePlan(SerializableModel):
    stable_prefix: list[JsonObject] = field(default_factory=list)
    dynamic_suffix: list[JsonObject] = field(default_factory=list)
    cache_breakpoints: list[int] = field(default_factory=list)
    provider_cache_key: str | None = None


@dataclass(slots=True)
class PromptCacheSnapshot(SerializableModel):
    stable_prefix_key: str | None
    dynamic_suffix_key: str | None
    tool_surface_key: str
    cache_scope: str = "session"
    ttl_bucket: str = "default"
    model_identity: str = "default"
    skip_cache_write: bool = False


@dataclass(slots=True)
class PromptCacheBreakResult(SerializableModel):
    break_detected: bool
    reason: str | None = None
    previous_baseline: JsonObject = field(default_factory=dict)
    current_baseline: JsonObject = field(default_factory=dict)
    expected_miss: bool = False


class PromptCacheStrategyName(StrEnum):
    ANTHROPIC_NATIVE = "anthropic_native"
    OPENCLOW_MEDIATED = "openclaw_mediated"
    FALLBACK = "fallback"


@dataclass(slots=True)
class ContinuationBudgetPlan(SerializableModel):
    remaining_tokens: int
    reserved_output_tokens: int
    available_context_tokens: int
    continuation_message_budget: int
    requires_compaction: bool
    requires_overflow_recovery: bool


@dataclass(slots=True)
class ExternalizedToolResult(SerializableModel):
    preview: str
    persisted_ref: str | None
    truncated: bool


@dataclass(slots=True)
class ContentExternalizationResult(SerializableModel):
    preview: str
    persisted_ref: str | None
    externalized: bool


@dataclass(slots=True)
class WorkingViewProjection(SerializableModel):
    projected_messages: list[JsonObject] = field(default_factory=list)
    projection_reason: str | None = None
