from __future__ import annotations

import logging
from types import SimpleNamespace

import httpx
import pytest
from anima_server.config import settings
from anima_server.services.agent.embeddings import generate_embedding
from anima_server.services.agent.llm import (
    ChatTarget,
    LLMConfigError,
    build_provider_headers,
    create_llm,
    create_provider_chat_client,
    invalidate_llm_cache,
    resolve_background_chat_targets,
    resolve_base_url,
    resolve_provider_api_key,
)


def test_resolve_background_chat_targets_orders_and_deduplicates() -> None:
    assert resolve_background_chat_targets(
        extraction_provider="ollama",
        extraction_model="all-minilm:latest",
        primary_provider="openai",
        primary_model="gpt-5-mini",
    ) == [
        ChatTarget(provider="ollama", model="all-minilm:latest"),
        ChatTarget(provider="openai", model="gpt-5-mini"),
    ]

    assert resolve_background_chat_targets(
        extraction_provider="",
        extraction_model="gpt-5-mini",
        primary_provider="openai",
        primary_model="gpt-5-mini",
    ) == [ChatTarget(provider="openai", model="gpt-5-mini")]


def test_resolve_background_chat_targets_filters_empty_and_scaffold() -> None:
    assert resolve_background_chat_targets(
        extraction_provider="ollama",
        extraction_model="",
        primary_provider="scaffold",
        primary_model="unused",
    ) == []


def test_resolve_background_chat_targets_filters_fastembed() -> None:
    # fastembed is embeddings-only; it must never surface as a chat target.
    assert resolve_background_chat_targets(
        extraction_provider="fastembed",
        extraction_model="BAAI/bge-small-en-v1.5",
        primary_provider="fastembed",
        primary_model="BAAI/bge-small-en-v1.5",
    ) == []


def test_resolve_base_url_rejects_chat_incapable_fastembed() -> None:
    with pytest.raises(LLMConfigError, match="not chat-capable"):
        resolve_base_url("fastembed")


def test_embedding_provider_defaults_to_fastembed(monkeypatch: pytest.MonkeyPatch) -> None:
    from anima_server.services.agent import embeddings

    monkeypatch.setattr(settings, "agent_embedding_provider", "")
    monkeypatch.setattr(settings, "agent_embedding_model", "")
    monkeypatch.setattr(settings, "agent_embedding_base_url", "")
    monkeypatch.setattr(settings, "agent_provider", "ollama")

    assert embeddings._resolve_embedding_provider() == "fastembed"


def test_explicit_embedding_provider_still_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    from anima_server.services.agent import embeddings

    monkeypatch.setattr(settings, "agent_embedding_provider", "openai")

    assert embeddings._resolve_embedding_provider() == "openai"


def test_legacy_piggyback_kept_when_embedding_model_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import embeddings

    monkeypatch.setattr(settings, "agent_embedding_provider", "")
    monkeypatch.setattr(settings, "agent_embedding_model", "nomic-embed-text")
    monkeypatch.setattr(settings, "agent_provider", "ollama")

    assert embeddings._resolve_embedding_provider() == "ollama"


def test_legacy_piggyback_kept_when_only_embedding_api_key_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # P2 regression: agent_embedding_api_key (ANIMA_AGENT_EMBEDDING_API_KEY)
    # is equally explicit embedding intent as agent_embedding_model /
    # agent_embedding_base_url. An install with only the embedding API key
    # set must not silently fall through to the fastembed default and
    # ignore the configured key.
    from anima_server.services.agent import embeddings

    monkeypatch.setattr(settings, "agent_embedding_provider", "")
    monkeypatch.setattr(settings, "agent_embedding_model", "")
    monkeypatch.setattr(settings, "agent_embedding_base_url", "")
    monkeypatch.setattr(settings, "agent_embedding_api_key", "sk-only-embedding-key")
    monkeypatch.setattr(settings, "agent_provider", "openai")

    assert embeddings._resolve_embedding_provider() == "openai"


def test_embedding_provider_defaults_to_fastembed_when_nothing_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Nothing set (no embedding provider, model, base URL, or API key)
    # must still resolve to the bundled fastembed default.
    from anima_server.services.agent import embeddings

    monkeypatch.setattr(settings, "agent_embedding_provider", "")
    monkeypatch.setattr(settings, "agent_embedding_model", "")
    monkeypatch.setattr(settings, "agent_embedding_base_url", "")
    monkeypatch.setattr(settings, "agent_embedding_api_key", "")
    monkeypatch.setattr(settings, "agent_provider", "openai")

    assert embeddings._resolve_embedding_provider() == "fastembed"


def test_embedding_api_key_piggyback_still_uses_extraction_model_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Verify the api-key piggyback composes correctly with the
    # extraction-model-skip logic from commit 5c62215: piggybacking to a
    # non-fastembed provider via the api key alone must still apply the
    # existing agent_extraction_model fallback (only fastembed skips it).
    from anima_server.services.agent import embeddings

    monkeypatch.setattr(settings, "agent_embedding_provider", "")
    monkeypatch.setattr(settings, "agent_embedding_model", "")
    monkeypatch.setattr(settings, "agent_embedding_base_url", "")
    monkeypatch.setattr(settings, "agent_embedding_api_key", "sk-only-embedding-key")
    monkeypatch.setattr(settings, "agent_provider", "openai")
    monkeypatch.setattr(settings, "agent_extraction_model", "gpt-5-mini")

    assert embeddings._resolve_embedding_provider() == "openai"
    assert embeddings._resolve_embedding_model() == "gpt-5-mini"


