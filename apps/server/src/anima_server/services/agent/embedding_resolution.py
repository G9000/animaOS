"""Shared embedding-provider/model resolution — single source of truth.

Previously this resolution logic (which provider generates embeddings, and
which model that provider uses) was duplicated between
``services.agent.embeddings`` (actual embedding generation) and ``config``
(embedding-dimension resolution), each carrying a "KEEP IN SYNC" docstring
pointing at the other copy. That is a real drift risk: either copy could
change without the other noticing. This leaf module is now the only copy;
both ``config.py`` and ``services.agent.embeddings`` import from here.

Leaf module contract: nothing here imports ``anima_server.config`` at module
scope, and ``config.py`` imports this module lazily (inside
``resolve_embedding_dim``). One of the two edges must stay lazy or the
import graph cycles (``config`` -> ``embedding_resolution`` -> ``config``);
keeping BOTH lazy also means this module stays importable from either
direction regardless of which side loads first.

Each function takes an optional ``settings`` object instead of importing the
global one directly. When omitted, it is fetched lazily (inside the function
body, to break the cycle above) from ``anima_server.config.settings``. Both
callers rely on this: ``config.py`` calls with no argument, so it picks up
whatever ``anima_server.config.settings`` currently is (including a
test-monkeypatched replacement); ``services.agent.embeddings`` passes its own
module-level ``settings`` binding explicitly, so tests that monkeypatch
``embeddings.settings`` (rather than ``anima_server.config.settings``)
continue to be honored exactly as before the move.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# Default embedding model per provider, consulted only when neither an
# explicit embedding model nor (for the genuine piggyback case — no explicit
# agent_embedding_provider named) the chat extraction model is configured.
# Also doubles as the "does this provider have a known default embedding
# model" membership check for VALID_EMBEDDING_PROVIDERS (see config.py's
# routes module) — a provider absent here (e.g. moonshot) must not be
# accepted as an embedding provider even if it has a usable HTTP endpoint,
# since resolve_embedding_model would have no sane default to fall back to.
DEFAULT_EMBEDDING_MODELS: dict[str, str] = {
    "ollama": "nomic-embed-text",
    "openai": "text-embedding-3-small",
    "vllm": "text-embedding-3-small",
    "doubleword": "Qwen/Qwen3-Embedding-8B",
    "fastembed": "BAAI/bge-small-en-v1.5",
}

DEFAULT_EMBEDDING_BASE_URLS: dict[str, str] = {
    "ollama": "http://127.0.0.1:11434",
    "openrouter": "https://openrouter.ai/api/v1",
    "moonshot": "https://api.moonshot.cn/v1",
    "vllm": "http://127.0.0.1:8000/v1",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "doubleword": "https://api.doubleword.ai/v1",
}
# openrouter intentionally has no entry here: it always has a real
# `_embedding_skip_reason` (no supported embeddings endpoint), so it is
# already excluded from every embedding surface via that gate and can never
# reach this table at runtime. Keeping a dead entry around would misleadingly
# suggest openrouter passes the has-default-model membership check (see
# VALID_EMBEDDING_PROVIDERS in config.py and embeddings.embedding_provider_
# unusable_reason) when it never gets there in the first place.


def _setting_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _default_settings() -> Any:
    from anima_server.config import settings

    return settings


def has_embedding_piggyback_intent(settings: Any = None) -> bool:
    """True when the user configured embedding-specific details (model, base
    URL, or API key) against their chat provider without naming an embedding
    provider explicitly.

    That is a real signal of intent, not an accident of the old fallback
    onto ``agent_provider`` — ``resolve_embedding_provider`` honors it by
    falling back to the chat provider instead of the bundled default.
    """
    if settings is None:
        settings = _default_settings()

    embedding_model = _setting_text(getattr(settings, "agent_embedding_model", ""))
    embedding_base_url = _setting_text(getattr(settings, "agent_embedding_base_url", ""))
    embedding_api_key = _setting_text(getattr(settings, "agent_embedding_api_key", ""))
    return bool(embedding_model or embedding_base_url or embedding_api_key)


def resolve_embedding_provider(settings: Any = None) -> str:
    """Resolve which provider generates embeddings.

    Order: explicit ``agent_embedding_provider`` wins. Otherwise the bundled
    ``fastembed`` ONNX provider is the default — dense retrieval must work
    regardless of which chat LLM the user has configured. The old implicit
    piggyback onto ``agent_provider`` is preserved only when
    ``has_embedding_piggyback_intent()`` is true.
    """
    if settings is None:
        settings = _default_settings()

    configured = _setting_text(getattr(settings, "agent_embedding_provider", ""))
    if configured:
        return configured
    if has_embedding_piggyback_intent(settings):
        return _setting_text(getattr(settings, "agent_provider", "")) or "ollama"
    return "fastembed"


def resolve_embedding_model(provider: str, settings: Any = None) -> str:
    """Return the embedding model to use for the given, already-resolved provider.

    ``agent_extraction_model`` is a CHAT model setting, not an embedding
    setting. It is kept as a legacy fallback ONLY for the genuine piggyback
    case: no ``agent_embedding_provider`` was named explicitly, so the
    embedding side is riding whatever the chat provider happens to be (see
    ``has_embedding_piggyback_intent``) — reusing the chat model name there
    was the pre-existing, documented behavior.

    It must NOT be consulted whenever ``agent_embedding_provider`` IS
    explicitly set, regardless of which provider that is. An explicit
    embedding provider names a real, distinct embeddings endpoint (e.g.
    "openai") — falling back to a CHAT model configured for a completely
    different chat provider (e.g. an ``agent_extraction_model`` of
    "qwen2.5:3b" left over from an ollama chat setup) would hijack the
    embedding request with a model that provider's ``/v1/embeddings``
    endpoint has never heard of, failing with a 400 instead of using the
    provider's own sane default. This generalizes the older fastembed-only
    skip (commit 5c62215): fastembed is just one case of "explicit,
    non-piggyback provider" among several.
    """
    if settings is None:
        settings = _default_settings()

    configured = _setting_text(getattr(settings, "agent_embedding_model", ""))
    if configured:
        return configured

    explicit_provider = _setting_text(getattr(settings, "agent_embedding_provider", ""))
    if not explicit_provider and has_embedding_piggyback_intent(settings):
        # Genuine piggyback only: no explicit embedding provider was named,
        # so the resolved provider mirrors the chat provider and reusing the
        # chat model name is the documented legacy behavior.
        configured = _setting_text(getattr(settings, "agent_extraction_model", ""))
        if configured:
            return configured

    # Defense-in-depth catch-all: DEFAULT_EMBEDDING_MODELS.get(provider, "")
    # rather than a hardcoded model name (e.g. the old "nomic-embed-text"
    # Ollama default) for an unrecognized provider — the API layer
    # (VALID_EMBEDDING_PROVIDERS) already rejects any provider without a
    # known default embedding model, so this branch should be unreachable
    # in normal operation. Returning "" for such a provider fails loudly
    # (an empty model name in the request) instead of silently sending some
    # OTHER provider's model name to an endpoint that has never heard of it.
    return DEFAULT_EMBEDDING_MODELS.get(provider, "")


def resolve_embedding_base_url(settings: Any = None) -> str:
    """Resolve the endpoint identity for the active embedding provider."""
    if settings is None:
        settings = _default_settings()

    provider = resolve_embedding_provider(settings)
    if provider == "fastembed":
        return ""

    configured = _setting_text(getattr(settings, "agent_embedding_base_url", ""))
    if configured:
        return configured.removesuffix("/v1") if provider == "ollama" else configured

    configured_agent = _setting_text(getattr(settings, "agent_base_url", ""))
    chat_provider = _setting_text(getattr(settings, "agent_provider", ""))
    if configured_agent and provider == chat_provider:
        if provider == "openrouter":
            return DEFAULT_EMBEDDING_BASE_URLS[provider]
        return (
            configured_agent.removesuffix("/v1")
            if provider == "ollama"
            else configured_agent
        )

    return DEFAULT_EMBEDDING_BASE_URLS[provider]


def configured_embedding_fingerprint(settings: Any = None) -> str:
    """Identify the effective embedding space without provider secrets."""
    if settings is None:
        settings = _default_settings()

    provider = resolve_embedding_provider(settings)
    payload = json.dumps(
        {
            "endpoint": resolve_embedding_base_url(settings),
            "provider": provider,
            "model": resolve_embedding_model(provider, settings),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "DEFAULT_EMBEDDING_BASE_URLS",
    "DEFAULT_EMBEDDING_MODELS",
    "configured_embedding_fingerprint",
    "has_embedding_piggyback_intent",
    "resolve_embedding_base_url",
    "resolve_embedding_model",
    "resolve_embedding_provider",
]
