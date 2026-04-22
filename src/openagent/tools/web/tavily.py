"""Tavily-backed implementation of the builtin web search backend."""

from __future__ import annotations

from dataclasses import dataclass

from openagent.object_model import JsonObject
from openagent.object_model.base import to_json_value
from openagent.tools.web.backends import WebSearchBackend, WebSearchBackendError, WebSearchResult
from openagent.tools.web.transport import (
    UrllibWebBackendHttpTransport,
    WebBackendHttpTransport,
    WebBackendTransportError,
)


@dataclass(slots=True)
class TavilyConfig:
    api_key: str
    base_url: str = "https://api.tavily.com"
    limit: int = 5
    timeout_seconds: float = 30.0

    def search_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/search"

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}


@dataclass(slots=True)
class TavilyWebSearchBackend(WebSearchBackend):
    config: TavilyConfig
    transport: WebBackendHttpTransport = UrllibWebBackendHttpTransport()

    def search(self, query: str) -> list[WebSearchResult]:
        payload: JsonObject = {
            "query": query,
            "max_results": self.config.limit,
        }
        try:
            response = self.transport.post_json(
                self.config.search_url(),
                payload,
                self.config.headers(),
                self.config.timeout_seconds,
            )
        except WebBackendTransportError as exc:
            raise WebSearchBackendError(str(exc)) from exc
        return _extract_tavily_search_results(response.body)


def _extract_tavily_search_results(body: JsonObject) -> list[WebSearchResult]:
    entries = body.get("results")
    if not isinstance(entries, list):
        raise WebSearchBackendError("Tavily search response missing results list")

    results: list[WebSearchResult] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        url = item.get("url")
        if not isinstance(title, str) or not isinstance(url, str):
            continue
        snippet = item.get("content") or item.get("snippet") or ""
        metadata: JsonObject = {"provider": "tavily"}
        for key in ("score", "published_date", "raw_content"):
            value = item.get(key)
            if value is not None and value != "":
                metadata[key] = to_json_value(value)
        results.append(
            WebSearchResult(
                title=title,
                url=url,
                snippet=str(snippet),
                metadata=metadata,
            )
        )
    return results