def test_resolve_embedding_model_ignores_extraction_model_for_fastembed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # agent_extraction_model is a CHAT model setting (e.g. "qwen2.5:3b").
    # When no embedding config is present the resolved provider is the
    # bundled fastembed default, and feeding it a chat model name would
    # kill dense retrieval (TextEmbedding load fails -> failed latch).
    from anima_server.services.agent import embeddings

    monkeypatch.setattr(settings, "agent_embedding_provider", "")
    monkeypatch.setattr(settings, "agent_embedding_model", "")
    monkeypatch.setattr(settings, "agent_embedding_base_url", "")
    monkeypatch.setattr(settings, "agent_provider", "ollama")
    monkeypatch.setattr(settings, "agent_extraction_model", "qwen2.5:3b")

    assert embeddings._resolve_embedding_provider() == "fastembed"
    assert embeddings._resolve_embedding_model() == "BAAI/bge-small-en-v1.5"


def test_resolve_embedding_model_explicit_wins_over_fastembed_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import embeddings

    monkeypatch.setattr(settings, "agent_embedding_provider", "fastembed")
    monkeypatch.setattr(settings, "agent_embedding_model", "custom/embed-model")
    monkeypatch.setattr(settings, "agent_extraction_model", "qwen2.5:3b")

    assert embeddings._resolve_embedding_model() == "custom/embed-model"


def test_resolve_embedding_model_ignores_extraction_model_for_explicit_non_fastembed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Audit fix (was: "keeps_extraction_fallback_for_non_fastembed"): an
    # EXPLICITLY-configured embedding provider (agent_embedding_provider set)
    # must NEVER consult agent_extraction_model — that's a CHAT model
    # setting for a possibly-unrelated chat provider. The old behavior let a
    # chat extraction model (e.g. "qwen2.5:3b" for an ollama chat setup)
    # hijack an explicit embedding provider's request, e.g. POSTing a chat
    # model name to openai's /v1/embeddings -> 400. The extraction-model
    # fallback is now reserved strictly for the genuine piggyback case (see
    # test_embedding_api_key_piggyback_still_uses_extraction_model_fallback
    # above) where no embedding provider was named explicitly at all.
    from anima_server.services.agent import embeddings

    monkeypatch.setattr(settings, "agent_embedding_provider", "ollama")
    monkeypatch.setattr(settings, "agent_embedding_model", "")
    monkeypatch.setattr(settings, "agent_extraction_model", "qwen2.5:3b")

    assert embeddings._resolve_embedding_model() == "nomic-embed-text"


def test_resolve_embedding_model_explicit_openai_ignores_chat_extraction_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # HIGH severity regression guard: explicit embeddingProvider="openai"
    # with an unrelated chat agent_extraction_model set (e.g. left over from
    # an ollama chat configuration) must resolve to openai's OWN default
    # embedding model, not the chat model — which would 400 against
    # /v1/embeddings.
    from anima_server.services.agent import embeddings

    monkeypatch.setattr(settings, "agent_embedding_provider", "openai")
    monkeypatch.setattr(settings, "agent_embedding_model", "")
    monkeypatch.setattr(settings, "agent_provider", "ollama")
    monkeypatch.setattr(settings, "agent_extraction_model", "qwen2.5:3b")

    assert embeddings._resolve_embedding_model() == "text-embedding-3-small"


def test_resolve_embedding_model_genuine_piggyback_still_uses_extraction_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Mirrors the explicit-provider tests above but for the TRUE piggyback
    # case: no agent_embedding_provider named, piggyback intent signaled via
    # agent_embedding_base_url alone, resolved (chat) provider is non-ollama.
    # This is the one case where reusing agent_extraction_model is still the
    # documented, intentional legacy behavior.
    from anima_server.services.agent import embeddings

    monkeypatch.setattr(settings, "agent_embedding_provider", "")
    monkeypatch.setattr(settings, "agent_embedding_model", "")
    monkeypatch.setattr(settings, "agent_embedding_base_url", "http://custom-vllm:8000/v1")
    monkeypatch.setattr(settings, "agent_embedding_api_key", "")
    monkeypatch.setattr(settings, "agent_provider", "vllm")
    monkeypatch.setattr(settings, "agent_extraction_model", "qwen2.5:3b")

    assert embeddings._resolve_embedding_provider() == "vllm"
    assert embeddings._resolve_embedding_model() == "qwen2.5:3b"


def test_resolve_embedding_model_unknown_provider_returns_empty_not_ollama_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # FIX 2 defense-in-depth: a provider with no DEFAULT_EMBEDDING_MODELS
    # entry (e.g. moonshot — accepted by SUPPORTED_PROVIDERS/_embedding_skip_
    # reason but with no default embedding model) must not silently fall
    # through to the Ollama "nomic-embed-text" catch-all, which would send
    # an Ollama-only model name to a completely different provider's API
    # and 404. Empty string fails loudly instead.
    from anima_server.services.agent.embedding_resolution import (
        resolve_embedding_model,
    )

    monkeypatch.setattr(settings, "agent_embedding_provider", "moonshot")
    monkeypatch.setattr(settings, "agent_embedding_model", "")
    monkeypatch.setattr(settings, "agent_extraction_model", "")

    assert resolve_embedding_model("moonshot", settings) == ""


