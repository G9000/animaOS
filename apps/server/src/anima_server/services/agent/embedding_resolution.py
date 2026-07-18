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

from typing import Any

# Default embedding model per provider, consulted only when neither an
# explicit embedding model nor (for a non-fastembed piggyback provider) the
# chat extraction model is configured.
DEFAULT_EMBEDDING_MODELS: dict[str, str] = {
    "ollama": "nomic-embed-text",
    "openrouter": "openai/text-embedding-3-small",
    "openai": "text-embedding-3-small",
    "vllm": "text-embedding-3-small",
    "doubleword": "Qwen/Qwen3-Embedding-8B",
    "fastembed": "BAAI/bge-small-en-v1.5",
}


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
    setting. It is kept as a legacy fallback only for the piggyback case —
    an explicitly-configured non-fastembed provider — where reusing the
    chat model name was the pre-existing, documented behavior. It must NOT
    be consulted when *provider* is the bundled ``fastembed`` ONNX backend:
    fastembed can only load embedding-capable ONNX models, so leaking a chat
    model name (e.g. "qwen2.5:3b") in here would fail ``TextEmbedding``
    construction (or, for dimension resolution, silently miss
    ``KNOWN_EMBEDDING_DIMS`` and fall through to a wrong dimension default).
    """
    if settings is None:
        settings = _default_settings()

    configured = _setting_text(getattr(settings, "agent_embedding_model", ""))
    if configured:
        return configured
    if provider != "fastembed":
        configured = _setting_text(getattr(settings, "agent_extraction_model", ""))
        if configured:
            return configured
    return DEFAULT_EMBEDDING_MODELS.get(provider, "nomic-embed-text")


__all__ = [
    "DEFAULT_EMBEDDING_MODELS",
    "has_embedding_piggyback_intent",
    "resolve_embedding_model",
    "resolve_embedding_provider",
]
