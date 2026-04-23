"""Real provider adapters for harness model integration."""

from __future__ import annotations

import json
import os
from urllib import request
from urllib.error import HTTPError, URLError

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
        model = _resolve_openai_model_name(base_url, model)
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


def _resolve_openai_model_name(base_url: str, requested_model: str) -> str:
    """Resolve the configured model name against the endpoint's advertised models."""

    available_models = _fetch_openai_model_ids(base_url)
    if not available_models:
        return requested_model
    if requested_model in available_models:
        return requested_model
    if len(available_models) == 1:
        resolved = available_models[0]
        print(
            "openagent-provider> OPENAGENT_MODEL not advertised by endpoint; "
            f"using only available model requested={requested_model} resolved={resolved}"
        )
        return resolved
    available = ", ".join(available_models)
    raise ProviderConfigurationError(
        "OPENAGENT_MODEL is not available from the configured OpenAI-compatible endpoint: "
        f"requested={requested_model}; available=[{available}]"
    )


def _fetch_openai_model_ids(base_url: str) -> list[str]:
    models_url = f"{base_url.rstrip('/')}/v1/models"
    http_request = request.Request(url=models_url, method="GET")
    try:
        with request.urlopen(http_request, timeout=10.0) as response:
            raw_body = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return []
    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError:
        return []
    if not isinstance(body, dict):
        return []
    raw_models = body.get("data")
    if not isinstance(raw_models, list):
        return []
    model_ids: list[str] = []
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id") or item.get("model") or item.get("name")
        if isinstance(model_id, str) and model_id.strip():
            model_ids.append(model_id.strip())
    return model_ids


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