def test_resolve_embedding_base_url_explicit_local_ollama_falls_back_to_agent_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # P2 regression: an explicit local embedding provider (ollama) with no
    # dedicated embedding base URL must reuse the configured local server
    # address (agent_base_url) instead of silently reverting to the
    # hardcoded localhost default — the API has no field to set a distinct
    # embedding base URL, so agent_base_url is the only real signal. This is
    # the legitimate case: the embedding provider IS the chat provider, so
    # agent_base_url is known to point at ollama's own server.
    from anima_server.services.agent import embeddings

    monkeypatch.setattr(settings, "agent_provider", "ollama")
    monkeypatch.setattr(settings, "agent_embedding_provider", "ollama")
    monkeypatch.setattr(settings, "agent_embedding_base_url", "")
    monkeypatch.setattr(settings, "agent_base_url", "http://192.168.1.5:11434")

    assert embeddings._resolve_embedding_base_url() == "http://192.168.1.5:11434"


def test_resolve_embedding_base_url_explicit_vllm_matching_chat_provider_falls_back_to_agent_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Same legitimate-piggyback case as the ollama test above, for vllm:
    # chat provider and embedding provider both resolve to "vllm", so
    # agent_base_url is known to be vllm's own server address.
    from anima_server.services.agent import embeddings

    monkeypatch.setattr(settings, "agent_provider", "vllm")
    monkeypatch.setattr(settings, "agent_embedding_provider", "vllm")
    monkeypatch.setattr(settings, "agent_embedding_base_url", "")
    monkeypatch.setattr(settings, "agent_base_url", "http://192.168.1.5:8000/v1")

    assert embeddings._resolve_embedding_base_url() == "http://192.168.1.5:8000/v1"


def test_resolve_embedding_base_url_mismatched_local_provider_uses_its_own_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # P2 regression: chat=vllm (agent_base_url pointing at vllm's server) with
    # an explicit, DIFFERENT local embedding provider (ollama) must NOT reuse
    # agent_base_url — that would send ollama's /api/embed requests to the
    # vLLM port and silently fail dense retrieval. It must fall through to
    # ollama's own default base URL instead.
    from anima_server.services.agent import embeddings

    monkeypatch.setattr(settings, "agent_provider", "vllm")
    monkeypatch.setattr(settings, "agent_base_url", "http://127.0.0.1:8000/v1")
    monkeypatch.setattr(settings, "agent_embedding_provider", "ollama")
    monkeypatch.setattr(settings, "agent_embedding_base_url", "")

    assert embeddings._resolve_embedding_base_url() == "http://127.0.0.1:11434"


def test_resolve_embedding_base_url_mismatched_local_provider_vllm_uses_its_own_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Mirror case: chat=ollama, explicit embedding provider=vllm. Must use
    # vllm's own default base URL, not ollama's agent_base_url.
    from anima_server.services.agent import embeddings

    monkeypatch.setattr(settings, "agent_provider", "ollama")
    monkeypatch.setattr(settings, "agent_base_url", "http://192.168.1.5:11434")
    monkeypatch.setattr(settings, "agent_embedding_provider", "vllm")
    monkeypatch.setattr(settings, "agent_embedding_base_url", "")

    assert embeddings._resolve_embedding_base_url() == "http://127.0.0.1:8000/v1"


def test_resolve_embedding_base_url_explicit_embedding_base_url_still_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import embeddings

    monkeypatch.setattr(settings, "agent_embedding_provider", "ollama")
    monkeypatch.setattr(settings, "agent_embedding_base_url", "http://10.0.0.9:11434")
    monkeypatch.setattr(settings, "agent_base_url", "http://192.168.1.5:11434")

    assert embeddings._resolve_embedding_base_url() == "http://10.0.0.9:11434"


def test_resolve_embedding_base_url_non_local_explicit_provider_unaffected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # openai (non-local, fixed cloud endpoint) must keep its default base URL
    # even when agent_base_url happens to be set to some local server address
    # (e.g. left over from a prior ollama chat-provider configuration).
    from anima_server.services.agent import embeddings

    monkeypatch.setattr(settings, "agent_embedding_provider", "openai")
    monkeypatch.setattr(settings, "agent_embedding_base_url", "")
    monkeypatch.setattr(settings, "agent_base_url", "http://192.168.1.5:11434")

    assert embeddings._resolve_embedding_base_url() == "https://api.openai.com/v1"


@pytest.mark.asyncio
async def test_generate_embedding_skips_explicit_openrouter_embedding_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # fastembed is the bundled default when no embedding provider is
    # configured at all, so the meaningful case for openrouter (which has no
    # embeddings endpoint) is a user/legacy config that names it explicitly.
    from anima_server.services.agent import embeddings as embeddings_module

    async def unexpected_embed(text: str) -> list[float] | None:
        raise AssertionError(
            "OpenRouter has no embeddings endpoint; it must be skipped even "
            "when explicitly configured as the embedding provider")

    original_provider = settings.agent_provider
    original_embedding_provider = settings.agent_embedding_provider

    try:
        settings.agent_provider = "openrouter"
        settings.agent_embedding_provider = "openrouter"
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
async def test_generate_embedding_skips_explicit_anthropic_embedding_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # fastembed is the bundled default when no embedding provider is
    # configured at all, so the meaningful case for anthropic (which has no
    # embeddings endpoint) is a user/legacy config that names it explicitly.
    from anima_server.services.agent import embeddings as embeddings_module

    async def unexpected_embed(text: str) -> list[float] | None:
        raise AssertionError(
            "Anthropic has no embeddings endpoint; it must be skipped even "
            "when explicitly configured as the embedding provider"
        )

    original_provider = settings.agent_provider
    original_embedding_provider = settings.agent_embedding_provider

    try:
        settings.agent_provider = "anthropic"
        settings.agent_embedding_provider = "anthropic"
        monkeypatch.setattr(embeddings_module, "_embed_ollama", unexpected_embed)
        monkeypatch.setattr(
            embeddings_module, "_embed_openai_compatible", unexpected_embed
        )

        result = await generate_embedding("hello")
    finally:
        settings.agent_provider = original_provider
        settings.agent_embedding_provider = original_embedding_provider

    assert result is None


