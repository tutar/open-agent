"""Real provider adapters for harness model integration."""

from __future__ import annotations

import os

from openagent.harness.providers.anthropic import AnthropicMessagesModelAdapter
from openagent.harness.providers.base import (
    HttpResponse,
    HttpTransport,
    ProviderConfigurationError,
    ProviderError,
    UrllibHttpTransport,
)
from openagent.harness.providers.openai import OpenAIChatCompletionsModelAdapter
from openagent.harness.runtime.io import ModelProviderAdapter


def load_model_from_env() -> ModelProviderAdapter:
    """Create a real model adapter from the local environment."""

    provider = os.getenv("OPENAGENT_PROVIDER", "openai").strip().lower()
    model = os.getenv("OPENAGENT_MODEL")
    base_url = os.getenv("OPENAGENT_BASE_URL")
    if not model:
        raise ProviderConfigurationError("OPENAGENT_MODEL is required")
    if not base_url:
        raise ProviderConfigurationError("OPENAGENT_BASE_URL is required")
    if provider == "openai":
        return OpenAIChatCompletionsModelAdapter(
            model=model,
            base_url=base_url,
            api_key=os.getenv("OPENAI_API_KEY") or os.getenv("OPENAGENT_API_KEY"),
        )
    if provider == "anthropic":
        return AnthropicMessagesModelAdapter(
            model=model,
            base_url=base_url,
            api_key=os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAGENT_API_KEY"),
        )
    raise ProviderConfigurationError(f"Unsupported provider: {provider}")


__all__ = [
    "AnthropicMessagesModelAdapter",
    "HttpResponse",
    "HttpTransport",
    "OpenAIChatCompletionsModelAdapter",
    "ProviderConfigurationError",
    "ProviderError",
    "UrllibHttpTransport",
    "load_model_from_env",
]
