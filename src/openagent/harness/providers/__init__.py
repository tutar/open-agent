"""Real provider adapters for harness model integration."""

from __future__ import annotations

import json
import os
from urllib import request
from urllib.error import HTTPError, URLError

from openagent.harness.providers.errors import (
    ProviderConfigurationError,
    ProviderError,
)
from openagent.harness.providers.instructor_adapter import (
    InstructorModelAdapter,
)
from openagent.harness.runtime.io import ModelProviderAdapter


def load_model_from_env() -> ModelProviderAdapter:
    """Create a real model adapter from the local environment."""

    model = os.getenv("OPENAGENT_MODEL")
    base_url = os.getenv("OPENAGENT_BASE_URL")
    provider = _resolve_provider_from_env(base_url)
    if not model:
        raise ProviderConfigurationError("OPENAGENT_MODEL is required")
    if not base_url:
        raise ProviderConfigurationError("OPENAGENT_BASE_URL is required")
    if provider == "openai":
        model = _resolve_openai_model_name(base_url, model)
    return InstructorModelAdapter(
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=_provider_api_key(provider),
        max_tokens=1024 if provider == "anthropic" else None,
    )


def _resolve_provider_from_env(base_url: str | None) -> str:
    configured = os.getenv("OPENAGENT_PROVIDER", "").strip().lower()
    if configured in {"openai", "anthropic"}:
        return configured
    normalized_base_url = (base_url or "").strip().lower()
    if "anthropic" in normalized_base_url or normalized_base_url.rstrip("/").endswith("/v1/messages"):
        return "anthropic"
    if os.getenv("ANTHROPIC_API_KEY") and not (
        os.getenv("OPENAI_API_KEY") or os.getenv("OPENAGENT_API_KEY")
    ):
        return "anthropic"
    return "openai"


def _provider_api_key(provider: str) -> str | None:
    if provider == "anthropic":
        return os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAGENT_API_KEY")
    return os.getenv("OPENAI_API_KEY") or os.getenv("OPENAGENT_API_KEY")


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
    "InstructorModelAdapter",
    "ProviderConfigurationError",
    "ProviderError",
    "load_model_from_env",
]
