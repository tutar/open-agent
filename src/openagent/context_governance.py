"""Context shaping and budget governance baseline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from openagent.object_model import JsonObject, SerializableModel, ToolResult
from openagent.session import SessionMessage


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
class ContextGovernance:
    """Local-first context governance using character-based token estimates."""

    max_tokens: int = 1600
    warning_tokens: int = 1200
    compact_to_messages: int = 6
    overflow_compact_to_messages: int = 3
    externalize_threshold_chars: int = 200
    storage_dir: str | None = None
    reserve_output_tokens: int = 256
    minimum_continuation_tokens: int = 128

    def analyze(self, messages: list[SessionMessage], tools: list[str]) -> ContextReport:
        estimated_tokens = self.estimate_tokens(messages, tools)
        budget_remaining = self.max_tokens - estimated_tokens
        tool_result_count = sum(1 for message in messages if message.role == "tool")
        return ContextReport(
            message_count=len(messages),
            estimated_tokens=estimated_tokens,
            tool_result_count=tool_result_count,
            over_budget=estimated_tokens > self.max_tokens,
            warning_threshold_reached=estimated_tokens >= self.warning_tokens,
            budget_remaining=budget_remaining,
            externalized_tool_count=sum(
                1 for message in messages if "[externalized:" in message.content
            ),
        )

    def estimate_tokens(self, messages: list[SessionMessage], tools: list[str]) -> int:
        message_tokens = sum(max(1, len(message.content) // 4) for message in messages)
        return message_tokens + len(tools) * 8

    def should_compact(self, messages: list[SessionMessage], model: str = "default") -> bool:
        del model
        report = self.analyze(messages, [])
        return report.warning_threshold_reached or report.over_budget

    def compact(self, messages: list[SessionMessage]) -> CompactResult:
        if len(messages) <= self.compact_to_messages:
            return CompactResult(
                messages=[message.to_dict() for message in messages],
                summary="No compaction needed",
                compacted_count=0,
            )

        kept = messages[-self.compact_to_messages :]
        compacted_count = len(messages) - len(kept)
        summary = f"Compacted {compacted_count} earlier messages into recent-context view"
        return CompactResult(
            messages=[message.to_dict() for message in kept],
            summary=summary,
            compacted_count=compacted_count,
        )

    def recover_overflow(self, messages: list[SessionMessage]) -> OverflowRecoveryResult:
        if len(messages) <= self.overflow_compact_to_messages:
            return OverflowRecoveryResult(
                messages=[message.to_dict() for message in messages],
                summary="No overflow recovery needed",
                recovered=False,
            )
        kept = messages[-self.overflow_compact_to_messages :]
        trimmed = len(messages) - len(kept)
        return OverflowRecoveryResult(
            messages=[message.to_dict() for message in kept],
            summary=f"Recovered from overflow by trimming {trimmed} earlier messages",
            recovered=True,
        )

    def build_prompt_cache_plan(self, messages: list[SessionMessage]) -> PromptCachePlan:
        if len(messages) <= 2:
            return PromptCachePlan(
                stable_prefix=[message.to_dict() for message in messages],
                dynamic_suffix=[],
                cache_breakpoints=[],
                provider_cache_key=self._provider_cache_key(messages),
            )
        stable_prefix = [message.to_dict() for message in messages[:-2]]
        dynamic_suffix = [message.to_dict() for message in messages[-2:]]
        return PromptCachePlan(
            stable_prefix=stable_prefix,
            dynamic_suffix=dynamic_suffix,
            cache_breakpoints=[len(stable_prefix)],
            provider_cache_key=self._provider_cache_key(messages),
        )

    def build_continuation_budget_plan(
        self,
        messages: list[SessionMessage],
        tools: list[str],
    ) -> ContinuationBudgetPlan:
        report = self.analyze(messages, tools)
        reserved_output_tokens = min(self.reserve_output_tokens, self.max_tokens)
        available_context_tokens = max(0, self.max_tokens - reserved_output_tokens)
        continuation_message_budget = max(
            0,
            report.budget_remaining - reserved_output_tokens,
        )
        return ContinuationBudgetPlan(
            remaining_tokens=max(0, report.budget_remaining),
            reserved_output_tokens=reserved_output_tokens,
            available_context_tokens=available_context_tokens,
            continuation_message_budget=continuation_message_budget,
            requires_compaction=report.warning_threshold_reached,
            requires_overflow_recovery=report.over_budget,
        )

    def snapshot_prompt_cache(
        self,
        messages: list[SessionMessage],
        tools: list[str],
        *,
        cache_scope: str = "session",
        ttl_bucket: str = "default",
        model_identity: str = "default",
    ) -> PromptCacheSnapshot:
        plan = self.build_prompt_cache_plan(messages)
        dynamic_material = "|".join(
            f"{item['role']}:{item['content']}" for item in plan.dynamic_suffix
        )
        return PromptCacheSnapshot(
            stable_prefix_key=plan.provider_cache_key,
            dynamic_suffix_key=str(abs(hash(dynamic_material))) if dynamic_material else None,
            tool_surface_key="|".join(sorted(tools)),
            cache_scope=cache_scope,
            ttl_bucket=ttl_bucket,
            model_identity=model_identity,
        )

    def detect_cache_break(
        self,
        previous: PromptCacheSnapshot,
        current: PromptCacheSnapshot,
    ) -> PromptCacheBreakResult:
        reason: str | None = None
        expected_miss = False

        if previous.ttl_bucket != current.ttl_bucket:
            reason = "ttl_changed"
            expected_miss = True
        elif previous.cache_scope != current.cache_scope:
            reason = "cache_scope_changed"
            expected_miss = True
        elif previous.model_identity != current.model_identity:
            reason = "model_identity_changed"
            expected_miss = True
        elif previous.tool_surface_key != current.tool_surface_key:
            reason = "tool_surface_changed"
            expected_miss = True
        elif previous.stable_prefix_key != current.stable_prefix_key:
            reason = "prompt_bytes_changed"

        return PromptCacheBreakResult(
            break_detected=reason is not None,
            reason=reason,
            previous_baseline=previous.to_dict(),
            current_baseline=current.to_dict(),
            expected_miss=expected_miss,
        )

    def fork_prompt_cache(
        self,
        parent: PromptCacheSnapshot,
        child_dynamic_suffix: list[SessionMessage],
        *,
        skip_cache_write: bool = False,
    ) -> PromptCacheSnapshot:
        dynamic_material = "|".join(
            f"{message.role}:{message.content}" for message in child_dynamic_suffix
        )
        return PromptCacheSnapshot(
            stable_prefix_key=parent.stable_prefix_key,
            dynamic_suffix_key=str(abs(hash(dynamic_material))) if dynamic_material else None,
            tool_surface_key=parent.tool_surface_key,
            cache_scope=parent.cache_scope,
            ttl_bucket=parent.ttl_bucket,
            model_identity=parent.model_identity,
            skip_cache_write=skip_cache_write,
        )

    def snapshot_prompt_cache_with_strategy(
        self,
        messages: list[SessionMessage],
        tools: list[str],
        strategy: PromptCacheStrategyName | str,
        *,
        cache_scope: str = "session",
        ttl_bucket: str = "default",
        model_identity: str = "default",
    ) -> PromptCacheSnapshot:
        del strategy
        return self.snapshot_prompt_cache(
            messages,
            tools,
            cache_scope=cache_scope,
            ttl_bucket=ttl_bucket,
            model_identity=model_identity,
        )

    def report_for_model_input(
        self,
        messages: list[SessionMessage],
        tools: list[str],
        compacted: bool,
        recovered_from_overflow: bool,
    ) -> ContextReport:
        report = self.analyze(messages, tools)
        cache_plan = self.build_prompt_cache_plan(messages)
        report.compacted = compacted
        report.recovered_from_overflow = recovered_from_overflow
        report.cache_stable_prefix_messages = len(cache_plan.stable_prefix)
        report.cache_dynamic_suffix_messages = len(cache_plan.dynamic_suffix)
        budget_plan = self.build_continuation_budget_plan(messages, tools)
        report.continuation_message_budget = budget_plan.continuation_message_budget
        report.recommended_max_output_tokens = budget_plan.reserved_output_tokens
        report.provider_cache_key = cache_plan.provider_cache_key
        return report

    def should_allow_continuation(
        self,
        messages: list[SessionMessage],
        tools: list[str],
    ) -> bool:
        plan = self.build_continuation_budget_plan(messages, tools)
        return (
            not plan.requires_overflow_recovery
            and plan.continuation_message_budget >= self.minimum_continuation_tokens
        )

    def externalize_tool_result(self, result: ToolResult) -> ToolResult:
        content_text = "\n".join(str(item) for item in result.content)
        if len(content_text) <= self.externalize_threshold_chars:
            return result

        preview = content_text[: self.externalize_threshold_chars]
        persisted_ref: str | None = None
        if self.storage_dir is not None:
            storage_path = Path(self.storage_dir)
            storage_path.mkdir(parents=True, exist_ok=True)
            result_path = storage_path / f"{result.tool_name}.txt"
            result_path.write_text(content_text, encoding="utf-8")
            persisted_ref = str(result_path)

        result.content = [preview]
        result.persisted_ref = persisted_ref
        result.truncated = True
        metadata = dict(result.metadata or {})
        metadata["preview"] = preview
        if persisted_ref is not None:
            metadata["externalized"] = True
            metadata["persisted_ref"] = persisted_ref
        result.metadata = metadata
        return result

    def tool_result_message_content(self, result: ToolResult) -> str:
        content_text = "\n".join(str(item) for item in result.content)
        if result.persisted_ref is None:
            return f"{result.tool_name}: {content_text}"
        return (
            f"{result.tool_name}: {content_text}\n"
            "[tool result externalized to internal storage; this is not a workspace file path "
            "and should not be read with local file tools]"
        )

    def _provider_cache_key(self, messages: list[SessionMessage]) -> str | None:
        if not messages:
            return None
        stable_messages = messages[:-2] if len(messages) > 2 else messages
        key_material = "|".join(f"{message.role}:{message.content}" for message in stable_messages)
        return str(abs(hash(key_material)))
