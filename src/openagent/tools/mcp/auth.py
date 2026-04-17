"""Authorization seams for MCP HTTP transports."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from urllib import request
from urllib.error import HTTPError, URLError

from openagent.object_model import JsonObject
from openagent.tools.mcp.errors import McpAuthorizationError
from openagent.tools.mcp.models import McpAuthMetadata, McpAuthState


def parse_www_authenticate(challenge: str) -> JsonObject:
    parsed: JsonObject = {}
    if not challenge:
        return parsed
    scheme, _, payload = challenge.partition(" ")
    parsed["scheme"] = scheme.lower()
    for chunk in payload.split(","):
        key, _, value = chunk.strip().partition("=")
        if not key:
            continue
        parsed[key.lower()] = value.strip().strip('"')
    return parsed


@dataclass(slots=True)
class McpAuthorizationAdapter:
    timeout_seconds: float = 5.0
    discovery_path: str = "/.well-known/oauth-authorization-server"
    token_override: str | None = None
    scope_token_map: dict[str, str] = field(default_factory=dict)

    def discover_authorization(self, server_endpoint: str) -> McpAuthMetadata:
        discovery_url = server_endpoint.rstrip("/") + self.discovery_path
        http_request = request.Request(discovery_url, method="GET")
        try:
            with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                raw_body = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise McpAuthorizationError(
                f"Authorization discovery failed: HTTP {exc.code}: {detail}"
            ) from exc
        except URLError as exc:
            raise McpAuthorizationError(f"Authorization discovery failed: {exc.reason}") from exc
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise McpAuthorizationError("Authorization discovery returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise McpAuthorizationError("Authorization discovery must return a JSON object")
        scopes = body.get("scopes_supported", [])
        return McpAuthMetadata(
            token_endpoint=str(body.get("token_endpoint")) if body.get("token_endpoint") else None,
            authorization_server=str(body.get("issuer")) if body.get("issuer") else None,
            scopes=[str(scope) for scope in scopes] if isinstance(scopes, list) else [],
            metadata={str(key): value for key, value in body.items() if isinstance(key, str)},
        )

    def acquire_token(
        self,
        auth_metadata: McpAuthMetadata,
        scopes: list[str] | None = None,
    ) -> McpAuthState:
        requested_scopes = scopes or auth_metadata.scopes
        if self.token_override is not None:
            return McpAuthState(access_token=self.token_override, scopes=list(requested_scopes))
        if requested_scopes:
            for scope in reversed(requested_scopes):
                if scope in self.scope_token_map:
                    return McpAuthState(
                        access_token=self.scope_token_map[scope],
                        scopes=list(requested_scopes),
                    )
        token_endpoint = auth_metadata.token_endpoint
        if token_endpoint is None:
            raise McpAuthorizationError("Authorization metadata is missing token_endpoint")
        payload = json.dumps({"scopes": requested_scopes}).encode("utf-8")
        http_request = request.Request(
            token_endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                raw_body = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise McpAuthorizationError(
                f"Token acquisition failed: HTTP {exc.code}: {detail}"
            ) from exc
        except URLError as exc:
            raise McpAuthorizationError(f"Token acquisition failed: {exc.reason}") from exc
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise McpAuthorizationError("Token acquisition returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise McpAuthorizationError("Token acquisition must return a JSON object")
        access_token = body.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise McpAuthorizationError("Token acquisition response is missing access_token")
        returned_scope = body.get("scope")
        effective_scopes = (
            returned_scope.split()
            if isinstance(returned_scope, str) and returned_scope
            else list(requested_scopes)
        )
        return McpAuthState(access_token=access_token, scopes=effective_scopes)

    def refresh_token(self, token_state: McpAuthState) -> McpAuthState:
        if token_state.access_token is None:
            raise McpAuthorizationError("Cannot refresh an empty auth state")
        return token_state

    def handle_www_authenticate(
        self,
        challenge: str,
        auth_state: McpAuthState,
    ) -> McpAuthState:
        parsed = parse_www_authenticate(challenge)
        requested_scope = parsed.get("scope")
        if isinstance(requested_scope, str) and requested_scope:
            merged_scopes = list(dict.fromkeys([*auth_state.scopes, *requested_scope.split()]))
            if merged_scopes != auth_state.scopes:
                upgraded = self.acquire_token(
                    McpAuthMetadata(scopes=merged_scopes),
                    scopes=merged_scopes,
                )
                return upgraded
        return auth_state
