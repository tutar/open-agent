"""Backend interfaces for `WebFetch` and `WebSearch`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from openagent.object_model import JsonObject, SerializableModel


class WebFetchBackendError(RuntimeError):
    """Raised when a fetch backend cannot retrieve or normalize a document."""


class WebSearchBackendError(RuntimeError):
    """Raised when a search backend cannot produce a result list."""


@dataclass(slots=True)
class WebDocument(SerializableModel):
    url: str
    content: str
    content_format: str = "text"
    title: str | None = None
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class WebSearchResult(SerializableModel):
    title: str
    url: str
    snippet: str = ""
    metadata: JsonObject = field(default_factory=dict)


@runtime_checkable
class WebFetchBackend(Protocol):
    def fetch(self, url: str) -> WebDocument:
        """Fetch and normalize a concrete URL."""


@runtime_checkable
class WebSearchBackend(Protocol):
    def search(self, query: str) -> list[WebSearchResult]:
        """Search the web and return a normalized result list."""
