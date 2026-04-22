import os
from pathlib import Path
from typing import cast

import pytest

from openagent.object_model import JsonObject
from openagent.tools import WebFetchTool, WebSearchTool, create_builtin_toolset
from openagent.tools.web import (
    BraveConfig,
    BraveWebSearchBackend,
    FirecrawlConfig,
    FirecrawlWebFetchBackend,
    FirecrawlWebSearchBackend,
    TavilyConfig,
    TavilyWebSearchBackend,
    WebBackendHttpResponse,
    WebBackendTransportError,
)


class FakeTransport:
    def __init__(self, body: dict[str, object]) -> None:
        self.body = cast(JsonObject, body)
        self.seen_url: str | None = None
        self.seen_payload: JsonObject | None = None
        self.seen_headers: dict[str, str] | None = None

    def post_json(
        self,
        url: str,
        payload: JsonObject,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> WebBackendHttpResponse:
        del timeout_seconds
        self.seen_url = url
        self.seen_payload = payload
        self.seen_headers = headers
        return WebBackendHttpResponse(status_code=200, body=self.body)

    def get_json(
        self,
        url: str,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> WebBackendHttpResponse:
        del timeout_seconds
        self.seen_url = url
        self.seen_payload = None
        self.seen_headers = headers
        return WebBackendHttpResponse(status_code=200, body=self.body)


class FailingTransport:
    def __init__(self, message: str) -> None:
        self.message = message

    def post_json(
        self,
        url: str,
        payload: JsonObject,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> WebBackendHttpResponse:
        del url, payload, headers, timeout_seconds
        raise WebBackendTransportError(self.message)

    def get_json(
        self,
        url: str,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> WebBackendHttpResponse:
        del url, headers, timeout_seconds
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


def test_tavily_websearch_backend_maps_results() -> None:
    transport = FakeTransport(
        {
            "results": [
                {
                    "title": "Tavily Result",
                    "url": "https://example.com/tavily",
                    "content": "Tavily summary",
                    "score": 0.92,
                    "published_date": "2026-04-20",
                    "raw_content": "Full page text",
                },
                {"title": "Missing URL"},
            ]
        }
    )
    backend = TavilyWebSearchBackend(
        TavilyConfig(api_key="tavily-token", base_url="https://api.tavily.test", limit=3),
        transport=transport,
    )

    results = backend.search("openagent search")

    assert transport.seen_url == "https://api.tavily.test/search"
    assert transport.seen_payload == {"query": "openagent search", "max_results": 3}
    assert transport.seen_headers == {"Authorization": "Bearer tavily-token"}
    assert len(results) == 1
    assert results[0].title == "Tavily Result"
    assert results[0].url == "https://example.com/tavily"
    assert results[0].snippet == "Tavily summary"
    assert results[0].metadata == {
        "provider": "tavily",
        "score": 0.92,
        "published_date": "2026-04-20",
        "raw_content": "Full page text",
    }


def test_tavily_websearch_backend_wraps_transport_error() -> None:
    backend = TavilyWebSearchBackend(
        TavilyConfig(api_key="secret-token"),
        transport=FailingTransport("HTTP 401: unauthorized"),
    )

    with pytest.raises(RuntimeError) as exc:
        backend.search("openagent")

    assert "HTTP 401: unauthorized" in str(exc.value)
    assert "secret-token" not in str(exc.value)


def test_brave_websearch_backend_maps_results_and_headers() -> None:
    transport = FakeTransport(
        {
            "web": {
                "results": [
                    {
                        "title": "Brave Result",
                        "url": "https://example.com/brave",
                        "description": "Brave summary",
                        "age": "2 days ago",
                        "language": "en",
                        "family_friendly": True,
                    },
                    {"title": "Missing URL"},
                ]
            }
        }
    )
    backend = BraveWebSearchBackend(
        BraveConfig(api_key="brave-token", base_url="https://api.search.brave.test", limit=7),
        transport=transport,
    )

    results = backend.search("openagent search")

    assert (
        transport.seen_url
        == "https://api.search.brave.test/res/v1/web/search?q=openagent+search&count=7"
    )
    assert transport.seen_headers == {
        "Accept": "application/json",
        "X-Subscription-Token": "brave-token",
    }
    assert len(results) == 1
    assert results[0].title == "Brave Result"
    assert results[0].url == "https://example.com/brave"
    assert results[0].snippet == "Brave summary"
    assert results[0].metadata == {
        "provider": "brave",
        "age": "2 days ago",
        "language": "en",
        "family_friendly": True,
    }


def test_brave_websearch_backend_returns_empty_results_when_web_results_missing() -> None:
    backend = BraveWebSearchBackend(
        BraveConfig(api_key="brave-token"),
        transport=FakeTransport({"query": {"original": "openagent"}}),
    )

    assert backend.search("openagent") == []


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


def test_create_builtin_toolset_uses_tavily_websearch_backend_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAGENT_WEBSEARCH_BACKEND", "tavily")
    monkeypatch.setenv("OPENAGENT_TAVILY_API_KEY", "tavily-token")
    monkeypatch.setenv("OPENAGENT_TAVILY_BASE_URL", "https://api.tavily.test")
    monkeypatch.setenv("OPENAGENT_WEBSEARCH_LIMIT", "10")

    toolset = {tool.name: tool for tool in create_builtin_toolset()}

    backend = cast(WebSearchTool, toolset["WebSearch"]).backend
    assert isinstance(backend, TavilyWebSearchBackend)
    assert backend.config.api_key == "tavily-token"
    assert backend.config.base_url == "https://api.tavily.test"
    assert backend.config.limit == 10


def test_create_builtin_toolset_uses_brave_websearch_backend_from_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAGENT_WEBSEARCH_BACKEND", raising=False)
    monkeypatch.delenv("OPENAGENT_BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAGENT_BRAVE_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAGENT_WEBSEARCH_LIMIT", raising=False)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "OPENAGENT_WEBSEARCH_BACKEND=brave",
                "OPENAGENT_BRAVE_API_KEY=brave-token",
                "OPENAGENT_BRAVE_BASE_URL=https://api.search.brave.test",
                "OPENAGENT_WEBSEARCH_LIMIT=8",
            ]
        ),
        encoding="utf-8",
    )

    toolset = {tool.name: tool for tool in create_builtin_toolset()}

    backend = cast(WebSearchTool, toolset["WebSearch"]).backend
    assert isinstance(backend, BraveWebSearchBackend)
    assert backend.config.api_key == "brave-token"
    assert backend.config.base_url == "https://api.search.brave.test"
    assert backend.config.limit == 8


def test_create_builtin_toolset_requires_selected_search_backend_api_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAGENT_WEBSEARCH_BACKEND", "tavily")
    monkeypatch.delenv("OPENAGENT_TAVILY_API_KEY", raising=False)

    with pytest.raises(RuntimeError) as exc:
        create_builtin_toolset()

    assert "OPENAGENT_TAVILY_API_KEY" in str(exc.value)


def test_create_builtin_toolset_rejects_invalid_websearch_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAGENT_WEBSEARCH_BACKEND", "brave")
    monkeypatch.setenv("OPENAGENT_BRAVE_API_KEY", "brave-token")
    monkeypatch.setenv("OPENAGENT_WEBSEARCH_LIMIT", "0")

    with pytest.raises(RuntimeError) as exc:
        create_builtin_toolset()

    assert "OPENAGENT_WEBSEARCH_LIMIT" in str(exc.value)


def test_create_builtin_toolset_requires_firecrawl_base_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
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
    fetch_tool = WebFetchTool(FirecrawlWebFetchBackend(FirecrawlConfig(base_url=base_url)))
    search_tool = WebSearchTool(FirecrawlWebSearchBackend(FirecrawlConfig(base_url=base_url)))

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


@pytest.mark.skipif(
    not os.getenv("OPENAGENT_RUN_TAVILY_SMOKE") or not os.getenv("OPENAGENT_TAVILY_API_KEY"),
    reason=(
        "set OPENAGENT_RUN_TAVILY_SMOKE=1 and OPENAGENT_TAVILY_API_KEY "
        "to run live Tavily smoke tests"
    ),
)
def test_tavily_smoke_search() -> None:
    search_tool = WebSearchTool(
        TavilyWebSearchBackend(TavilyConfig(api_key=os.environ["OPENAGENT_TAVILY_API_KEY"]))
    )

    search_result = search_tool.call({"query": "OpenAgent agent-spec tools"})

    assert search_result.success is True
    assert search_result.structured_content is not None
    assert "results" in search_result.structured_content


@pytest.mark.skipif(
    not os.getenv("OPENAGENT_RUN_BRAVE_SMOKE") or not os.getenv("OPENAGENT_BRAVE_API_KEY"),
    reason=(
        "set OPENAGENT_RUN_BRAVE_SMOKE=1 and OPENAGENT_BRAVE_API_KEY to run live Brave smoke tests"
    ),
)
def test_brave_smoke_search() -> None:
    search_tool = WebSearchTool(
        BraveWebSearchBackend(BraveConfig(api_key=os.environ["OPENAGENT_BRAVE_API_KEY"]))
    )

    search_result = search_tool.call({"query": "OpenAgent agent-spec tools"})

    assert search_result.success is True
    assert search_result.structured_content is not None
    assert "results" in search_result.structured_content