@pytest.mark.asyncio
async def test_generate_embedding_skips_explicit_moonshot_no_default_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # STRUCTURAL FIX (was reachable at runtime despite being excluded from
    # every user-facing surface): moonshot has a real, openai-compatible
    # embeddings endpoint (no `_embedding_skip_reason`) but no entry in
    # DEFAULT_EMBEDDING_MODELS, so `generate_embedding` must refuse it
    # WITHOUT ever making an HTTP call — exactly like the endpoint-less
    # openrouter/anthropic skips above — even when a (leftover) embedding key
    # is present, so the runtime path agrees with VALID_EMBEDDING_PROVIDERS.
    from anima_server.services.agent import embeddings as embeddings_module

    async def unexpected_embed(text: str) -> list[float] | None:
        raise AssertionError(
            "moonshot has no default embedding model; it must be skipped "
            "before any HTTP call is attempted, even with a usable key"
        )

    original_provider = settings.agent_provider
    original_embedding_provider = settings.agent_embedding_provider
    original_embedding_model = settings.agent_embedding_model
    original_embedding_api_key = settings.agent_embedding_api_key

    try:
        settings.agent_provider = "moonshot"
        settings.agent_embedding_provider = "moonshot"
        settings.agent_embedding_model = ""
        settings.agent_embedding_api_key = "sk-leftover-moonshot-key"
        monkeypatch.setattr(embeddings_module, "_embed_ollama", unexpected_embed)
        monkeypatch.setattr(
            embeddings_module, "_embed_openai_compatible", unexpected_embed
        )

        result = await generate_embedding("hello")
    finally:
        settings.agent_provider = original_provider
        settings.agent_embedding_provider = original_embedding_provider
        settings.agent_embedding_model = original_embedding_model
        settings.agent_embedding_api_key = original_embedding_api_key

    assert result is None


@pytest.mark.asyncio
async def test_generate_embedding_skips_moonshot_even_with_explicit_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # P1 (Codex round 10): the leak the structural fix half-closed. A legacy
    # moonshot config WITH an explicit embedding model previously bypassed the
    # has-default-model gate and POSTed real memory/document text to the
    # moonshot endpoint while the UI showed fastembed. An explicit model must
    # NOT rescue an unsupported provider — generate_embedding must still refuse
    # it WITHOUT any HTTP call.
    from anima_server.services.agent import embeddings as embeddings_module

    async def unexpected_embed(text: str) -> list[float] | None:
        raise AssertionError(
            "moonshot is not a supported embedding provider; an explicit "
            "model must not make it reachable at the HTTP call site"
        )

    original_provider = settings.agent_provider
    original_embedding_provider = settings.agent_embedding_provider
    original_embedding_model = settings.agent_embedding_model
    original_embedding_api_key = settings.agent_embedding_api_key

    try:
        settings.agent_provider = "moonshot"
        settings.agent_embedding_provider = "moonshot"
        settings.agent_embedding_model = "custom-embed-model"
        settings.agent_embedding_api_key = "sk-leftover-moonshot-key"
        monkeypatch.setattr(embeddings_module, "_embed_ollama", unexpected_embed)
        monkeypatch.setattr(
            embeddings_module, "_embed_openai_compatible", unexpected_embed
        )

        result = await generate_embedding("hello")
    finally:
        settings.agent_provider = original_provider
        settings.agent_embedding_provider = original_embedding_provider
        settings.agent_embedding_model = original_embedding_model
        settings.agent_embedding_api_key = original_embedding_api_key

    assert result is None


def test_embedding_provider_unusable_reason_moonshot_has_no_default_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import embeddings as embeddings_module

    monkeypatch.setattr(settings, "agent_embedding_model", "")

    reason = embeddings_module.embedding_provider_unusable_reason("moonshot")
    assert reason is not None
    assert "moonshot" in reason


def test_embedding_provider_unusable_reason_openai_usable_with_key_and_default_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import embeddings as embeddings_module

    monkeypatch.setattr(settings, "agent_embedding_provider", "openai")
    monkeypatch.setattr(settings, "agent_embedding_api_key", "sk-test-openai-key")

    assert embeddings_module.embedding_provider_unusable_reason("openai") is None


def test_embedding_provider_unusable_reason_ollama_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import embeddings as embeddings_module

    assert embeddings_module.embedding_provider_unusable_reason("ollama") is None


def test_embedding_provider_unusable_reason_openrouter_still_excluded() -> None:
    from anima_server.services.agent import embeddings as embeddings_module

    assert embeddings_module.embedding_provider_unusable_reason("openrouter") is not None


def test_embedding_provider_unusable_reason_anthropic_still_excluded() -> None:
    from anima_server.services.agent import embeddings as embeddings_module

    assert embeddings_module.embedding_provider_unusable_reason("anthropic") is not None


