from __future__ import annotations

from typing import Any

import pytest
from anima_server.config import settings
from anima_server.services.agent import fastembed_backend
from anima_server.services.documents import reranker as reranker_module


@pytest.fixture(autouse=True)
def _reset_model_caches():
    fastembed_backend._reset_backend_for_tests()
    reranker_module._reset_model_cache_for_tests()
    yield
    fastembed_backend._reset_backend_for_tests()
    reranker_module._reset_model_cache_for_tests()


def test_warmup_loads_embedding_and_reranker_when_reranker_local(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(settings, "retrieval_reranker", "local")
    embedding_calls: list[str] = []
    reranker_calls: list[int] = []

    monkeypatch.setattr(
        fastembed_backend,
        "_load_model",
        lambda model_name: embedding_calls.append(model_name),
    )
    monkeypatch.setattr(
        reranker_module,
        "_load_model",
        lambda: reranker_calls.append(1),
    )

    fastembed_backend.warm_up_retrieval_models()

    assert len(embedding_calls) == 1
    assert reranker_calls == [1]


def test_warmup_skips_reranker_when_off(monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "retrieval_reranker", "off")
    embedding_calls: list[str] = []
    reranker_calls: list[int] = []

    monkeypatch.setattr(
        fastembed_backend,
        "_load_model",
        lambda model_name: embedding_calls.append(model_name),
    )
    monkeypatch.setattr(
        reranker_module,
        "_load_model",
        lambda: reranker_calls.append(1),
    )

    fastembed_backend.warm_up_retrieval_models()

    assert len(embedding_calls) == 1
    assert reranker_calls == []


def test_warmup_uses_resolved_default_embedding_model_name(monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "retrieval_reranker", "off")
    from anima_server.services.agent import embeddings as embeddings_module

    monkeypatch.setattr(
        embeddings_module, "_resolve_embedding_model", lambda: "the-resolved-model"
    )
    embedding_calls: list[str] = []
    monkeypatch.setattr(
        fastembed_backend,
        "_load_model",
        lambda model_name: embedding_calls.append(model_name),
    )

    fastembed_backend.warm_up_retrieval_models()

    assert embedding_calls == ["the-resolved-model"]


def test_warmup_never_raises_when_embedding_loader_fails(monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "retrieval_reranker", "off")

    def _boom(model_name: str) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(fastembed_backend, "_load_model", _boom)

    fastembed_backend.warm_up_retrieval_models()  # must not raise


def test_warmup_never_raises_when_reranker_loader_fails(monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "retrieval_reranker", "local")
    monkeypatch.setattr(fastembed_backend, "_load_model", lambda model_name: None)

    def _boom() -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(reranker_module, "_load_model", _boom)

    fastembed_backend.warm_up_retrieval_models()  # must not raise


def test_warmup_skips_embedding_load_when_provider_not_fastembed(
    monkeypatch: Any,
) -> None:
    from anima_server.services.agent import embeddings as embeddings_module

    # Provider is "ollama", not "fastembed", so embedding model load should be skipped
    monkeypatch.setattr(embeddings_module, "_resolve_embedding_provider", lambda: "ollama")
    monkeypatch.setattr(settings, "retrieval_reranker", "local")

    embedding_calls: list[str] = []
    reranker_calls: list[int] = []

    monkeypatch.setattr(
        fastembed_backend,
        "_load_model",
        lambda model_name: embedding_calls.append(model_name),
    )
    monkeypatch.setattr(
        reranker_module,
        "_load_model",
        lambda: reranker_calls.append(1),
    )

    fastembed_backend.warm_up_retrieval_models()

    # Embedding load should be skipped when provider is not fastembed
    assert embedding_calls == []
    # Reranker should still be loaded
    assert reranker_calls == [1]


def test_warmup_never_downloads_models_via_conftest_stubs(monkeypatch: Any) -> None:
    # No monkeypatching of loaders at all: this exercises the real
    # _load_model path, which hits the conftest raiser stubs for
    # _create_model. Warm-up must swallow that and never raise or attempt
    # a network download.
    monkeypatch.setattr(settings, "retrieval_reranker", "local")
    fastembed_backend.warm_up_retrieval_models()  # must not raise
