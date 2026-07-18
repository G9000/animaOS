from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from conftest import managed_test_client


def _mock_session(user_id: int = 1) -> MagicMock:
    session = MagicMock()
    session.user_id = user_id
    return session


def _fake_capabilities(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "parsingPack": {"state": "ready", "progress": None, "error": None},
        "embeddings": {
            "provider": "fastembed",
            "model": "BAAI/bge-small-en-v1.5",
            "dim": 384,
            "backend": "ready",
        },
        "reranker": {
            "enabled": True,
            "model": "Xenova/ms-marco-MiniLM-L-6-v2",
            "backend": "ready",
        },
        "llm": {"configured": True},
        "contextualChunks": True,
        "fullDocumentContext": True,
    }
    base.update(overrides)
    return base


def test_get_capabilities_returns_payload_shape() -> None:
    fake = _fake_capabilities()

    with managed_test_client("anima-capabilities-api-", invalidate_agent=False) as client:
        with patch(
            "anima_server.api.routes.capabilities.require_unlocked_session",
            return_value=_mock_session(),
        ), patch(
            "anima_server.api.routes.capabilities.collect_capabilities",
            return_value=fake,
        ):
            response = client.get("/api/capabilities")

        assert response.status_code == 200
        data = response.json()
        assert data == fake
        assert set(data.keys()) == {
            "parsingPack",
            "embeddings",
            "reranker",
            "llm",
            "contextualChunks",
            "fullDocumentContext",
        }
        assert set(data["parsingPack"].keys()) == {"state", "progress", "error"}
        assert set(data["embeddings"].keys()) == {"provider", "model", "dim", "backend"}
        assert set(data["reranker"].keys()) == {"enabled", "model", "backend"}
        assert data["llm"] == {"configured": True}


def test_get_capabilities_requires_auth() -> None:
    with managed_test_client("anima-capabilities-api-", invalidate_agent=False) as client:
        response = client.get("/api/capabilities")
        assert response.status_code == 401


def test_get_capabilities_reflects_degraded_backend_states() -> None:
    fake = _fake_capabilities(
        parsingPack={"state": "downloading", "progress": 0.4, "error": None},
        embeddings={
            "provider": "fastembed",
            "model": "BAAI/bge-small-en-v1.5",
            "dim": 384,
            "backend": "failed_retrying",
        },
    )

    with managed_test_client("anima-capabilities-api-", invalidate_agent=False) as client:
        with patch(
            "anima_server.api.routes.capabilities.require_unlocked_session",
            return_value=_mock_session(),
        ), patch(
            "anima_server.api.routes.capabilities.collect_capabilities",
            return_value=fake,
        ):
            response = client.get("/api/capabilities")

        assert response.status_code == 200
        data = response.json()
        assert data["parsingPack"]["state"] == "downloading"
        assert data["embeddings"]["backend"] == "failed_retrying"


# ---------------------------------------------------------------------------
# services.capabilities.collect_capabilities
# ---------------------------------------------------------------------------


def test_collect_capabilities_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    from anima_server.services import capabilities as capabilities_module
    from anima_server.services.documents.parsing_pack import ParsingPackStatus

    monkeypatch.setattr(
        capabilities_module, "pack_status", lambda: ParsingPackStatus(state="ready")
    )
    monkeypatch.setattr(
        capabilities_module, "_resolve_embedding_provider", lambda: "fastembed"
    )
    monkeypatch.setattr(
        capabilities_module, "_resolve_embedding_model", lambda: "BAAI/bge-small-en-v1.5"
    )
    monkeypatch.setattr(capabilities_module, "resolve_embedding_dim", lambda: 384)
    monkeypatch.setattr(capabilities_module, "fastembed_backend_status", lambda: "ready")
    monkeypatch.setattr(capabilities_module, "reranker_backend_status", lambda: "cold")
    monkeypatch.setattr(capabilities_module, "_llm_configured", lambda: True)

    result = capabilities_module.collect_capabilities()

    assert result["parsingPack"] == {"state": "ready", "progress": None, "error": None}
    assert result["embeddings"] == {
        "provider": "fastembed",
        "model": "BAAI/bge-small-en-v1.5",
        "dim": 384,
        "backend": "ready",
    }
    assert result["reranker"]["backend"] == "cold"
    assert result["llm"] == {"configured": True}
    assert isinstance(result["contextualChunks"], bool)
    assert isinstance(result["fullDocumentContext"], bool)


