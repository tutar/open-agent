from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib import request
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

from typing import Any

import pytest


@dataclass(slots=True)
class ProviderTurnResult:
    streaming: bool
    finish_reason: str | None
    content: str
    tool_calls: list[dict[str, Any]]
    raw_response: dict[str, Any] | None = None
    raw_events: list[dict[str, Any]] | None = None


def load_repo_env() -> None:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value
    _ensure_local_endpoint_bypasses_proxy()


def _ensure_local_endpoint_bypasses_proxy() -> None:
    base_url = os.getenv("OPENAGENT_BASE_URL", "").strip()
    if not base_url:
        return
    hostname = urlparse(base_url).hostname
    if hostname not in {"127.0.0.1", "localhost"}:
        return
    no_proxy_entries = [
        entry.strip()
        for entry in os.getenv("NO_PROXY", os.getenv("no_proxy", "")).split(",")
        if entry.strip()
    ]
    for host in ("127.0.0.1", "localhost"):
        if host not in no_proxy_entries:
            no_proxy_entries.append(host)
    joined = ",".join(no_proxy_entries)
    os.environ["NO_PROXY"] = joined
    os.environ["no_proxy"] = joined


def live_tool_selection_eval_enabled() -> bool:
    load_repo_env()
    return bool(
        os.getenv("OPENAGENT_RUN_TOOL_SELECTION_EVAL")
        and os.getenv("OPENAGENT_MODEL")
        and os.getenv("OPENAGENT_BASE_URL")
    )


def require_live_model_endpoint() -> None:
    load_repo_env()
    provider = os.getenv("OPENAGENT_PROVIDER", "openai").strip().lower()
    base_url = os.getenv("OPENAGENT_BASE_URL", "").strip()
    model = os.getenv("OPENAGENT_MODEL", "").strip()
    if provider != "openai" or not base_url:
        return
    models_url = f"{base_url.rstrip('/')}/v1/models"
    http_request = request.Request(url=models_url, method="GET")
    opener = request.build_opener(request.ProxyHandler({}))
    try:
        with opener.open(http_request, timeout=5.0) as response:
            if response.status >= 400:
                raise RuntimeError(f"unexpected status {response.status}")
    except (HTTPError, URLError, TimeoutError, RuntimeError) as exc:
        pytest.fail(
            "live tool eval requires a reachable OpenAI-compatible endpoint; "
            f"provider={provider} model={model} base_url={base_url} "
            f"models_url={models_url} error={exc}"
        )


def provider_summary() -> str:
    load_repo_env()
    provider = os.getenv("OPENAGENT_PROVIDER", "openai").strip().lower()
    base_url = os.getenv("OPENAGENT_BASE_URL", "").strip()
    model = os.getenv("OPENAGENT_MODEL", "").strip()
    return f"provider={provider} model={model} base_url={base_url}"


def configured_provider() -> str:
    load_repo_env()
    return os.getenv("OPENAGENT_PROVIDER", "openai").strip().lower()


