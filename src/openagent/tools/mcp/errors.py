"""MCP-specific error types."""

from __future__ import annotations


class McpError(RuntimeError):
    """Base class for MCP integration failures."""


class McpTransportError(McpError):
    """Raised when the underlying transport fails."""


class McpAuthorizationError(McpError):
    """Raised when authorization discovery or token handling fails."""


class McpProtocolError(McpError):
    """Raised when a server violates protocol or negotiation fails."""


class McpCapabilityError(McpError):
    """Raised when an unavailable capability is used."""


class McpRemoteToolError(McpError):
    """Raised when a remote MCP tool fails."""
