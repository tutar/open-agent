"""Firecrawl-backed implementations of builtin web tool backends."""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urlparse

from openagent.object_model import JsonObject
from openagent.tools.web.backends import (
    WebDocument,
    WebFetchBackend,
    WebFetchBackendError,
    WebSearchBackend,
    WebSearchBackendError,
    WebSearchResult,
)
from openagent.tools.web.transport import (
    UrllibWebBackendHttpTransport,
    WebBackendHttpTransport,
    WebBackendTransportError,
)


@dataclass(slots=True)
class FirecrawlConfig:
    base_url: str
    api_key: str | None = None
    timeout_seconds: float = 30.0

    def scrape_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/v2/scrape"

    def search_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/v2/search"

    def headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers


@dataclass(slots=True)
class FirecrawlWebFetchBackend(WebFetchBackend):
    config: FirecrawlConfig
    transport: WebBackendHttpTransport = UrllibWebBackendHttpTransport()

    def fetch(self, url: str) -> WebDocument:
        normalized_url = _normalize_fetch_url(url)
        payload: JsonObject = {
            "url": normalized_url,
            "formats": ["markdown"],
            "onlyMainContent": True,
        }
        try:
            response = self.transport.post_json(
                self.config.scrape_url(),
                payload,
                self.config.headers(),
                self.config.timeout_seconds,
            )
        except WebBackendTransportError as exc:
            raise WebFetchBackendError(_summarize_firecrawl_transport_error(str(exc))) from exc
        document = _extract_firecrawl_document(normalized_url, response.body.get("data"))
        if document is None:
            raise WebFetchBackendError("Firecrawl scrape response missing markdown data")
        return document


@dataclass(slots=True)
class FirecrawlWebSearchBackend(WebSearchBackend):
    config: FirecrawlConfig
    transport: WebBackendHttpTransport = UrllibWebBackendHttpTransport()

    def search(self, query: str) -> list[WebSearchResult]:
        payload: JsonObject = {
            "query": query,
            "limit": 5,
        }
        try:
            response = self.transport.post_json(
                self.config.search_url(),
                payload,
                self.config.headers(),
                self.config.timeout_seconds,
            )
        except WebBackendTransportError as exc:
            raise WebSearchBackendError(_summarize_firecrawl_transport_error(str(exc))) from exc

        return _extract_firecrawl_search_results(response.body.get("data"))


def _extract_firecrawl_document(source_url: str, data: object) -> WebDocument | None:
    if not isinstance(data, dict):
        return None
    markdown = data.get("markdown")
    if not isinstance(markdown, str) or not markdown:
        return None
    metadata_source = data.get("metadata")
    title = (
        metadata_source.get("title")
        if isinstance(metadata_source, dict)
        else None
    )
    metadata: JsonObject = {
        "provider": "firecrawl",
        "source_url": source_url,
    }
    if isinstance(metadata_source, dict):
        for key, value in metadata_source.items():
            if isinstance(key, str) and value is not None:
                metadata[key] = (
                    value if isinstance(value, (str, int, float, bool)) else str(value)
                )
    return WebDocument(
        url=source_url,
        content=markdown,
        content_format="markdown",
        title=str(title) if title is not None else None,
        metadata=metadata,
    )


def _extract_firecrawl_search_results(data: object) -> list[WebSearchResult]:
    entries: list[object]
    if isinstance(data, list):
        entries = data
    elif isinstance(data, dict):
        web_entries = data.get("web")
        entries = web_entries if isinstance(web_entries, list) else []
    else:
        entries = []

    results: list[WebSearchResult] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        url = item.get("url")
        if not isinstance(title, str) or not isinstance(url, str):
            continue
        snippet = item.get("description")
        metadata: JsonObject = {
            "provider": "firecrawl",
        }
        markdown = item.get("markdown")
        if isinstance(markdown, str) and markdown:
            metadata["markdown"] = markdown
        results.append(
            WebSearchResult(
                title=title,
                url=url,
                snippet=str(snippet) if snippet is not None else "",
                metadata=metadata,
            )
        )
    if not results:
        raise WebSearchBackendError("Firecrawl search response did not include any results")
    return results


def _normalize_fetch_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "github.com":
        return url
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 5 or parts[2] != "blob":
        return url
    owner, repo, _, ref = parts[:4]
    rest = "/".join(parts[4:])
    if not rest:
        return url
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{rest}"


def _summarize_firecrawl_transport_error(message: str) -> str:
    payload = _extract_error_payload(message)
    if not isinstance(payload, dict):
        return message
    code = payload.get("code")
    if code == "SCRAPE_ALL_ENGINES_FAILED":
        return (
            "Firecrawl could not retrieve the page content. "
            "The page may block automated access, require authentication, or be unavailable."
        )
    error = payload.get("error")
    if isinstance(error, str) and error.strip():
        return error.strip()
    return message


def _extract_error_payload(message: str) -> dict[str, object] | None:
    json_start = message.find("{")
    if json_start < 0:
        return None
    try:
        payload = json.loads(message[json_start:])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None