def test_collect_capabilities_backend_is_provider_truthful_for_http_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """embeddings.backend must reflect the *active* provider, not always fastembed.

    Regression guard for the carried-in review requirement: when the
    resolved embedding provider is an HTTP provider (e.g. openai), the
    backend status must come from the HTTP cooldown latch, never the
    unrelated fastembed model-load latch.
    """
    from anima_server.services import capabilities as capabilities_module
    from anima_server.services.documents.parsing_pack import ParsingPackStatus

    monkeypatch.setattr(
        capabilities_module, "pack_status", lambda: ParsingPackStatus(state="ready")
    )
    monkeypatch.setattr(
        capabilities_module, "_resolve_embedding_provider", lambda: "openai"
    )
    monkeypatch.setattr(
        capabilities_module, "_resolve_embedding_model", lambda: "text-embedding-3-small"
    )
    monkeypatch.setattr(capabilities_module, "resolve_embedding_dim", lambda: 1536)
    # The fastembed latch is deliberately wired to report "ready" here so the
    # test would fail loudly if collect_capabilities ever fell back to it for
    # a non-fastembed provider.
    monkeypatch.setattr(capabilities_module, "fastembed_backend_status", lambda: "ready")
    monkeypatch.setattr(
        capabilities_module, "http_backend_status", lambda provider: "failed_retrying"
    )
    monkeypatch.setattr(capabilities_module, "reranker_backend_status", lambda: "cold")
    monkeypatch.setattr(capabilities_module, "_llm_configured", lambda: True)

    result = capabilities_module.collect_capabilities()

    assert result["embeddings"] == {
        "provider": "openai",
        "model": "text-embedding-3-small",
        "dim": 1536,
        "backend": "failed_retrying",
    }


def test_embedding_backend_status_uses_fastembed_latch_for_fastembed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services import capabilities as capabilities_module

    monkeypatch.setattr(capabilities_module, "fastembed_backend_status", lambda: "cold")
    monkeypatch.setattr(
        capabilities_module,
        "http_backend_status",
        lambda provider: (_ for _ in ()).throw(AssertionError("must not be called")),
    )

    assert capabilities_module._embedding_backend_status("fastembed") == "cold"


def test_embedding_backend_status_uses_http_cooldown_for_other_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services import capabilities as capabilities_module

    monkeypatch.setattr(
        capabilities_module,
        "fastembed_backend_status",
        lambda: (_ for _ in ()).throw(AssertionError("must not be called")),
    )
    monkeypatch.setattr(
        capabilities_module, "http_backend_status", lambda provider: "ready"
    )

    assert capabilities_module._embedding_backend_status("ollama") == "ready"


def test_llm_configured_false_when_no_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    from anima_server.services import capabilities as capabilities_module

    monkeypatch.setattr(
        capabilities_module, "resolve_background_chat_targets", lambda: []
    )
    assert capabilities_module._llm_configured() is False


def test_llm_configured_false_when_validation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services import capabilities as capabilities_module
    from anima_server.services.agent.llm import ChatTarget, LLMConfigError

    monkeypatch.setattr(
        capabilities_module,
        "resolve_background_chat_targets",
        lambda: [ChatTarget(provider="openai", model="gpt-4o-mini")],
    )

    def _raise(provider: str) -> None:
        raise LLMConfigError("missing key")

    monkeypatch.setattr(capabilities_module, "validate_provider_configuration", _raise)
    assert capabilities_module._llm_configured() is False


def test_llm_configured_true_when_target_validates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services import capabilities as capabilities_module
    from anima_server.services.agent.llm import ChatTarget

    monkeypatch.setattr(
        capabilities_module,
        "resolve_background_chat_targets",
        lambda: [ChatTarget(provider="ollama", model="qwen2.5:3b")],
    )
    monkeypatch.setattr(
        capabilities_module, "validate_provider_configuration", lambda provider: None
    )
    assert capabilities_module._llm_configured() is True


# ---------------------------------------------------------------------------
# fastembed_backend.backend_status / reranker.backend_status
# ---------------------------------------------------------------------------


def test_fastembed_backend_status_cold_by_default() -> None:
    from anima_server.services.agent import fastembed_backend

    fastembed_backend._reset_backend_for_tests()
    assert fastembed_backend.backend_status() == "cold"


def test_fastembed_backend_status_ready_when_model_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import fastembed_backend

    fastembed_backend._reset_backend_for_tests()
    monkeypatch.setattr(
        fastembed_backend, "_resolve_current_model_name", lambda: "model-a"
    )
    fastembed_backend._model = object()
    fastembed_backend._model_name_loaded = "model-a"
    try:
        assert fastembed_backend.backend_status() == "ready"
    finally:
        fastembed_backend._reset_backend_for_tests()


def test_fastembed_backend_status_failed_retrying_within_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    from anima_server.services.agent import fastembed_backend

    fastembed_backend._reset_backend_for_tests()
    monkeypatch.setattr(
        fastembed_backend, "_resolve_current_model_name", lambda: "model-a"
    )
    fastembed_backend._failed_at = time.monotonic()
    fastembed_backend._failed_model_name = "model-a"
    try:
        assert fastembed_backend.backend_status() == "failed_retrying"
    finally:
        fastembed_backend._reset_backend_for_tests()


