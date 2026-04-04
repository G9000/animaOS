from __future__ import annotations

from types import SimpleNamespace

import httpx
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


@pytest.mark.asyncio
async def test_embed_ollama_prefers_native_embed_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import embeddings as embeddings_module

    calls: list[tuple[str, dict[str, object]]] = []

    class FakeResponse:
        def __init__(self, url: str, *, payload: dict[str, object]) -> None:
            self.status_code = 200
            self._payload = payload
            self.text = ""
            self.request = httpx.Request("POST", url)

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self._payload

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, json: dict[str, object]) -> FakeResponse:
            calls.append((url, json))
            if url.endswith("/api/embed"):
                return FakeResponse(url, payload={"embeddings": [[0.4, 0.5, 0.6]]})
            raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        embeddings_module,
        "settings",
        SimpleNamespace(
            agent_provider="ollama",
            agent_base_url="http://127.0.0.1:11434",
            agent_api_key="",
            agent_embedding_provider="",
            agent_embedding_base_url="",
            agent_embedding_model="",
            agent_embedding_api_key="",
            agent_extraction_model="",
        ),
    )

    result = await embeddings_module._embed_ollama("hello")

    assert result == [0.4, 0.5, 0.6]
    assert calls == [
        (
            "http://127.0.0.1:11434/api/embed",
            {"model": "nomic-embed-text", "input": "hello"},
        )
    ]


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


def test_resolve_embedding_dim_normalizes_tagged_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server import config as config_module

    monkeypatch.setattr(
        config_module,
        "settings",
        SimpleNamespace(
            agent_embedding_model="all-minilm:latest",
            agent_extraction_model="",
            agent_embedding_provider="ollama",
            agent_provider="openrouter",
            agent_embedding_dim=768,
        ),
    )
    config_module.clear_detected_embedding_dim()

    assert config_module.resolve_embedding_dim() == 384


@pytest.mark.asyncio
async def test_embed_ollama_uses_explicit_embedding_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import embeddings as embeddings_module

    calls: list[tuple[str, dict[str, object]]] = []

    class FakeResponse:
        def __init__(self, url: str, *, payload: dict[str, object]) -> None:
            self.status_code = 200
            self._payload = payload
            self.text = ""
            self.request = httpx.Request("POST", url)

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self._payload

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, json: dict[str, object]) -> FakeResponse:
            calls.append((url, json))
            return FakeResponse(url, payload={"embeddings": [[0.4, 0.5, 0.6]]})

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        embeddings_module,
        "settings",
        SimpleNamespace(
            agent_provider="openrouter",
            agent_base_url="",
            agent_api_key="",
            agent_embedding_provider="ollama",
            agent_embedding_base_url="http://127.0.0.1:11434",
            agent_embedding_model="all-minilm:latest",
            agent_embedding_api_key="",
            agent_extraction_model="",
        ),
    )

    result = await embeddings_module._embed_ollama("hello")

    assert result == [0.4, 0.5, 0.6]
    assert calls == [
        (
            "http://127.0.0.1:11434/api/embed",
            {"model": "all-minilm:latest", "input": "hello"},
        )
    ]