def test_embedding_provider_unusable_reason_moonshot_blocked_even_with_explicit_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # P1 (Codex round 10): an explicit agent_embedding_model must NOT rescue a
    # provider that isn't a supported embedding provider. Membership in
    # DEFAULT_EMBEDDING_MODELS is a property of the PROVIDER (is it an
    # embedding provider at all); a model string only selects which model
    # within a supported one. A legacy moonshot config WITH an explicit model
    # previously slipped through and kept POSTing memory/document text to the
    # moonshot endpoint while the UI showed fastembed — it must stay blocked.
    from anima_server.services.agent import embeddings as embeddings_module

    monkeypatch.setattr(settings, "agent_embedding_provider", "moonshot")
    monkeypatch.setattr(settings, "agent_embedding_model", "custom-embed-model")
    monkeypatch.setattr(settings, "agent_embedding_api_key", "sk-test-moonshot-key")

    reason = embeddings_module.embedding_provider_unusable_reason("moonshot")
    assert reason is not None
    assert "moonshot" in reason


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
            # fastembed is the bundled default now; pin the embedding
            # provider explicitly to keep exercising the ollama backend.
            agent_embedding_provider="ollama",
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


def test_build_provider_headers_supports_anthropic() -> None:
    original_api_key = settings.agent_api_key

    try:
        settings.agent_api_key = "test-anthropic-key"
        headers = build_provider_headers("anthropic")
    finally:
        settings.agent_api_key = original_api_key

    assert headers == {
        "x-api-key": "test-anthropic-key",
        "anthropic-version": "2023-06-01",
    }


def test_build_provider_headers_rejects_anthropic_without_api_key() -> None:
    original_api_key = settings.agent_api_key

    try:
        settings.agent_api_key = ""
        with pytest.raises(
            LLMConfigError,
            match="ANIMA_AGENT_API_KEY is required",
        ):
            build_provider_headers("anthropic")
    finally:
        settings.agent_api_key = original_api_key


def test_doubleword_resolves_default_base_url_and_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_api_key = settings.agent_api_key
    original_base_url = settings.agent_base_url
    monkeypatch.delenv("DOUBLEWORD_API_KEY", raising=False)

    try:
        settings.agent_api_key = "test-doubleword-key"
        settings.agent_base_url = ""

        assert resolve_base_url("doubleword") == "https://api.doubleword.ai/v1"
        assert build_provider_headers("doubleword") == {
            "Authorization": "Bearer test-doubleword-key"
        }
    finally:
        settings.agent_api_key = original_api_key
        settings.agent_base_url = original_base_url


def test_doubleword_uses_provider_specific_env_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_api_key = settings.agent_api_key
    monkeypatch.setenv("DOUBLEWORD_API_KEY", "env-doubleword-key")

    try:
        settings.agent_api_key = ""

        assert build_provider_headers("doubleword") == {
            "Authorization": "Bearer env-doubleword-key"
        }
    finally:
        settings.agent_api_key = original_api_key


def test_doubleword_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    original_api_key = settings.agent_api_key
    monkeypatch.delenv("DOUBLEWORD_API_KEY", raising=False)

    try:
        settings.agent_api_key = ""
        with pytest.raises(
            LLMConfigError,
            match="DOUBLEWORD_API_KEY",
        ):
            build_provider_headers("doubleword")
    finally:
        settings.agent_api_key = original_api_key


def test_provider_key_map_prevents_legacy_key_leakage() -> None:
    original_api_key = settings.agent_api_key
    original_api_keys_json = settings.agent_api_keys_json

    try:
        settings.agent_api_key = "legacy-key"
        settings.agent_api_keys_json = '{"openai": "openai-key"}'

        assert resolve_provider_api_key("openai") == "openai-key"
        assert resolve_provider_api_key("anthropic") == ""
    finally:
        settings.agent_api_key = original_api_key
        settings.agent_api_keys_json = original_api_keys_json


def test_create_llm_uses_openai_compatible_client_for_doubleword() -> None:
    from anima_server.services.agent.openai_compatible_client import (
        OpenAICompatibleChatClient,
    )

    original_api_key = settings.agent_api_key
    original_base_url = settings.agent_base_url

    try:
        settings.agent_api_key = "test-doubleword-key"
        settings.agent_base_url = ""

        client = create_provider_chat_client(
            provider="doubleword",
            model="Qwen/Qwen3.6-35B-A3B-FP8",
            timeout=12.0,
        )
    finally:
        settings.agent_api_key = original_api_key
        settings.agent_base_url = original_base_url

    assert isinstance(client, OpenAICompatibleChatClient)
    assert client.provider == "doubleword"
    assert client.model == "Qwen/Qwen3.6-35B-A3B-FP8"
    assert client.base_url == "https://api.doubleword.ai/v1"


def test_doubleword_embedding_defaults_and_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import embeddings as embeddings_module

    monkeypatch.delenv("DOUBLEWORD_API_KEY", raising=False)
    monkeypatch.setattr(
        embeddings_module,
        "settings",
        SimpleNamespace(
            agent_provider="doubleword",
            agent_base_url="",
            agent_api_key="test-doubleword-key",
            # fastembed is the bundled default now; pin the embedding
            # provider explicitly to keep exercising doubleword's defaults.
            agent_embedding_provider="doubleword",
            agent_embedding_base_url="",
            agent_embedding_model="",
            agent_embedding_api_key="",
            agent_extraction_model="",
        ),
    )

    assert embeddings_module.resolve_base_url() == "https://api.doubleword.ai/v1"
    assert embeddings_module._resolve_embedding_model() == "Qwen/Qwen3-Embedding-8B"
    assert embeddings_module.build_provider_headers("doubleword") == {
        "Authorization": "Bearer test-doubleword-key"
    }