def test_fastembed_backend_status_cold_after_ttl_expires() -> None:
    import time

    from anima_server.services.agent import fastembed_backend

    fastembed_backend._reset_backend_for_tests()
    fastembed_backend._failed_at = time.monotonic() - fastembed_backend._RETRY_TTL_SECONDS - 1
    try:
        assert fastembed_backend.backend_status() == "cold"
    finally:
        fastembed_backend._reset_backend_for_tests()


def test_fastembed_backend_status_not_ready_when_loaded_model_is_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Model A loaded fine, but config now resolves to model B with a latched
    failure — must report the failure, not the stale model A as "ready"."""
    import time

    from anima_server.services.agent import fastembed_backend

    fastembed_backend._reset_backend_for_tests()
    monkeypatch.setattr(
        fastembed_backend, "_resolve_current_model_name", lambda: "model-b"
    )
    fastembed_backend._model = object()
    fastembed_backend._model_name_loaded = "model-a"
    fastembed_backend._failed_at = time.monotonic()
    fastembed_backend._failed_model_name = "model-b"
    try:
        assert fastembed_backend.backend_status() == "failed_retrying"
    finally:
        fastembed_backend._reset_backend_for_tests()


def test_fastembed_backend_status_cold_when_loaded_model_differs_no_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Model A loaded, config now resolves to model B, no active failure —
    a load of the current model hasn't happened yet, so status is "cold"."""
    from anima_server.services.agent import fastembed_backend

    fastembed_backend._reset_backend_for_tests()
    monkeypatch.setattr(
        fastembed_backend, "_resolve_current_model_name", lambda: "model-b"
    )
    fastembed_backend._model = object()
    fastembed_backend._model_name_loaded = "model-a"
    try:
        assert fastembed_backend.backend_status() == "cold"
    finally:
        fastembed_backend._reset_backend_for_tests()


def test_reranker_backend_status_matches_latch_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    from anima_server.config import settings
    from anima_server.services.documents import reranker

    reranker._reset_model_cache_for_tests()
    assert reranker.backend_status() == "cold"

    reranker._model = object()
    reranker._model_name_loaded = settings.retrieval_reranker_model
    assert reranker.backend_status() == "ready"

    reranker._reset_model_cache_for_tests()
    reranker._failed_at = time.monotonic()
    reranker._failed_model_name = settings.retrieval_reranker_model
    assert reranker.backend_status() == "failed_retrying"

    reranker._reset_model_cache_for_tests()


