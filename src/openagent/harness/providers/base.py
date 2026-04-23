"""Provider-agnostic HTTP primitives for harness model adapters."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol
from urllib import request
from urllib.error import HTTPError, URLError

from openagent.object_model import JsonObject


@dataclass(slots=True)
class HttpResponse:
    status_code: int
    body: JsonObject
    headers: dict[str, str] = field(default_factory=dict)


class HttpTransport(Protocol):
    def post_json(
        self,
        url: str,
        payload: JsonObject,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> HttpResponse:
        """Send a JSON POST request and return the decoded JSON response."""

    def post_json_stream(
        self,
        url: str,
        payload: JsonObject,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> Iterator[str]:
        """Send a JSON POST request and yield the text stream response line by line."""


class ProviderError(RuntimeError):
    """Raised when a provider request fails or returns invalid data."""


class ProviderConfigurationError(RuntimeError):
    """Raised when provider configuration is incomplete."""


class UrllibHttpTransport:
    """Minimal stdlib transport used by provider adapters."""

    def post_json(
        self,
        url: str,
        payload: JsonObject,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> HttpResponse:
        encoded = json.dumps(payload).encode("utf-8")
        request_headers = {"Content-Type": "application/json", **headers}
        http_request = request.Request(
            url=url,
            data=encoded,
            headers=request_headers,
            method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=timeout_seconds) as response:
                raw_body = response.read().decode("utf-8")
                body = json.loads(raw_body)
                if not isinstance(body, dict):
                    raise ProviderError("Provider response must be a JSON object")
                response_headers = {
                    key.lower(): value for key, value in response.headers.items()
                }
                return HttpResponse(
                    status_code=response.status,
                    body=body,
                    headers=response_headers,
                )
        except HTTPError as exc:
            detail = _format_http_error_detail(exc)
            raise ProviderError(f"HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise ProviderError(f"Network error: {exc.reason}") from exc

    def post_json_stream(
        self,
        url: str,
        payload: JsonObject,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> Iterator[str]:
        encoded = json.dumps(payload).encode("utf-8")
        request_headers = {
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
            "Content-Type": "application/json",
            **headers,
        }
        http_request = request.Request(
            url=url,
            data=encoded,
            headers=request_headers,
            method="POST",
        )

        try:
            response = request.urlopen(http_request, timeout=timeout_seconds)
        except HTTPError as exc:
            detail = _format_http_error_detail(exc)
            raise ProviderError(f"HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise ProviderError(f"Network error: {exc.reason}") from exc

        def _iter_lines() -> Iterator[str]:
            with response:
                for raw_line in response:
                    yield raw_line.decode("utf-8", errors="replace")

        return _iter_lines()


def _format_http_error_detail(exc: HTTPError) -> str:
    detail = exc.read().decode("utf-8", errors="replace").strip()
    if detail:
        return detail
    return "upstream returned an empty error body"
