"""Prompt cache strategy helpers."""

from __future__ import annotations

from dataclasses import dataclass

from openagent.harness.context_engineering.governance.models import (
    ContinuationBudgetPlan,
    PromptCacheBreakResult,
    PromptCachePlan,
    PromptCacheSnapshot,
)
from openagent.session import SessionMessage


def provider_cache_key(messages: list[SessionMessage]) -> str | None:
    if not messages:
        return None
    stable_messages = messages[:-2] if len(messages) > 2 else messages
    key_material = "|".join(f"{message.role}:{message.content}" for message in stable_messages)
    return str(abs(hash(key_material)))


def build_prompt_cache_plan(messages: list[SessionMessage]) -> PromptCachePlan:
    if len(messages) <= 2:
        return PromptCachePlan(
            stable_prefix=[message.to_dict() for message in messages],
            dynamic_suffix=[],
            cache_breakpoints=[],
            provider_cache_key=provider_cache_key(messages),
        )
    stable_prefix = [message.to_dict() for message in messages[:-2]]
    dynamic_suffix = [message.to_dict() for message in messages[-2:]]
    return PromptCachePlan(
        stable_prefix=stable_prefix,
        dynamic_suffix=dynamic_suffix,
        cache_breakpoints=[len(stable_prefix)],
        provider_cache_key=provider_cache_key(messages),
    )


def snapshot_prompt_cache(
    messages: list[SessionMessage],
    tools: list[str],
    *,
    cache_scope: str = "session",
    ttl_bucket: str = "default",
    model_identity: str = "default",
) -> PromptCacheSnapshot:
    plan = build_prompt_cache_plan(messages)
    dynamic_material = "|".join(f"{item['role']}:{item['content']}" for item in plan.dynamic_suffix)
    return PromptCacheSnapshot(
        stable_prefix_key=plan.provider_cache_key,
        dynamic_suffix_key=str(abs(hash(dynamic_material))) if dynamic_material else None,
        tool_surface_key="|".join(sorted(tools)),
        cache_scope=cache_scope,
        ttl_bucket=ttl_bucket,
        model_identity=model_identity,
    )


def detect_cache_break(
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


def build_continuation_budget_plan(
    remaining_tokens: int,
    max_tokens: int,
    reserve_output_tokens: int,
    warning_threshold_reached: bool,
    over_budget: bool,
) -> ContinuationBudgetPlan:
    reserved_output_tokens = min(reserve_output_tokens, max_tokens)
    available_context_tokens = max(0, max_tokens - reserved_output_tokens)
    continuation_message_budget = max(0, remaining_tokens - reserved_output_tokens)
    return ContinuationBudgetPlan(
        remaining_tokens=max(0, remaining_tokens),
        reserved_output_tokens=reserved_output_tokens,
        available_context_tokens=available_context_tokens,
        continuation_message_budget=continuation_message_budget,
        requires_compaction=warning_threshold_reached,
        requires_overflow_recovery=over_budget,
    )


@dataclass(slots=True)
class PromptCacheStrategy:
    def prepare_cacheable_prefix(self, messages: list[SessionMessage]) -> PromptCachePlan:
        return build_prompt_cache_plan(messages)

    def place_cache_markers(self, messages: list[SessionMessage]) -> PromptCachePlan:
        return build_prompt_cache_plan(messages)

    def build_cache_policy(
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
