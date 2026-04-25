from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest


def test_adapter_exposes_expected_capabilities() -> None:
    from anima_server.services import anima_core_bindings

    status = anima_core_bindings.get_binding_status()
    capabilities = status["capabilities"]
    expected_capabilities = {
        "text_processing",
        "triplet_extraction",
        "adaptive_retrieval",
        "capsule",
        "retrieval_index",
        "search_helpers",
        "stateful_engine",
    }

    assert isinstance(status["available"], bool)
    assert isinstance(status["degraded"], bool)
    assert status["degraded"] is (not status["available"])
    assert isinstance(capabilities, dict)
    assert set(capabilities) == expected_capabilities
    assert all(isinstance(value, bool) for value in capabilities.values())

    if not status["available"]:
        pytest.skip("anima_core is optional and unavailable in this environment")

    assert capabilities["text_processing"] is True
    assert capabilities["capsule"] is True
    assert capabilities["retrieval_index"] is True


def test_missing_binding_is_silent_degraded_mode(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from anima_server.services import anima_core_bindings

    class PartialCore:
        def __getattr__(self, name: str):
            raise AttributeError(name)

    monkeypatch.setattr(anima_core_bindings, "_anima_core", PartialCore())

    with caplog.at_level("WARNING"):
        assert anima_core_bindings.get_binding("newer_binding") is None
        assert anima_core_bindings.has_binding("newer_binding") is False

    assert [
        record.message
        for record in caplog.records
        if record.name == "anima_server.services.anima_core_bindings"
    ] == []


def test_has_binding_degrades_when_getattr_raises(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from anima_server.services import anima_core_bindings

    class BrokenCore:
        def __getattr__(self, _name: str):
            raise ValueError("boom")

    monkeypatch.setattr(anima_core_bindings, "_anima_core", BrokenCore())

    with caplog.at_level("WARNING"):
        assert anima_core_bindings.has_binding("normalize_text") is False
        status = anima_core_bindings.get_binding_status()

    assert status["capabilities"]["text_processing"] is False
    assert any(
        "Failed to resolve anima_core.normalize_text" in record.message
        for record in caplog.records
    )


def test_cosine_similarity_uses_adapter_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    from anima_server.services import anima_core_bindings
    from anima_server.services.agent import embeddings

    monkeypatch.setattr(anima_core_bindings, "rust_cosine_similarity", lambda _a, _b: 0.25)

    assert embeddings.cosine_similarity([1.0], [1.0]) == 0.25


def test_rrf_fusion_uses_adapter_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    from anima_server.services import anima_core_bindings
    from anima_server.services.agent import embeddings

    def _fake_rrf(ranked_lists, k):
        assert k == embeddings._RRF_K
        assert ranked_lists == [[(1, 0.9)], [(2, 0.8)]]
        return [(2, 1.0), (1, 0.5)]

    monkeypatch.setattr(anima_core_bindings, "rust_rrf_fuse", _fake_rrf)

    assert embeddings._reciprocal_rank_fusion(
        [(1, 0.9)],
        [(2, 0.8)],
        semantic_weight=0.5,
        keyword_weight=0.5,
    ) == [(2, 0.5), (1, 0.25)]


def test_heat_scoring_uses_adapter_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    from anima_server.services import anima_core_bindings
    from anima_server.services.agent import heat_scoring

    now = datetime(2026, 4, 25, tzinfo=UTC)
    last_accessed = now - timedelta(seconds=30)

    def _fake_heat(**kwargs):
        assert kwargs["access_count"] == 2
        assert kwargs["interaction_depth"] == 3
        assert kwargs["importance"] == 4.0
        assert kwargs["seconds_since_access"] == 30.0
        assert kwargs["superseded"] is False
        return 12.5

    monkeypatch.setattr(anima_core_bindings, "rust_compute_heat", _fake_heat)

    assert (
        heat_scoring.compute_heat(
            access_count=2,
            interaction_depth=3,
            last_accessed_at=last_accessed,
            importance=4.0,
            now=now,
        )
        == 12.5
    )


def test_text_processing_uses_adapter_bindings(monkeypatch: pytest.MonkeyPatch) -> None:
    from anima_server.services.agent import text_processing

    monkeypatch.setattr(
        text_processing.anima_core_bindings,
        "rust_normalize_text",
        lambda text, limit: (f"normalized:{text}:{limit}", False),
    )
    monkeypatch.setattr(
        text_processing.anima_core_bindings,
        "rust_fix_pdf_spacing",
        lambda text: text.replace("s pa cing", "spacing"),
    )

    assert (
        text_processing.prepare_memory_text("s pa cing", limit=42, apply_pdf_spacing=True)
        == "normalized:spacing:42"
    )


def test_triplets_use_adapter_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    from anima_server.services.agent import graph_triplets

    monkeypatch.setattr(
        graph_triplets.anima_core_bindings,
        "rust_extract_triplets",
        lambda _text: [("User", "person", "works_at", "OpenAI", "organization", 0.9, 0, 10)],
    )

    assert graph_triplets.extract_triplets("I work at OpenAI") == [
        {
            "subject": "User",
            "subject_type": "person",
            "predicate": "works_at",
            "object": "OpenAI",
            "object_type": "organization",
            "confidence": 0.9,
            "char_start": 0,
            "char_end": 10,
        }
    ]
