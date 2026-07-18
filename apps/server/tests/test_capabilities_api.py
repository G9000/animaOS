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


def test_fastembed_backend_status_ready_when_model_loaded() -> None:
    from anima_server.services.agent import fastembed_backend

    fastembed_backend._reset_backend_for_tests()
    fastembed_backend._model = object()
    try:
        assert fastembed_backend.backend_status() == "ready"
    finally:
        fastembed_backend._reset_backend_for_tests()


def test_fastembed_backend_status_failed_retrying_within_ttl() -> None:
    import time

    from anima_server.services.agent import fastembed_backend

    fastembed_backend._reset_backend_for_tests()
    fastembed_backend._failed_at = time.monotonic()
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


def test_reranker_backend_status_matches_latch_states() -> None:
    import time

    from anima_server.services.documents import reranker

    reranker._reset_model_cache_for_tests()
    assert reranker.backend_status() == "cold"

    reranker._model = object()
    assert reranker.backend_status() == "ready"

    reranker._reset_model_cache_for_tests()
    reranker._failed_at = time.monotonic()
    assert reranker.backend_status() == "failed_retrying"

    reranker._reset_model_cache_for_tests()


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
