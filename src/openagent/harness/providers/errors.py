"""Provider error types shared by harness model adapters."""

from __future__ import annotations


class ProviderError(RuntimeError):
    """Raised when a provider request fails or returns invalid data."""


class ProviderConfigurationError(RuntimeError):
    """Raised when provider configuration is incomplete."""
