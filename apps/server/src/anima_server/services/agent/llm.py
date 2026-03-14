from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from typing import Any, Final, Protocol

from anima_server.config import settings

SUPPORTED_PROVIDERS: Final[tuple[str, ...]] = ("ollama", "openrouter", "vllm")
DEFAULT_BASE_URLS: Final[dict[str, str]] = {
    "ollama": "http://127.0.0.1:11434/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "vllm": "http://127.0.0.1:8000/v1",
}


class LLMConfigError(RuntimeError):
    """Raised when the LLM provider is misconfigured."""


class ChatClient(Protocol):
    async def ainvoke(self, input: Sequence[Any]) -> Any:
        """Invoke the chat model with a normalized message list."""

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
    """Validate the configured provider until a concrete client is wired in."""
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

    raise LLMConfigError(
        f"agent_provider={provider!r} is scaffolded only. "
        f"Wire a chat client against {resolve_base_url(provider)!r} "
        "before enabling live model calls."
    )


def invalidate_llm_cache() -> None:
    create_llm.cache_clear()


def resolve_base_url(provider: str) -> str:
    configured_base_url = settings.agent_base_url.strip()
    if configured_base_url:
        return configured_base_url
    return DEFAULT_BASE_URLS[provider]
