"""HTTP primitives for web tool backends."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol
from urllib import request
from urllib.error import HTTPError, URLError

from openagent.object_model import JsonObject


@dataclass(slots=True)
class WebBackendHttpResponse:
    status_code: int
    body: JsonObject
    headers: dict[str, str] = field(default_factory=dict)


class WebBackendHttpTransport(Protocol):
    def post_json(
        self,
        url: str,
        payload: JsonObject,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> WebBackendHttpResponse:
        """Send a JSON POST request and return the decoded JSON response."""


class WebBackendTransportError(RuntimeError):
    """Raised when a web backend transport fails."""


class UrllibWebBackendHttpTransport:
    """Minimal stdlib transport for web backends."""

    def post_json(
        self,
        url: str,
        payload: JsonObject,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> WebBackendHttpResponse:
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
                    raise WebBackendTransportError("Web backend response must be a JSON object")
                response_headers = {
                    key.lower(): value for key, value in response.headers.items()
                }
                return WebBackendHttpResponse(
                    status_code=response.status,
                    body=body,
                    headers=response_headers,
                )
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise WebBackendTransportError(f"HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise WebBackendTransportError(f"Network error: {exc.reason}") from exc
