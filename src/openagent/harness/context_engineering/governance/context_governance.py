"""Context shaping and budget governance baseline."""

from __future__ import annotations

from dataclasses import dataclass

from openagent.harness.context_engineering.governance.context_editing import (
    externalize_tool_result,
    tool_result_message_content,
)
from openagent.harness.context_engineering.governance.models import (
    CompactResult,
    ContextReport,
    ContinuationBudgetPlan,
    OverflowRecoveryResult,
    PromptCacheBreakResult,
    PromptCachePlan,
    PromptCacheSnapshot,
    PromptCacheStrategyName,
)
from openagent.harness.context_engineering.governance.prompt_cache_strategy import (
    build_continuation_budget_plan,
    build_prompt_cache_plan,
    detect_cache_break,
    fork_prompt_cache,
    provider_cache_key,
    snapshot_prompt_cache,
)
from openagent.object_model import ToolResult
from openagent.session import SessionMessage


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

    def _retain_recent_messages(
        self,
        messages: list[SessionMessage],
        *,
        limit: int,
    ) -> list[SessionMessage]:
        if len(messages) <= limit:
            return list(messages)
        kept = list(messages[-limit:])
        if any(message.role == "user" for message in kept):
            return kept
        latest_user_index = next(
            (index for index in range(len(messages) - 1, -1, -1) if messages[index].role == "user"),
            None,
        )
        if latest_user_index is None:
            return kept
        kept_indexes = list(range(len(messages) - limit, len(messages)))
        if latest_user_index not in kept_indexes:
            kept_indexes.append(latest_user_index)
        return [messages[index] for index in sorted(set(kept_indexes))]

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
        kept = self._retain_recent_messages(messages, limit=self.compact_to_messages)
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
        kept = self._retain_recent_messages(
            messages,
            limit=self.overflow_compact_to_messages,
        )
        trimmed = len(messages) - len(kept)
        return OverflowRecoveryResult(
            messages=[message.to_dict() for message in kept],
            summary=f"Recovered from overflow by trimming {trimmed} earlier messages",
            recovered=True,
        )

    def build_prompt_cache_plan(self, messages: list[SessionMessage]) -> PromptCachePlan:
        return build_prompt_cache_plan(messages)

    def build_continuation_budget_plan(
        self,
        messages: list[SessionMessage],
        tools: list[str],
    ) -> ContinuationBudgetPlan:
        report = self.analyze(messages, tools)
        return build_continuation_budget_plan(
            report.budget_remaining,
            self.max_tokens,
            self.reserve_output_tokens,
            report.warning_threshold_reached,
            report.over_budget,
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
        return snapshot_prompt_cache(
            messages,
            tools,
            cache_scope=cache_scope,
            ttl_bucket=ttl_bucket,
            model_identity=model_identity,
        )

    def detect_cache_break(
        self,
        previous: PromptCacheSnapshot,
        current: PromptCacheSnapshot,
    ) -> PromptCacheBreakResult:
        return detect_cache_break(previous, current)

    def fork_prompt_cache(
        self,
        parent: PromptCacheSnapshot,
        child_dynamic_suffix: list[SessionMessage],
        *,
        skip_cache_write: bool = False,
    ) -> PromptCacheSnapshot:
        return fork_prompt_cache(
            parent,
            child_dynamic_suffix,
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
        return externalize_tool_result(
            result,
            externalize_threshold_chars=self.externalize_threshold_chars,
            storage_dir=self.storage_dir,
        )

    def tool_result_message_content(self, result: ToolResult) -> str:
        return tool_result_message_content(result)

    def _provider_cache_key(self, messages: list[SessionMessage]) -> str | None:
        return provider_cache_key(messages)
