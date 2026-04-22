"""Brave Search-backed implementation of the builtin web search backend."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from openagent.object_model import JsonObject
from openagent.object_model.base import to_json_value
from openagent.tools.web.backends import WebSearchBackend, WebSearchBackendError, WebSearchResult
from openagent.tools.web.transport import (
    UrllibWebBackendHttpTransport,
    WebBackendHttpTransport,
    WebBackendTransportError,
)


@dataclass(slots=True)
class BraveConfig:
    api_key: str
    base_url: str = "https://api.search.brave.com"
    limit: int = 5
    timeout_seconds: float = 30.0

    def search_url(self, query: str) -> str:
        params = urlencode({"q": query, "count": self.limit})
        return f"{self.base_url.rstrip('/')}/res/v1/web/search?{params}"

    def headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "X-Subscription-Token": self.api_key,
        }


@dataclass(slots=True)
class BraveWebSearchBackend(WebSearchBackend):
    config: BraveConfig
    transport: WebBackendHttpTransport = UrllibWebBackendHttpTransport()

    def search(self, query: str) -> list[WebSearchResult]:
        try:
            response = self.transport.get_json(
                self.config.search_url(query),
                self.config.headers(),
                self.config.timeout_seconds,
            )
        except WebBackendTransportError as exc:
            raise WebSearchBackendError(str(exc)) from exc
        return _extract_brave_search_results(response.body)


def _extract_brave_search_results(body: JsonObject) -> list[WebSearchResult]:
    web = body.get("web")
    if not isinstance(web, dict):
        return []
    entries = web.get("results")
    if not isinstance(entries, list):
        return []

    results: list[WebSearchResult] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        url = item.get("url")
        if not isinstance(title, str) or not isinstance(url, str):
            continue
        snippet = item.get("description") or ""
        metadata: JsonObject = {"provider": "brave"}
        for key in ("age", "language", "family_friendly"):
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
