"""Compatibility helpers for evolving tool interfaces."""

from __future__ import annotations

from collections.abc import Callable
from inspect import Parameter, Signature, signature
from typing import Any, cast

from openagent.object_model import ToolResult
from openagent.tools.models import (
    PermissionDecision,
    PersistedToolResultRef,
    ToolExecutionContext,
)


def tool_aliases(tool: object) -> list[str]:
    aliases = getattr(tool, "aliases", [])
    if isinstance(aliases, (list, tuple)):
        return [str(alias) for alias in aliases]
    return []


def tool_description(
    tool: object,
    arguments: dict[str, Any] | None = None,
    describe_context: dict[str, Any] | None = None,
) -> str:
    describe = cast(Callable[..., str], getattr(tool, "description"))
    return str(
        _invoke_with_optional_args(
            describe,
            {
                "arguments": arguments or {},
                "input": arguments or {},
                "describe_context": describe_context or {},
                "context": describe_context or {},
            },
        )
    )


def tool_is_enabled(tool: object, context: ToolExecutionContext | None = None) -> bool:
    enabled = getattr(tool, "is_enabled", None)
    if not callable(enabled):
        return True
    result = _invoke_with_optional_args(enabled, {"context": context})
    return bool(result)


def tool_is_read_only(tool: object, arguments: dict[str, Any]) -> bool:
    read_only = getattr(tool, "is_read_only", None)
    if not callable(read_only):
        destructive = getattr(tool, "is_destructive", False)
        return not bool(destructive)
    return bool(
        _invoke_with_optional_args(
            read_only,
            {"arguments": arguments, "input": arguments},
        )
    )


def tool_is_concurrency_safe(tool: object, arguments: dict[str, Any]) -> bool:
    fn = getattr(tool, "is_concurrency_safe", None)
    if not callable(fn):
        return False
    return bool(
        _invoke_with_optional_args(
            fn,
            {"arguments": arguments, "input": arguments},
        )
    )


def tool_validate_input(tool: object, arguments: dict[str, Any]) -> dict[str, Any]:
    validator = getattr(tool, "validate_input", None)
    if not callable(validator):
        return arguments
    validated = _invoke_with_optional_args(
        validator,
        {"arguments": arguments, "input": arguments},
    )
    if isinstance(validated, dict):
        return dict(validated)
    return arguments


def tool_check_permissions(
    tool: object,
    arguments: dict[str, Any],
    context: ToolExecutionContext,
) -> PermissionDecision:
    checker = getattr(tool, "check_permissions")
    raw = _invoke_with_optional_args(
        checker,
        {
            "arguments": arguments,
            "input": arguments,
            "tool_use_context": context,
            "context": context,
        },
    )
    if isinstance(raw, PermissionDecision):
        return raw
    return PermissionDecision(str(raw))


def tool_call(
    tool: object,
    arguments: dict[str, Any],
    context: ToolExecutionContext,
    progress_handler: Callable[[str, float | None], None] | None = None,
) -> ToolResult:
    call = getattr(tool, "call")
    result = _invoke_with_optional_args(
        call,
        {
            "arguments": arguments,
            "input": arguments,
            "tool_use_context": context,
            "context": context,
            "progress_handler": progress_handler,
        },
    )
    if isinstance(result, ToolResult):
        return result
    raise TypeError(
        f"Tool {getattr(tool, 'name', type(tool).__name__)} returned invalid ToolResult"
    )


def tool_map_result(tool: object, result: ToolResult, tool_use_id: str | None) -> ToolResult:
    mapper = getattr(tool, "map_result", None)
    if callable(mapper):
        mapped = _invoke_with_optional_args(
            mapper,
            {
                "result": result,
                "tool_use_id": tool_use_id,
            },
        )
        if isinstance(mapped, ToolResult):
            return mapped
    if tool_use_id is not None:
        metadata = dict(result.metadata or {})
        metadata["tool_use_id"] = tool_use_id
        result.metadata = metadata
    return result


def tool_supports_result_persistence(tool: object) -> bool:
    return bool(getattr(tool, "supports_result_persistence", False))


def persisted_ref_to_string(ref: PersistedToolResultRef | str | None) -> str | None:
    if ref is None:
        return None
    if isinstance(ref, PersistedToolResultRef):
        return ref.ref
    return str(ref)


def _invoke_with_optional_args(
    fn: Callable[..., Any],
    available: dict[str, Any],
) -> Any:
    sig = signature(fn)
    if _has_varargs(sig):
        return fn(**available)
    kwargs: dict[str, Any] = {}
    positional_values: list[Any] = []
    for parameter in sig.parameters.values():
        if parameter.kind in {Parameter.VAR_POSITIONAL, Parameter.VAR_KEYWORD}:
            continue
        if parameter.name == "self":
            continue
        if parameter.kind in {Parameter.POSITIONAL_ONLY, Parameter.POSITIONAL_OR_KEYWORD}:
            if parameter.name in available:
                positional_values.append(available[parameter.name])
            elif parameter.default is Parameter.empty:
                raise TypeError(f"Cannot satisfy required parameter {parameter.name}")
            continue
        if parameter.kind is Parameter.KEYWORD_ONLY and parameter.name in available:
            kwargs[parameter.name] = available[parameter.name]
    return fn(*positional_values, **kwargs)


def _has_varargs(sig: Signature) -> bool:
    return any(
        parameter.kind in {Parameter.VAR_KEYWORD, Parameter.VAR_POSITIONAL}
        for parameter in sig.parameters.values()
    )
