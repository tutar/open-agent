"""Default builtin web backend implementations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from openagent.object_model.base import to_json_value
from openagent.tools.web.backends import (
    WebDocument,
    WebFetchBackend,
    WebFetchBackendError,
    WebSearchBackend,
    WebSearchResult,
)


@dataclass(slots=True)
class DefaultWebFetchBackend(WebFetchBackend):
    user_agent: str = "openagent/0.1"
    timeout_seconds: float = 10.0

    def fetch(self, url: str) -> WebDocument:
        request = Request(url, headers={"User-Agent": self.user_agent})
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8", errors="replace")
        except Exception as exc:  # pragma: no cover - stdlib network errors vary by runtime
            raise WebFetchBackendError(str(exc)) from exc
        return WebDocument(
            url=url,
            content=body,
            content_format="text",
        )


@dataclass(slots=True)
class DefaultWebSearchBackend(WebSearchBackend):
    def search(self, query: str) -> list[WebSearchResult]:
        return [
            WebSearchResult(
                title=f"Search result for {query}",
                url=f"https://duckduckgo.com/?q={quote_plus(query)}",
                snippet=(
                    "No host-integrated search backend configured; "
                    "returning a search URL placeholder."
                ),
            )
        ]


@dataclass(slots=True)
class CallableWebSearchBackend(WebSearchBackend):
    callback: Callable[[str], list[dict[str, object]]]

    def search(self, query: str) -> list[WebSearchResult]:
        results = self.callback(query)
        normalized: list[WebSearchResult] = []
        for item in results:
            title = str(item.get("title", ""))
            url = str(item.get("url", ""))
            snippet = str(item.get("snippet", ""))
            metadata = {
                key: to_json_value(value)
                for key, value in item.items()
                if key not in {"title", "url", "snippet"}
            }
            normalized.append(
                WebSearchResult(
                    title=title,
                    url=url,
                    snippet=snippet,
                    metadata=metadata,
                )
            )
        return normalized