def test_reranker_backend_status_not_ready_when_loaded_model_is_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Model A loaded fine, but settings now name model B with a latched
    failure — must report the failure, not the stale model A as "ready"."""
    import time

    from anima_server.config import settings
    from anima_server.services.documents import reranker

    reranker._reset_model_cache_for_tests()
    monkeypatch.setattr(settings, "retrieval_reranker_model", "model-b")
    reranker._model = object()
    reranker._model_name_loaded = "model-a"
    reranker._failed_at = time.monotonic()
    reranker._failed_model_name = "model-b"
    try:
        assert reranker.backend_status() == "failed_retrying"
    finally:
        reranker._reset_model_cache_for_tests()


def test_reranker_backend_status_cold_when_loaded_model_differs_no_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.config import settings
    from anima_server.services.documents import reranker

    reranker._reset_model_cache_for_tests()
    monkeypatch.setattr(settings, "retrieval_reranker_model", "model-b")
    reranker._model = object()
    reranker._model_name_loaded = "model-a"
    try:
        assert reranker.backend_status() == "cold"
    finally:
        reranker._reset_model_cache_for_tests()


# ---------------------------------------------------------------------------
# embeddings.http_backend_status
# ---------------------------------------------------------------------------


def test_http_backend_status_ready_when_key_configured_and_not_cooling_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.config import settings
    from anima_server.services.agent import embeddings as embeddings_module

    embeddings_module._provider_unavailable_until.clear()
    monkeypatch.setattr(settings, "agent_embedding_api_key", "sk-test-openai-key")
    assert embeddings_module.http_backend_status("openai") == "ready"


def test_http_backend_status_failed_retrying_during_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.config import settings
    from anima_server.services.agent import embeddings as embeddings_module

    monkeypatch.setattr(settings, "agent_embedding_api_key", "sk-test-openai-key")
    monkeypatch.setattr(embeddings_module, "_provider_in_cooldown", lambda key: True)
    assert embeddings_module.http_backend_status("openai") == "failed_retrying"


def test_http_backend_status_failed_retrying_when_openai_has_no_usable_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # P2 regression: generate_embedding() returns None *before* any HTTP
    # request for a key-required provider with no usable key, so no
    # cooldown is ever recorded — http_backend_status must not read that
    # silence as "ready".
    from anima_server.config import settings
    from anima_server.services.agent import embeddings as embeddings_module

    embeddings_module._provider_unavailable_until.clear()
    monkeypatch.setattr(settings, "agent_embedding_api_key", "")
    monkeypatch.setattr(settings, "agent_api_key", "")
    monkeypatch.setattr(settings, "agent_api_keys_json", "{}")
    monkeypatch.setattr(settings, "agent_provider", "ollama")

    assert embeddings_module.http_backend_status("openai") == "failed_retrying"


def test_http_backend_status_failed_retrying_when_doubleword_has_no_usable_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.config import settings
    from anima_server.services.agent import embeddings as embeddings_module

    embeddings_module._provider_unavailable_until.clear()
    monkeypatch.delenv("DOUBLEWORD_API_KEY", raising=False)
    monkeypatch.setattr(settings, "agent_embedding_api_key", "")
    monkeypatch.setattr(settings, "agent_api_key", "")
    monkeypatch.setattr(settings, "agent_api_keys_json", "{}")
    monkeypatch.setattr(settings, "agent_provider", "ollama")

    assert embeddings_module.http_backend_status("doubleword") == "failed_retrying"


@pytest.mark.asyncio
async def test_check_retrieval_capabilities_unhealthy_for_keyless_openai_embedding_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # End-to-end guard for the same regression: a keyless cloud embedding
    # provider must make collect_capabilities/the health check report the
    # retrieval stack unhealthy, not silently "ready".
    from anima_server.config import settings
    from anima_server.services import capabilities as capabilities_module
    from anima_server.services.agent import embeddings as embeddings_module
    from anima_server.services.documents.parsing_pack import ParsingPackStatus
    from anima_server.services.health.checks import check_retrieval_capabilities

    embeddings_module._provider_unavailable_until.clear()
    monkeypatch.setattr(settings, "agent_embedding_api_key", "")
    monkeypatch.setattr(settings, "agent_api_key", "")
    monkeypatch.setattr(settings, "agent_api_keys_json", "{}")
    monkeypatch.setattr(settings, "agent_provider", "ollama")
    monkeypatch.setattr(settings, "agent_embedding_provider", "openai")

    monkeypatch.setattr(
        capabilities_module, "pack_status", lambda: ParsingPackStatus(state="ready")
    )
    monkeypatch.setattr(
        capabilities_module, "_resolve_embedding_model", lambda: "text-embedding-3-small"
    )
    monkeypatch.setattr(capabilities_module, "resolve_embedding_dim", lambda: 1536)
    monkeypatch.setattr(capabilities_module, "reranker_backend_status", lambda: "cold")
    monkeypatch.setattr(capabilities_module, "_llm_configured", lambda: True)

    capabilities = capabilities_module.collect_capabilities()
    assert capabilities["embeddings"]["backend"] == "failed_retrying"

    result = await check_retrieval_capabilities(1, capabilities=capabilities)
    assert result.status == "unhealthy"
    assert "embeddings" in result.message


# ---------------------------------------------------------------------------
# health check registration + behavior
# ---------------------------------------------------------------------------


def test_default_registry_includes_retrieval_capabilities() -> None:
    from anima_server.services.health.registry import get_default_registry

    registry = get_default_registry()
    assert "retrieval_capabilities" in registry._checks


@pytest.mark.asyncio
async def test_check_retrieval_capabilities_healthy() -> None:
    from anima_server.services.health.checks import check_retrieval_capabilities

    result = await check_retrieval_capabilities(
        1,
        capabilities=_fake_capabilities(),
    )
    assert result.status == "healthy"
    assert result.name == "retrieval_capabilities"


@pytest.mark.asyncio
async def test_check_retrieval_capabilities_unhealthy_on_failed_backend() -> None:
    from anima_server.services.health.checks import check_retrieval_capabilities

    fake = _fake_capabilities(
        embeddings={
            "provider": "fastembed",
            "model": "BAAI/bge-small-en-v1.5",
            "dim": 384,
            "backend": "failed_retrying",
        }
    )
    result = await check_retrieval_capabilities(1, capabilities=fake)
    assert result.status == "unhealthy"
    assert "embeddings" in result.message


@pytest.mark.asyncio
async def test_check_retrieval_capabilities_unhealthy_on_pack_error() -> None:
    from anima_server.services.health.checks import check_retrieval_capabilities

    fake = _fake_capabilities(
        parsingPack={"state": "error", "progress": None, "error": "boom"}
    )
    result = await check_retrieval_capabilities(1, capabilities=fake)
    assert result.status == "unhealthy"
    assert "parsing pack" in result.message
