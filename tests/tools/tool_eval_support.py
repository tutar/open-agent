from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse
from urllib import request
from urllib.error import HTTPError, URLError

import pytest


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
