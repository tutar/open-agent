"""Environment-backed helpers for builtin web tool defaults."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from openagent.tools.web import (
    BraveConfig,
    BraveWebSearchBackend,
    CallableWebSearchBackend,
    DefaultWebFetchBackend,
    DefaultWebSearchBackend,
    FirecrawlConfig,
    FirecrawlWebFetchBackend,
    FirecrawlWebSearchBackend,
    TavilyConfig,
    TavilyWebSearchBackend,
    WebFetchBackend,
    WebSearchBackend,
)


def default_web_fetch_backend() -> WebFetchBackend:
    backend_name = env_value("OPENAGENT_WEBFETCH_BACKEND", "default").strip().lower()
    if backend_name in {"", "default"}:
        return DefaultWebFetchBackend()
    if backend_name == "firecrawl":
        return FirecrawlWebFetchBackend(firecrawl_config_from_env())
    raise RuntimeError(f"Unsupported OPENAGENT_WEBFETCH_BACKEND: {backend_name}")


def default_web_search_backend(
    backend: WebSearchBackend | Callable[[str], list[dict[str, object]]] | None,
) -> WebSearchBackend:
    if backend is not None:
        if isinstance(backend, WebSearchBackend):
            return backend
        return CallableWebSearchBackend(backend)
    backend_name = env_value("OPENAGENT_WEBSEARCH_BACKEND", "default").strip().lower()
    if backend_name in {"", "default"}:
        return DefaultWebSearchBackend()
    if backend_name == "firecrawl":
        return FirecrawlWebSearchBackend(firecrawl_config_from_env())
    if backend_name == "tavily":
        return TavilyWebSearchBackend(tavily_config_from_env())
    if backend_name == "brave":
        return BraveWebSearchBackend(brave_config_from_env())
    raise RuntimeError(f"Unsupported OPENAGENT_WEBSEARCH_BACKEND: {backend_name}")


def firecrawl_config_from_env() -> FirecrawlConfig:
    base_url = env_value("OPENAGENT_FIRECRAWL_BASE_URL", "").strip()
    if not base_url:
        raise RuntimeError(
            "OPENAGENT_FIRECRAWL_BASE_URL is required when using the firecrawl web backend"
        )
    api_key = env_value("OPENAGENT_FIRECRAWL_API_KEY")
    return FirecrawlConfig(
        base_url=base_url,
        api_key=api_key.strip() if isinstance(api_key, str) and api_key.strip() else None,
    )


def tavily_config_from_env() -> TavilyConfig:
    api_key = env_value("OPENAGENT_TAVILY_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "OPENAGENT_TAVILY_API_KEY is required when using the tavily web search backend"
        )
    return TavilyConfig(
        api_key=api_key,
        base_url=env_value("OPENAGENT_TAVILY_BASE_URL", "https://api.tavily.com").strip(),
        limit=web_search_limit_from_env(),
    )


def brave_config_from_env() -> BraveConfig:
    api_key = env_value("OPENAGENT_BRAVE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "OPENAGENT_BRAVE_API_KEY is required when using the brave web search backend"
        )
    return BraveConfig(
        api_key=api_key,
        base_url=env_value("OPENAGENT_BRAVE_BASE_URL", "https://api.search.brave.com").strip(),
        limit=web_search_limit_from_env(),
    )


def web_search_limit_from_env() -> int:
    raw_limit = env_value("OPENAGENT_WEBSEARCH_LIMIT", "5").strip()
    try:
        limit = int(raw_limit)
    except ValueError as exc:
        raise RuntimeError("OPENAGENT_WEBSEARCH_LIMIT must be a positive integer") from exc
    if limit < 1:
        raise RuntimeError("OPENAGENT_WEBSEARCH_LIMIT must be a positive integer")
    return limit


def env_value(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is not None:
        return value
    return load_dotenv_values().get(name, default)


def load_dotenv_values() -> dict[str, str]:
    dotenv_path = Path(".env")
    if not dotenv_path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = strip_dotenv_value(raw_value.strip())
    return values


def strip_dotenv_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
