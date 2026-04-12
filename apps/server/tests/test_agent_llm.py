from __future__ import annotations

import logging
from types import SimpleNamespace

import httpx
import pytest
from anima_server.config import settings
from anima_server.services.agent.embeddings import generate_embedding
from anima_server.services.agent.llm import LLMConfigError, build_provider_headers


@pytest.mark.asyncio
async def test_generate_embedding_skips_openrouter_without_embedding_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import embeddings as embeddings_module

    async def unexpected_embed(text: str) -> list[float] | None:
        raise AssertionError(
            "OpenRouter embeddings should be skipped without an explicit embedding provider")

    original_provider = settings.agent_provider
    original_embedding_provider = settings.agent_embedding_provider

    try:
        settings.agent_provider = "openrouter"
        settings.agent_embedding_provider = ""
        monkeypatch.setattr(embeddings_module,
                            "_embed_ollama", unexpected_embed)
        monkeypatch.setattr(embeddings_module,
                            "_embed_openai_compatible", unexpected_embed)

        result = await generate_embedding("hello")
    finally:
        settings.agent_provider = original_provider
        settings.agent_embedding_provider = original_embedding_provider

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


@pytest.mark.asyncio
async def test_generate_embedding_cools_down_unreachable_ollama(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from anima_server.services.agent import embeddings as embeddings_module

    call_count = 0
    request = httpx.Request("POST", "http://127.0.0.1:11434/api/embed")

    async def ollama_unreachable(text: str) -> list[float] | None:
        nonlocal call_count
        call_count += 1
        raise httpx.ConnectError(
            "All connection attempts failed", request=request)

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
    monkeypatch.setattr(embeddings_module, "_embed_ollama", ollama_unreachable)
    embeddings_module.clear_embedding_cache()

    with caplog.at_level(logging.WARNING, logger="anima_server.services.agent.embeddings"):
        first = await embeddings_module.generate_embedding("hello")
        second = await embeddings_module.generate_embedding("world")

    assert first is None
    assert second is None
    assert call_count == 1

    records = [
        record for record in caplog.records
        if record.name == "anima_server.services.agent.embeddings"
    ]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert records[0].exc_info is None
    assert "Cooling down for 30s" in records[0].getMessage()


@pytest.mark.asyncio
async def test_batch_embed_ollama_skips_remainder_when_provider_is_cooling_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import embeddings as embeddings_module

    call_count = 0

    async def mock_generate(text: str) -> list[float] | None:
        nonlocal call_count
        call_count += 1
        return None

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
    monkeypatch.setattr(embeddings_module, "generate_embedding", mock_generate)
    monkeypatch.setattr(embeddings_module,
                        "_provider_in_cooldown", lambda key: True)

    result = await embeddings_module._batch_embed_ollama(["a", "b", "c"])

    assert result == [None, None, None]
    assert call_count == 1


@pytest.mark.asyncio
async def test_generate_embedding_normalizes_cache_key_for_equivalent_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import embeddings as embeddings_module

    call_args: list[str] = []

    async def mock_embed(text: str) -> list[float] | None:
        call_args.append(text)
        return [0.1, 0.2, 0.3]

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
    monkeypatch.setattr(embeddings_module, "_embed_ollama", mock_embed)
    embeddings_module.clear_embedding_cache()

    first = await embeddings_module.generate_embedding("hello\tworld")
    second = await embeddings_module.generate_embedding("hello world")

    assert first == [0.1, 0.2, 0.3]
    assert second == [0.1, 0.2, 0.3]
    assert call_args == ["hello world"]
