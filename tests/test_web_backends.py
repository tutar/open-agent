import os

import pytest

from openagent.tools import WebFetchTool, WebSearchTool, create_builtin_toolset
from openagent.tools.web import (
    FirecrawlConfig,
    FirecrawlWebFetchBackend,
    FirecrawlWebSearchBackend,
    WebBackendHttpResponse,
    WebBackendTransportError,
)


class FakeTransport:
    def __init__(self, body: dict[str, object]) -> None:
        self.body = body
        self.seen_url: str | None = None
        self.seen_payload: dict[str, object] | None = None
        self.seen_headers: dict[str, str] | None = None

    def post_json(
        self,
        url: str,
        payload: dict[str, object],
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> WebBackendHttpResponse:
        del timeout_seconds
        self.seen_url = url
        self.seen_payload = payload
        self.seen_headers = headers
        return WebBackendHttpResponse(status_code=200, body=self.body)


class FailingTransport:
    def __init__(self, message: str) -> None:
        self.message = message

    def post_json(
        self,
        url: str,
        payload: dict[str, object],
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> WebBackendHttpResponse:
        del url, payload, headers, timeout_seconds
        raise WebBackendTransportError(self.message)


def test_firecrawl_webfetch_backend_maps_scrape_markdown() -> None:
    transport = FakeTransport(
        {
            "success": True,
            "data": {
                "markdown": "# Firecrawl\n\nSelf-hosting notes",
                "metadata": {"title": "Self-hosting Firecrawl"},
            },
        }
    )
    backend = FirecrawlWebFetchBackend(
        FirecrawlConfig(base_url="http://127.0.0.1:3002", api_key="token"),
        transport=transport,
    )

    document = backend.fetch("https://github.com/firecrawl/firecrawl/blob/main/SELF_HOST.md")

    assert transport.seen_url == "http://127.0.0.1:3002/v2/scrape"
    assert transport.seen_payload == {
        "url": "https://raw.githubusercontent.com/firecrawl/firecrawl/main/SELF_HOST.md",
        "formats": ["markdown"],
        "onlyMainContent": True,
    }
    assert transport.seen_headers == {"Authorization": "Bearer token"}
    assert document.content_format == "markdown"
    assert document.title == "Self-hosting Firecrawl"
    assert "Firecrawl" in document.content


def test_firecrawl_webfetch_backend_normalizes_github_blob_urls() -> None:
    transport = FakeTransport({"success": True, "data": {"markdown": "# ok", "metadata": {}}})
    backend = FirecrawlWebFetchBackend(
        FirecrawlConfig(base_url="http://127.0.0.1:3002"),
        transport=transport,
    )

    backend.fetch("https://github.com/openai/open-agent/blob/main/README.md")

    assert transport.seen_payload is not None
    assert (
        transport.seen_payload["url"]
        == "https://raw.githubusercontent.com/openai/open-agent/main/README.md"
    )


def test_firecrawl_websearch_backend_maps_result_list() -> None:
    transport = FakeTransport(
        {
            "success": True,
            "data": [
                {
                    "title": "Firecrawl Search",
                    "description": "Search docs",
                    "url": "https://docs.firecrawl.dev/features/search",
                    "markdown": "# Search\n\nDocs",
                }
            ],
        }
    )
    backend = FirecrawlWebSearchBackend(
        FirecrawlConfig(base_url="http://127.0.0.1:3002"),
        transport=transport,
    )

    results = backend.search("firecrawl search")

    assert transport.seen_url == "http://127.0.0.1:3002/v2/search"
    assert transport.seen_payload == {"query": "firecrawl search", "limit": 5}
    assert len(results) == 1
    assert results[0].title == "Firecrawl Search"
    assert results[0].url == "https://docs.firecrawl.dev/features/search"
    assert results[0].metadata["provider"] == "firecrawl"
    assert "markdown" in results[0].metadata


def test_firecrawl_webfetch_backend_summarizes_scrape_failure() -> None:
    backend = FirecrawlWebFetchBackend(
        FirecrawlConfig(base_url="http://127.0.0.1:3002"),
        transport=FailingTransport(
            "HTTP 500: "
            '{"success":false,"code":"SCRAPE_ALL_ENGINES_FAILED",'
            '"error":"All scraping engines failed"}'
        ),
    )

    with pytest.raises(RuntimeError) as exc:
        backend.fetch("https://example.com")

    assert str(exc.value) == (
        "Firecrawl could not retrieve the page content. "
        "The page may block automated access, require authentication, or be unavailable."
    )


def test_create_builtin_toolset_uses_firecrawl_backends_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAGENT_WEBFETCH_BACKEND", "firecrawl")
    monkeypatch.setenv("OPENAGENT_WEBSEARCH_BACKEND", "firecrawl")
    monkeypatch.setenv("OPENAGENT_FIRECRAWL_BASE_URL", "http://127.0.0.1:3002")

    toolset = {tool.name: tool for tool in create_builtin_toolset()}

    assert isinstance(toolset["WebFetch"], WebFetchTool)
    assert isinstance(toolset["WebSearch"], WebSearchTool)
    assert type(toolset["WebFetch"].backend).__name__ == "FirecrawlWebFetchBackend"
    assert type(toolset["WebSearch"].backend).__name__ == "FirecrawlWebSearchBackend"


def test_create_builtin_toolset_requires_firecrawl_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAGENT_WEBFETCH_BACKEND", "firecrawl")
    monkeypatch.delenv("OPENAGENT_FIRECRAWL_BASE_URL", raising=False)

    with pytest.raises(RuntimeError) as exc:
        create_builtin_toolset()

    assert "OPENAGENT_FIRECRAWL_BASE_URL" in str(exc.value)


@pytest.mark.skipif(
    not os.getenv("OPENAGENT_RUN_FIRECRAWL_SMOKE"),
    reason="set OPENAGENT_RUN_FIRECRAWL_SMOKE=1 to run live Firecrawl smoke tests",
)
def test_firecrawl_smoke_fetch_and_search() -> None:
    base_url = os.getenv("OPENAGENT_FIRECRAWL_BASE_URL", "http://127.0.0.1:3002")
    fetch_tool = WebFetchTool(
        FirecrawlWebFetchBackend(FirecrawlConfig(base_url=base_url))
    )
    search_tool = WebSearchTool(
        FirecrawlWebSearchBackend(FirecrawlConfig(base_url=base_url))
    )

    fetch_result = fetch_tool.call(
        {"url": "https://github.com/firecrawl/firecrawl/blob/main/SELF_HOST.md"}
    )
    search_result = search_tool.call({"query": "site:docs.firecrawl.dev firecrawl search"})

    assert fetch_result.success is True
    assert fetch_result.content
    assert isinstance(fetch_result.content[0], str)
    assert search_result.success is True
    assert search_result.structured_content is not None
    assert "results" in search_result.structured_content