def live_tool_eval_timeout_seconds(default: float = 90.0) -> float:
    load_repo_env()
    raw_value = os.getenv("OPENAGENT_TOOL_EVAL_TIMEOUT_SEC", "").strip()
    if not raw_value:
        return default
    try:
        parsed = float(raw_value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def scenario_filter_names(env_var: str) -> set[str]:
    load_repo_env()
    raw_value = os.getenv(env_var, "").strip()
    if not raw_value:
        return set()
    return {item.strip() for item in raw_value.split(",") if item.strip()}


def latest_model_io_row(model_io_root: Path) -> dict[str, Any] | None:
    index_path = model_io_root / "index.jsonl"
    if not index_path.exists():
        return None
    lines = [line for line in index_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return None
    loaded = json.loads(lines[-1])
    return loaded if isinstance(loaded, dict) else None


def latest_provider_row_with_tool_messages(model_io_root: Path) -> dict[str, Any] | None:
    index_path = model_io_root / "index.jsonl"
    if not index_path.exists():
        return None
    rows = [
        json.loads(line)
        for line in index_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for row in reversed(rows):
        if not isinstance(row, dict):
            continue
        projected = row.get("provider_projected_messages")
        if not isinstance(projected, list):
            continue
        if any(isinstance(message, dict) and message.get("role") == "tool" for message in projected):
            return row
    return None


def provider_tool_messages(row: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(row, dict):
        return []
    projected = row.get("provider_projected_messages")
    if not isinstance(projected, list):
        return []
    return [
        dict(message)
        for message in projected
        if isinstance(message, dict) and message.get("role") == "tool"
    ]


def build_openai_function_tool(
    *,
    name: str,
    description: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


def openai_chat_completion_non_stream(
    *,
    base_url: str,
    payload: dict[str, Any],
    timeout_sec: float = 60.0,
) -> ProviderTurnResult:
    body = _post_json(base_url, payload, timeout_sec)
    parsed = json.loads(body)
    choice = parsed["choices"][0]
    message = choice.get("message", {})
    tool_calls = message.get("tool_calls")
    return ProviderTurnResult(
        streaming=False,
        finish_reason=choice.get("finish_reason"),
        content=message.get("content") or "",
        tool_calls=tool_calls if isinstance(tool_calls, list) else [],
        raw_response=parsed,
    )


def openai_chat_completion_stream(
    *,
    base_url: str,
    payload: dict[str, Any],
    timeout_sec: float = 60.0,
) -> ProviderTurnResult:
    events = _post_json_stream(base_url, payload, timeout_sec)
    tool_call_parts: dict[int, dict[str, Any]] = {}
    content_parts: list[str] = []
    finish_reason: str | None = None
    for event in events:
        choices = event.get("choices")
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if isinstance(delta, dict):
                content = delta.get("content")
                if isinstance(content, str) and content:
                    content_parts.append(content)
                raw_tool_calls = delta.get("tool_calls")
                if isinstance(raw_tool_calls, list):
                    _accumulate_tool_call_deltas(tool_call_parts, raw_tool_calls)
            if isinstance(choice.get("finish_reason"), str):
                finish_reason = choice["finish_reason"]
    return ProviderTurnResult(
        streaming=True,
        finish_reason=finish_reason,
        content="".join(content_parts),
        tool_calls=_finalize_stream_tool_calls(tool_call_parts),
        raw_events=events,
    )


def _post_json(base_url: str, payload: dict[str, Any], timeout_sec: float) -> str:
    opener = request.build_opener(request.ProxyHandler({}))
    http_request = request.Request(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with opener.open(http_request, timeout=timeout_sec) as response:
        return response.read().decode("utf-8")


def _post_json_stream(
    base_url: str,
    payload: dict[str, Any],
    timeout_sec: float,
) -> list[dict[str, Any]]:
    opener = request.build_opener(request.ProxyHandler({}))
    http_request = request.Request(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    events: list[dict[str, Any]] = []
    with opener.open(http_request, timeout=timeout_sec) as response:
        event_lines: list[str] = []
        for raw_line in response:
            line = raw_line.decode("utf-8").rstrip("\r\n")
            if not line:
                if event_lines:
                    payload_text = "\n".join(event_lines)
                    event_lines = []
                    if payload_text == "[DONE]":
                        break
                    events.append(json.loads(payload_text))
                continue
            if line.startswith("data:"):
                event_lines.append(line.removeprefix("data:").strip())
        if event_lines:
            payload_text = "\n".join(event_lines)
            if payload_text != "[DONE]":
                events.append(json.loads(payload_text))
    return events


def _accumulate_tool_call_deltas(
    tool_call_parts: dict[int, dict[str, Any]],
    raw_tool_calls: list[dict[str, Any]],
) -> None:
    for raw_item in raw_tool_calls:
        if not isinstance(raw_item, dict):
            continue
        index = raw_item.get("index")
        if not isinstance(index, int):
            continue
        entry = tool_call_parts.setdefault(
            index,
            {"id": None, "name": "", "arguments_text": ""},
        )
        if raw_item.get("id") is not None:
            entry["id"] = str(raw_item["id"])
        function = raw_item.get("function")
        if isinstance(function, dict):
            if function.get("name") is not None:
                entry["name"] = str(function["name"])
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                entry["arguments_text"] = str(entry.get("arguments_text", "")) + arguments


def _finalize_stream_tool_calls(tool_call_parts: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    tool_calls: list[dict[str, Any]] = []
    for index in sorted(tool_call_parts):
        entry = tool_call_parts[index]
        arguments_text = str(entry.get("arguments_text", "")).strip()
        parsed_arguments: dict[str, Any] = {}
        if arguments_text:
            loaded = json.loads(arguments_text)
            if isinstance(loaded, dict):
                parsed_arguments = loaded
        tool_calls.append(
            {
                "id": entry.get("id"),
                "type": "function",
                "function": {
                    "name": entry.get("name", ""),
                    "arguments": parsed_arguments,
                },
            }
        )
    return tool_calls