def test_doubleword_embedding_uses_provider_specific_env_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import embeddings as embeddings_module

    monkeypatch.setenv("DOUBLEWORD_API_KEY", "env-doubleword-key")
    monkeypatch.setattr(
        embeddings_module,
        "settings",
        SimpleNamespace(
            agent_provider="doubleword",
            agent_base_url="",
            agent_api_key="",
            agent_embedding_provider="",
            agent_embedding_base_url="",
            agent_embedding_model="",
            agent_embedding_api_key="",
            agent_extraction_model="",
        ),
    )

    assert embeddings_module.build_provider_headers("doubleword") == {
        "Authorization": "Bearer env-doubleword-key"
    }


def test_doubleword_embedding_uses_saved_provider_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import embeddings as embeddings_module

    original_api_key = settings.agent_api_key
    original_api_keys_json = settings.agent_api_keys_json
    original_embedding_api_key = settings.agent_embedding_api_key
    monkeypatch.delenv("DOUBLEWORD_API_KEY", raising=False)

    try:
        settings.agent_api_key = ""
        settings.agent_api_keys_json = '{"doubleword": "saved-doubleword-key"}'
        settings.agent_embedding_api_key = ""

        assert embeddings_module.build_provider_headers("doubleword") == {
            "Authorization": "Bearer saved-doubleword-key"
        }
    finally:
        settings.agent_api_key = original_api_key
        settings.agent_api_keys_json = original_api_keys_json
        settings.agent_embedding_api_key = original_embedding_api_key


def test_embedding_provider_key_map_prevents_legacy_key_leakage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import embeddings as embeddings_module

    original_api_key = settings.agent_api_key
    original_api_keys_json = settings.agent_api_keys_json
    original_embedding_api_key = settings.agent_embedding_api_key
    monkeypatch.delenv("DOUBLEWORD_API_KEY", raising=False)

    try:
        settings.agent_api_key = "legacy-key"
        settings.agent_api_keys_json = '{"openai": "openai-key"}'
        settings.agent_embedding_api_key = ""

        assert embeddings_module._resolve_embedding_api_key("openai") == "openai-key"
        assert embeddings_module._resolve_embedding_api_key("doubleword") == ""
    finally:
        settings.agent_api_key = original_api_key
        settings.agent_api_keys_json = original_api_keys_json
        settings.agent_embedding_api_key = original_embedding_api_key


