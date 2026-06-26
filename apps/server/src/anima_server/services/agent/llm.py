from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Sequence
from functools import lru_cache
from typing import Any, Final, Protocol

import httpx

from anima_server.config import settings
from anima_server.services.agent.anthropic_client import AnthropicChatClient
from anima_server.services.agent.openai_compatible_client import (
    OpenAICompatibleChatClient,
)

SUPPORTED_PROVIDERS: Final[tuple[str, ...]] = (
    "ollama",
    "openrouter",
    "moonshot",
    "vllm",
    "openai",
    "anthropic",
    "doubleword",
)
DEFAULT_BASE_URLS: Final[dict[str, str]] = {
    "ollama": "http://127.0.0.1:11434/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "moonshot": "https://api.moonshot.cn/v1",
    "vllm": "http://127.0.0.1:8000/v1",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "doubleword": "https://api.doubleword.ai/v1",
}
PROVIDER_API_KEY_ENV: Final[dict[str, str]] = {
    "doubleword": "DOUBLEWORD_API_KEY",
}
REQUIRED_API_KEY_PROVIDERS: Final[tuple[str, ...]] = (
    "openrouter",
    "moonshot",
    "openai",
    "anthropic",
    "doubleword",
)


class LLMConfigError(RuntimeError):
    """Raised when the LLM provider is misconfigured."""


class LLMInvocationError(RuntimeError):
    """Raised when a configured provider cannot be reached or returns an error."""


class ContextWindowOverflowError(LLMInvocationError):
    """Raised when the LLM reports that the input exceeds the context window."""


class ChatClient(Protocol):
    async def ainvoke(self, input: Sequence[Any]) -> Any:
        """Invoke the chat model with a normalized message list."""

    async def astream(self, input: Sequence[Any]) -> AsyncGenerator[Any, None]:
        """Stream chat model deltas for a normalized message list."""

    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Return a tool-bound chat client."""


@lru_cache(maxsize=1)
def create_llm() -> ChatClient:
    """Create a concrete chat client for the configured provider."""
    provider = settings.agent_provider

    return create_provider_chat_client(
        provider=provider,
        model=settings.agent_model,
        timeout=settings.agent_llm_timeout,
        max_tokens=settings.agent_max_tokens,
        temperature=settings.agent_temperature,
    )


def invalidate_llm_cache() -> None:
    create_llm.cache_clear()


def create_provider_chat_client(
    *,
    provider: str,
    model: str,
    timeout: float,
    max_tokens: int | None = None,
    temperature: float | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ChatClient:
    """Create an uncached chat client for a concrete provider/model pair."""
    validate_provider_configuration(provider)
    base_url = resolve_base_url(provider)
    headers = build_provider_headers(provider)

    if provider == "anthropic":
        return AnthropicChatClient(
            model=model,
            base_url=base_url,
            headers=headers,
            timeout=timeout,
            max_tokens=max_tokens,
            temperature=temperature,
            transport=transport,
        )

    return OpenAICompatibleChatClient(
        provider=provider,
        model=model,
        base_url=base_url,
        headers=headers,
        timeout=timeout,
        max_tokens=max_tokens,
        temperature=temperature,
        transport=transport,
    )


def resolve_base_url(provider: str) -> str:
    validate_provider(provider)
    configured_base_url = settings.agent_base_url.strip()
    if configured_base_url:
        if provider == "ollama" and not configured_base_url.rstrip("/").endswith("/v1"):
            return configured_base_url.rstrip("/") + "/v1"
        return configured_base_url
    return DEFAULT_BASE_URLS[provider]


def build_provider_headers(provider: str) -> dict[str, str]:
    validate_provider(provider)
    headers: dict[str, str] = {}

    api_key = resolve_provider_api_key(provider)
    if provider == "openrouter":
        api_key = require_provider_api_key(provider)
        headers["Authorization"] = f"Bearer {api_key}"
        headers["HTTP-Referer"] = "https://anima.local"
        headers["X-Title"] = "ANIMA"
        return headers

    if provider == "moonshot":
        api_key = require_provider_api_key(provider)
        # Log key prefix for debugging (never log full key)
        key_preview = api_key[:10] + "..." if len(api_key) > 10 else "[too short]"
        import logging

        logging.getLogger(__name__).info(
            f"Moonshot auth header using key starting with: {key_preview}"
        )
        headers["Authorization"] = f"Bearer {api_key}"
        headers["Content-Type"] = "application/json"
        return headers

    if provider == "openai":
        api_key = require_provider_api_key(provider)
        headers["Authorization"] = f"Bearer {api_key}"
        return headers

    if provider == "doubleword":
        api_key = require_provider_api_key(provider)
        headers["Authorization"] = f"Bearer {api_key}"
        return headers

    if provider == "anthropic":
        api_key = require_provider_api_key(provider)
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
        return headers

    if provider == "vllm" and api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    return headers


def validate_provider(provider: str) -> None:
    if provider not in SUPPORTED_PROVIDERS:
        raise LLMConfigError(
            f"Unsupported agent_provider: {provider!r}. "
            f"Expected one of: {', '.join(SUPPORTED_PROVIDERS)}"
        )


def validate_provider_configuration(provider: str) -> None:
    validate_provider(provider)
    require_provider_api_key(provider)


def resolve_provider_api_key(provider: str) -> str:
    validate_provider(provider)
    from anima_server.config import get_provider_api_key

    env_name = PROVIDER_API_KEY_ENV.get(provider)
    if env_name is not None:
        env_key = os.getenv(env_name, "").strip()
        if env_key:
            return env_key

    per_provider = get_provider_api_key(provider).strip()
    if per_provider:
        return per_provider

    return settings.agent_api_key.strip()


def require_provider_api_key(provider: str) -> str:
    api_key = resolve_provider_api_key(provider)
    if provider in REQUIRED_API_KEY_PROVIDERS and not api_key:
        env_name = PROVIDER_API_KEY_ENV.get(provider)
        key_hint = "ANIMA_AGENT_API_KEY"
        if env_name is not None:
            key_hint = f"{key_hint} or {env_name}"
        raise LLMConfigError(
            f"{key_hint} is required when agent_provider='{provider}'"
        )
    return api_key


_CONTEXT_OVERFLOW_PATTERNS = (
    "context length",
    "context_length",
    "maximum context",
    "token limit",
    "context window",
    "too many tokens",
    "input is too long",
    "prompt is too long",
    "exceeds the model",
    "reduce the length",
    "maximum number of tokens",
)


def _is_context_overflow_message(text: str) -> bool:
    lowered = text.lower()
    return any(pattern in lowered for pattern in _CONTEXT_OVERFLOW_PATTERNS)


def wrap_llm_error(exc: Exception, *, provider: str, base_url: str) -> LLMInvocationError:
    if isinstance(exc, httpx.HTTPStatusError):
        detail = exc.response.text.strip()
        msg = (
            f"{provider} returned {exc.response.status_code} from {base_url!r}: {detail}"
            if detail
            else f"{provider} returned {exc.response.status_code} from {base_url!r}."
        )
        if detail and _is_context_overflow_message(detail):
            return ContextWindowOverflowError(msg)
        return LLMInvocationError(msg)

    if isinstance(exc, httpx.HTTPError):
        return LLMInvocationError(f"Failed to reach {provider} at {base_url!r}: {exc}")

    error_str = str(exc)
    if _is_context_overflow_message(error_str):
        return ContextWindowOverflowError(error_str)

    return LLMInvocationError(error_str)
