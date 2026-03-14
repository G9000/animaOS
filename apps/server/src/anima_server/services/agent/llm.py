from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from functools import lru_cache
from typing import Any, Final, Protocol

import httpx

from anima_server.config import settings
from anima_server.services.agent.openai_compatible_client import (
    OpenAICompatibleChatClient,
)

SUPPORTED_PROVIDERS: Final[tuple[str, ...]] = ("ollama", "openrouter", "vllm")
DEFAULT_BASE_URLS: Final[dict[str, str]] = {
    "ollama": "http://127.0.0.1:11434/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "vllm": "http://127.0.0.1:8000/v1",
}


class LLMConfigError(RuntimeError):
    """Raised when the LLM provider is misconfigured."""


class LLMInvocationError(RuntimeError):
    """Raised when a configured provider cannot be reached or returns an error."""


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

    if provider not in SUPPORTED_PROVIDERS:
        raise LLMConfigError(
            f"Unsupported agent_provider: {provider!r}. "
            f"Expected one of: {', '.join(SUPPORTED_PROVIDERS)}"
        )

    if provider == "openrouter" and not settings.agent_api_key:
        raise LLMConfigError(
            "ANIMA_AGENT_API_KEY is required when agent_provider='openrouter'"
        )

    return OpenAICompatibleChatClient(
        provider=provider,
        model=settings.agent_model,
        base_url=resolve_base_url(provider),
        headers=build_provider_headers(provider),
    )


def invalidate_llm_cache() -> None:
    create_llm.cache_clear()


def resolve_base_url(provider: str) -> str:
    configured_base_url = settings.agent_base_url.strip()
    if configured_base_url:
        return configured_base_url
    return DEFAULT_BASE_URLS[provider]


def build_provider_headers(provider: str) -> dict[str, str]:
    headers: dict[str, str] = {}

    api_key = settings.agent_api_key.strip()
    if provider == "openrouter":
        headers["Authorization"] = f"Bearer {api_key}"
        headers["HTTP-Referer"] = "https://anima.local"
        headers["X-Title"] = "ANIMA"
        return headers

    if provider == "vllm" and api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    return headers


def wrap_llm_error(exc: Exception, *, provider: str, base_url: str) -> LLMInvocationError:
    if isinstance(exc, httpx.HTTPStatusError):
        detail = exc.response.text.strip()
        if detail:
            return LLMInvocationError(
                f"{provider} returned {exc.response.status_code} from {base_url!r}: {detail}"
            )
        return LLMInvocationError(
            f"{provider} returned {exc.response.status_code} from {base_url!r}."
        )

    if isinstance(exc, httpx.HTTPError):
        return LLMInvocationError(
            f"Failed to reach {provider} at {base_url!r}: {exc}"
        )

    return LLMInvocationError(str(exc))