def test_legacy_chat_key_not_reused_for_different_embedding_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The flat legacy ``agent_api_key`` field predates the per-provider key
    store and always held the CHAT provider's key. Falling back to it for an
    embedding provider that differs from the chat provider would silently
    authorize the NEW embedding provider's requests with the OLD chat
    provider's secret — a cross-provider key leak. It must only be reused
    when the resolved embedding provider is the SAME as the chat provider
    (the legitimate piggyback case)."""
    from anima_server.services.agent import embeddings as embeddings_module

    original_provider = settings.agent_provider
    original_api_key = settings.agent_api_key
    original_api_keys_json = settings.agent_api_keys_json
    original_embedding_api_key = settings.agent_embedding_api_key
    monkeypatch.delenv("DOUBLEWORD_API_KEY", raising=False)

    try:
        settings.agent_provider = "openai"
        settings.agent_api_key = "sk-openai"
        settings.agent_api_keys_json = "{}"
        settings.agent_embedding_api_key = ""

        # Embedding provider differs from the chat provider — must NOT leak
        # the openai chat key to doubleword.
        assert embeddings_module._resolve_embedding_api_key("doubleword") == ""

        # Embedding provider equals the chat provider — legitimate piggyback,
        # the chat key is preserved.
        assert embeddings_module._resolve_embedding_api_key("openai") == "sk-openai"
    finally:
        settings.agent_provider = original_provider
        settings.agent_api_key = original_api_key
        settings.agent_api_keys_json = original_api_keys_json
        settings.agent_embedding_api_key = original_embedding_api_key


def test_create_llm_uses_anthropic_client() -> None:
    from anima_server.services.agent.anthropic_client import AnthropicChatClient

    original_provider = settings.agent_provider
    original_model = settings.agent_model
    original_api_key = settings.agent_api_key
    original_base_url = settings.agent_base_url

    try:
        settings.agent_provider = "anthropic"
        settings.agent_model = "claude-haiku-4-5-20251001"
        settings.agent_api_key = "test-anthropic-key"
        settings.agent_base_url = ""
        invalidate_llm_cache()

        client = create_llm()
        anthropic_base_url = resolve_base_url("anthropic")
    finally:
        invalidate_llm_cache()
        settings.agent_provider = original_provider
        settings.agent_model = original_model
        settings.agent_api_key = original_api_key
        settings.agent_base_url = original_base_url

    assert isinstance(client, AnthropicChatClient)
    assert client.model == "claude-haiku-4-5-20251001"
    assert anthropic_base_url == "https://api.anthropic.com/v1"


def test_resolve_base_url_scopes_override_to_primary_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "agent_provider", "ollama")
    monkeypatch.setattr(settings, "agent_base_url", "http://127.0.0.1:11434")

    assert resolve_base_url("ollama") == "http://127.0.0.1:11434/v1"
    assert resolve_base_url("anthropic") == "https://api.anthropic.com/v1"


def test_create_llm_uses_configured_temperature() -> None:
    from anima_server.services.agent.anthropic_client import AnthropicChatClient

    original_provider = settings.agent_provider
    original_model = settings.agent_model
    original_api_key = settings.agent_api_key
    original_base_url = settings.agent_base_url
    original_temperature = settings.agent_temperature

    try:
        settings.agent_provider = "anthropic"
        settings.agent_model = "claude-haiku-4-5-20251001"
        settings.agent_api_key = "test-anthropic-key"
        settings.agent_base_url = ""
        settings.agent_temperature = 0.0
        invalidate_llm_cache()

        client = create_llm()
    finally:
        invalidate_llm_cache()
        settings.agent_provider = original_provider
        settings.agent_model = original_model
        settings.agent_api_key = original_api_key
        settings.agent_base_url = original_base_url
        settings.agent_temperature = original_temperature

    assert isinstance(client, AnthropicChatClient)
    assert client._temperature == 0.0


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


def test_resolve_embedding_dim_uses_doubleword_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server import config as config_module

    monkeypatch.setattr(
        config_module,
        "settings",
        SimpleNamespace(
            agent_embedding_model="",
            agent_extraction_model="",
            # fastembed is the bundled default now; pin the embedding
            # provider explicitly to keep exercising doubleword's default.
            agent_embedding_provider="doubleword",
            agent_provider="doubleword",
            agent_embedding_dim=768,
        ),
    )
    config_module.clear_detected_embedding_dim()

    assert config_module.resolve_embedding_dim() == 4096


def test_resolve_embedding_dim_knows_bundled_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server import config as config_module

    monkeypatch.setattr(
        config_module,
        "settings",
        SimpleNamespace(
            agent_embedding_model="BAAI/bge-small-en-v1.5",
            agent_extraction_model="",
            agent_embedding_provider="fastembed",
            agent_provider="fastembed",
            agent_embedding_dim=768,
        ),
    )
    config_module.clear_detected_embedding_dim()

    assert config_module.resolve_embedding_dim() == 384


def test_resolve_embedding_dim_ignores_extraction_model_for_fastembed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Mirrors test_resolve_embedding_model_ignores_extraction_model_for_fastembed:
    # extraction model is chat intent, not embedding intent, and must not leak
    # into the dim lookup for the resolved fastembed provider either — else
    # this resolves to the 768 fallback while the bundled default is 384.
    from anima_server import config as config_module

    monkeypatch.setattr(
        config_module,
        "settings",
        SimpleNamespace(
            agent_embedding_model="",
            agent_extraction_model="qwen2.5:3b",
            agent_embedding_provider="",
            agent_embedding_base_url="",
            agent_provider="ollama",
            agent_embedding_dim=768,
        ),
    )
    config_module.clear_detected_embedding_dim()

    assert config_module.resolve_embedding_dim() == 384


def test_resolve_embedding_dim_explicit_model_wins_over_fastembed_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server import config as config_module

    monkeypatch.setattr(
        config_module,
        "settings",
        SimpleNamespace(
            agent_embedding_model="BAAI/bge-small-en-v1.5",
            agent_extraction_model="qwen2.5:3b",
            agent_embedding_provider="fastembed",
            agent_embedding_base_url="",
            agent_provider="fastembed",
            agent_embedding_dim=768,
        ),
    )
    config_module.clear_detected_embedding_dim()

    assert config_module.resolve_embedding_dim() == 384


def test_resolve_embedding_dim_ignores_extraction_fallback_for_explicit_non_fastembed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Audit fix (was: "keeps_extraction_fallback_for_non_fastembed"): mirrors
    # test_resolve_embedding_model_ignores_extraction_model_for_explicit_non_fastembed
    # for dimension resolution. An explicit agent_embedding_provider must not
    # consult agent_extraction_model (a chat setting) — it resolves to
    # ollama's own default model ("nomic-embed-text", dim 768) instead of the
    # chat extraction model's dim (1024 for mxbai-embed-large).
    from anima_server import config as config_module

    monkeypatch.setattr(
        config_module,
        "settings",
        SimpleNamespace(
            agent_embedding_model="",
            agent_extraction_model="mxbai-embed-large",
            agent_embedding_provider="ollama",
            agent_embedding_base_url="",
            agent_provider="ollama",
            agent_embedding_dim=768,
        ),
    )
    config_module.clear_detected_embedding_dim()

    assert config_module.resolve_embedding_dim() == 768


def test_resolve_default_embedding_provider_piggybacks_on_embedding_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # P2 regression: agent_embedding_api_key alone is explicit embedding
    # intent, just like agent_embedding_model or agent_embedding_base_url.
    # Lives in embedding_resolution.py now — the single copy shared by
    # config.py and services.agent.embeddings.
    from anima_server import config as config_module
    from anima_server.services.agent import embedding_resolution

    monkeypatch.setattr(
        config_module,
        "settings",
        SimpleNamespace(
            agent_embedding_model="",
            agent_extraction_model="",
            agent_embedding_provider="",
            agent_embedding_base_url="",
            agent_embedding_api_key="sk-only-embedding-key",
            agent_provider="openai",
            agent_embedding_dim=768,
        ),
    )

    assert embedding_resolution.resolve_embedding_provider() == "openai"


def test_resolve_default_embedding_provider_defaults_to_fastembed_when_nothing_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server import config as config_module
    from anima_server.services.agent import embedding_resolution

    monkeypatch.setattr(
        config_module,
        "settings",
        SimpleNamespace(
            agent_embedding_model="",
            agent_extraction_model="",
            agent_embedding_provider="",
            agent_embedding_base_url="",
            agent_embedding_api_key="",
            agent_provider="openai",
            agent_embedding_dim=768,
        ),
    )

    assert embedding_resolution.resolve_embedding_provider() == "fastembed"


def test_resolve_embedding_dim_uses_openai_default_for_embedding_api_key_piggyback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Only the embedding API key is set (agent_provider="openai"): the
    # piggyback must resolve to "openai" and the dim lookup must use
    # openai's default embedding model (text-embedding-3-small -> 1536),
    # not the fastembed default (384) or the generic 768 fallback.
    from anima_server import config as config_module

    monkeypatch.setattr(
        config_module,
        "settings",
        SimpleNamespace(
            agent_embedding_model="",
            agent_extraction_model="",
            agent_embedding_provider="",
            agent_embedding_base_url="",
            agent_embedding_api_key="sk-only-embedding-key",
            agent_provider="openai",
            agent_embedding_dim=768,
        ),
    )
    config_module.clear_detected_embedding_dim()

    assert config_module.resolve_embedding_dim() == 1536


@pytest.mark.asyncio
async def test_generate_embedding_dispatches_to_fastembed_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import embeddings as embeddings_module
    from anima_server.services.agent import fastembed_backend

    calls: list[tuple[list[str], str]] = []

    def fake_embed_texts(texts: list[str], *, model_name: str) -> list[list[float] | None]:
        calls.append((list(texts), model_name))
        return [[0.5] * 384 for _ in texts]

    original_provider = settings.agent_provider
    original_embedding_provider = settings.agent_embedding_provider
    original_embedding_model = settings.agent_embedding_model

    try:
        settings.agent_embedding_provider = "fastembed"
        settings.agent_embedding_model = "BAAI/bge-small-en-v1.5"
        monkeypatch.setattr(fastembed_backend, "embed_texts", fake_embed_texts)
        embeddings_module.clear_embedding_cache()

        result = await embeddings_module.generate_embedding("hello world")
    finally:
        settings.agent_provider = original_provider
        settings.agent_embedding_provider = original_embedding_provider
        settings.agent_embedding_model = original_embedding_model

    assert result == [0.5] * 384
    assert calls == [(["hello world"], "BAAI/bge-small-en-v1.5")]


@pytest.mark.asyncio
async def test_generate_embeddings_batch_dispatches_to_fastembed_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import embeddings as embeddings_module
    from anima_server.services.agent import fastembed_backend

    calls: list[tuple[list[str], str]] = []

    def fake_embed_texts(texts: list[str], *, model_name: str) -> list[list[float] | None]:
        calls.append((list(texts), model_name))
        return [[0.25] * 384 for _ in texts]

    original_provider = settings.agent_provider
    original_embedding_provider = settings.agent_embedding_provider
    original_embedding_model = settings.agent_embedding_model

    try:
        settings.agent_embedding_provider = "fastembed"
        settings.agent_embedding_model = "BAAI/bge-small-en-v1.5"
        monkeypatch.setattr(fastembed_backend, "embed_texts", fake_embed_texts)
        embeddings_module.clear_embedding_cache()

        result = await embeddings_module.generate_embeddings_batch(["hello", "world"])
    finally:
        settings.agent_provider = original_provider
        settings.agent_embedding_provider = original_embedding_provider
        settings.agent_embedding_model = original_embedding_model

    assert result == [[0.25] * 384, [0.25] * 384]
    assert calls == [(["hello", "world"], "BAAI/bge-small-en-v1.5")]


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
            # fastembed is the bundled default now; pin the embedding
            # provider explicitly to keep exercising the ollama backend.
            agent_embedding_provider="ollama",
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
            # fastembed is the bundled default now; pin the embedding
            # provider explicitly to keep exercising the ollama backend.
            agent_embedding_provider="ollama",
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
            # fastembed is the bundled default now; pin the embedding
            # provider explicitly to keep exercising the ollama backend.
            agent_embedding_provider="ollama",
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


@pytest.mark.asyncio
async def test_batch_embed_openai_compatible_marks_provider_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # P2: the batch path (backfill/re-embed) used to swallow an HTTP outage and
    # return None entries WITHOUT recording the cooldown, so http_backend_status
    # kept reporting "ready" while batch embeddings were actually failing. It
    # must mark the provider unavailable on the SAME key the status reads.
    from anima_server.services.agent import embeddings as embeddings_module

    request = httpx.Request("POST", "http://127.0.0.1:8000/v1/embeddings")

    class _FailingAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> _FailingAsyncClient:
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        async def post(self, *args: object, **kwargs: object) -> object:
            raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(
        embeddings_module,
        "settings",
        SimpleNamespace(
            agent_provider="vllm",
            agent_base_url="http://127.0.0.1:8000/v1",
            agent_api_key="",
            agent_embedding_provider="vllm",
            agent_embedding_base_url="",
            agent_embedding_model="",
            agent_embedding_api_key="",
            agent_extraction_model="",
        ),
    )
    monkeypatch.setattr(embeddings_module.httpx, "AsyncClient", _FailingAsyncClient)
    embeddings_module._provider_unavailable_until.clear()

    # Sanity: healthy before the outage.
    assert embeddings_module.http_backend_status("vllm") == "ready"

    results = await embeddings_module._batch_embed_openai_compatible(["a", "b"])

    assert results == [None, None]
    # The batch outage is now visible to the trust surface.
    assert embeddings_module.http_backend_status("vllm") == "failed_retrying"

    embeddings_module._provider_unavailable_until.clear()


def test_resolve_embedding_api_key_never_returns_key_for_fastembed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2: fastembed is in-process (no HTTP endpoint, no API key). A stored
    agent_embedding_api_key must never be attributed to it — otherwise a
    legacy piggyback config whose get_config DISPLAY is normalized to
    'fastembed' would report hasEmbeddingApiKey=true for a provider that
    can't use one."""
    from anima_server.services.agent import embeddings as embeddings_module

    monkeypatch.setattr(settings, "agent_embedding_api_key", "sk-leftover")
    assert embeddings_module._resolve_embedding_api_key("fastembed") == ""
