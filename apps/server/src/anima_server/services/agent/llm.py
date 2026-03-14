from __future__ import annotations

from functools import lru_cache
from typing import Final

from langchain_core.language_models import BaseChatModel

from anima_server.config import settings

SUPPORTED_PROVIDERS: Final[tuple[str, ...]] = ("ollama", "openrouter", "vllm")
DEFAULT_BASE_URLS: Final[dict[str, str]] = {
    "ollama": "http://127.0.0.1:11434/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "vllm": "http://127.0.0.1:8000/v1",
}


class LLMConfigError(RuntimeError):
    """Raised when the LLM provider is misconfigured."""


@lru_cache(maxsize=1)
def create_llm() -> BaseChatModel:
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
        f"Wire a LangChain chat client against {resolve_base_url(provider)!r} "
        "before enabling live model calls."
    )


def invalidate_llm_cache() -> None:
    create_llm.cache_clear()

def resolve_base_url(provider: str) -> str:
    configured_base_url = settings.agent_base_url.strip()
    if configured_base_url:
        return configured_base_url
    return DEFAULT_BASE_URLS[provider]
