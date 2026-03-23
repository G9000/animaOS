from __future__ import annotations

import pytest
from anima_server.config import settings
from anima_server.services.agent.embeddings import generate_embedding
from anima_server.services.agent.llm import LLMConfigError, build_provider_headers


@pytest.mark.asyncio
async def test_generate_embedding_skips_openrouter_when_ollama_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # OpenRouter has no embeddings endpoint; it falls back to local Ollama.
    # When Ollama is also unavailable the function should return None.
    original_provider = settings.agent_provider

    async def ollama_unavailable(text: str) -> list[float] | None:
        raise RuntimeError("Ollama not reachable")

    try:
        settings.agent_provider = "openrouter"
        monkeypatch.setattr(
            "anima_server.services.agent.embeddings._embed_ollama",
            ollama_unavailable,
        )

        result = await generate_embedding("hello")
    finally:
        settings.agent_provider = original_provider

    assert result is None


def test_build_provider_headers_rejects_openrouter_without_api_key() -> None:
    original_api_key = settings.agent_api_key

    try:
        settings.agent_api_key = ""
        with pytest.raises(
            LLMConfigError,
            match="ANIMA_AGENT_API_KEY is required",
        ):
            build_provider_headers("openrouter")
    finally:
        settings.agent_api_key = original_api_key
