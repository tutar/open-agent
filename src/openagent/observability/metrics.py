"""Shared helpers for normalized observability metric emission."""

from __future__ import annotations

from openagent.object_model import JsonObject
from openagent.observability.models import RuntimeMetric


def normalized_duration_metrics(
    *,
    scope: str,
    total_duration_ms: float,
    session_id: str | None = None,
    task_id: str | None = None,
    agent_id: str | None = None,
    callsite: str | None = None,
    model: str | None = None,
    provider_adapter: str | None = None,
    api_kind: str | None = None,
    api_target: str | None = None,
    aggregation: str = "event",
    total_api_duration_ms: float = 0.0,
    extra_attributes: JsonObject | None = None,
) -> list[RuntimeMetric]:
    total_internal_duration_ms = max(total_duration_ms - total_api_duration_ms, 0.0)
    base_attributes: JsonObject = {
        "scope": scope,
        "aggregation": aggregation,
    }
    if callsite is not None:
        base_attributes["callsite"] = callsite
    if model is not None:
        base_attributes["model"] = model
    if provider_adapter is not None:
        base_attributes["provider_adapter"] = provider_adapter
    if api_kind is not None:
        base_attributes["api_kind"] = api_kind
    if api_target is not None:
        base_attributes["api_target"] = api_target
    if extra_attributes:
        base_attributes.update(extra_attributes)
    metrics: list[RuntimeMetric] = []
    for metric_kind, value in (
        ("total_duration_ms", total_duration_ms),
        ("total_api_duration_ms", total_api_duration_ms),
        ("total_internal_duration_ms", total_internal_duration_ms),
    ):
        attributes = dict(base_attributes)
        attributes["metric_kind"] = metric_kind
        metrics.append(
            RuntimeMetric(
                name="openagent_duration_ms",
                value=value,
                unit="ms",
                instrument_kind="gauge",
                session_id=session_id,
                task_id=task_id,
                agent_id=agent_id,
                attributes=attributes,
            )
        )
    return metrics


def normalized_token_usage_metrics(
    *,
    scope: str,
    session_id: str | None = None,
    task_id: str | None = None,
    agent_id: str | None = None,
    callsite: str | None = None,
    model: str | None = None,
    provider_adapter: str | None = None,
    aggregation: str = "event",
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cache_creation_input_tokens: int | None = None,
    cache_read_input_tokens: int | None = None,
    extra_attributes: JsonObject | None = None,
) -> list[RuntimeMetric]:
    base_attributes: JsonObject = {
        "scope": scope,
        "aggregation": aggregation,
        "metric_kind": "token_usage",
    }
    if callsite is not None:
        base_attributes["callsite"] = callsite
    if model is not None:
        base_attributes["model"] = model
    if provider_adapter is not None:
        base_attributes["provider_adapter"] = provider_adapter
    if extra_attributes:
        base_attributes.update(extra_attributes)
    values = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_creation_input_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
    }
    total_tokens = (
        (input_tokens if input_tokens is not None else 0)
        + (output_tokens if output_tokens is not None else 0)
    )
    if input_tokens is not None or output_tokens is not None:
        values["total_tokens"] = total_tokens
    metrics: list[RuntimeMetric] = []
    for token_type, value in values.items():
        if value is None:
            continue
        attributes = dict(base_attributes)
        attributes["token_type"] = token_type
        metrics.append(
            RuntimeMetric(
                name="openagent_token_usage",
                value=float(value),
                instrument_kind="gauge",
                session_id=session_id,
                task_id=task_id,
                agent_id=agent_id,
                attributes=attributes,
            )
        )
        metrics.append(
            RuntimeMetric(
                name="openagent_token_usage_total",
                value=float(value),
                instrument_kind="counter",
                session_id=session_id,
                task_id=task_id,
                agent_id=agent_id,
                attributes=attributes,
            )
        )
    return metrics
