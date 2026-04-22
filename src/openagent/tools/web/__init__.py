"""Pluggable web backends used by builtin web tools."""

from openagent.tools.web.backends import (
    WebDocument,
    WebFetchBackend,
    WebFetchBackendError,
    WebSearchBackend,
    WebSearchBackendError,
    WebSearchResult,
)
from openagent.tools.web.brave import BraveConfig, BraveWebSearchBackend
from openagent.tools.web.default_backends import (
    CallableWebSearchBackend,
    DefaultWebFetchBackend,
    DefaultWebSearchBackend,
)
from openagent.tools.web.firecrawl import (
    FirecrawlConfig,
    FirecrawlWebFetchBackend,
    FirecrawlWebSearchBackend,
)
from openagent.tools.web.tavily import TavilyConfig, TavilyWebSearchBackend
from openagent.tools.web.transport import (
    UrllibWebBackendHttpTransport,
    WebBackendHttpResponse,
    WebBackendHttpTransport,
    WebBackendTransportError,
)

__all__ = [
    "BraveConfig",
    "BraveWebSearchBackend",
    "CallableWebSearchBackend",
    "DefaultWebFetchBackend",
    "DefaultWebSearchBackend",
    "FirecrawlConfig",
    "FirecrawlWebFetchBackend",
    "FirecrawlWebSearchBackend",
    "TavilyConfig",
    "TavilyWebSearchBackend",
    "UrllibWebBackendHttpTransport",
    "WebBackendHttpResponse",
    "WebBackendHttpTransport",
    "WebBackendTransportError",
    "WebDocument",
    "WebFetchBackend",
    "WebFetchBackendError",
    "WebSearchBackend",
    "WebSearchBackendError",
    "WebSearchResult",
]
