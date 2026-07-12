from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from anima_server.config import settings
from anima_server.models.runtime import RuntimeDocumentChunk
from anima_server.services.documents import reranker as reranker_module
from anima_server.services.documents.contextual import (
    CONTEXT_BLURB_METADATA_KEY,
    chunk_index_text,
    generate_document_chunk_blurbs,
)
from anima_server.services.documents.indexing import embed_document_chunks
from anima_server.services.documents.models import (
    DocumentRegistration,
    ExtractedDocumentChunk,
)
from anima_server.services.documents.rag import _lexical_document_chunk_ranking
from anima_server.services.documents.reranker import rerank_chunk_ids
from anima_server.services.documents.store import (
    register_document,
    replace_document_chunks,
    set_document_status,
)

pytest_plugins = ("conftest_runtime",)

USER_ID = 1


class _ScriptedClient:
    def __init__(self, *payloads: str) -> None:
        self._payloads = list(payloads)
        self.prompts: list[str] = []

    async def ainvoke(self, messages: Any) -> Any:
        self.prompts.append("\n".join(str(m.content) for m in messages))
        return SimpleNamespace(content=self._payloads.pop(0))


def _document_with_chunks(runtime_db, texts: list[str], *, indexed: bool = True):
    document = register_document(
        runtime_db,
        DocumentRegistration(
            user_id=USER_ID,
            filename="manual.pdf",
            mime_type="application/pdf",
            storage_path=f".anima/documents/{USER_ID}/manual.pdf",
            sha256="e" * 64,
            size_bytes=512,
        ),
    )
    chunks = replace_document_chunks(
        runtime_db,
        document_id=document.id,
        chunks=[
            ExtractedDocumentChunk(
                chunk_index=index,
                content_text=text,
                section_title=f"Section {index}",
            )
            for index, text in enumerate(texts)
        ],
    )
    if indexed:
        set_document_status(
            runtime_db, document_id=document.id, status="indexed", indexed=True
        )
    return document, chunks


# ── Contextual blurbs ────────────────────────────────────────────────


def test_blurb_generation_is_off_by_default(runtime_db) -> None:
    assert settings.contextual_chunks == "off"
    document, chunks = _document_with_chunks(runtime_db, ["alpha body"])

    written = generate_document_chunk_blurbs(
        runtime_db, user_id=USER_ID, document_id=document.id
    )

    assert written == 0
    refreshed = runtime_db.get(RuntimeDocumentChunk, chunks[0].id)
    assert not (refreshed.metadata_json or {}).get(CONTEXT_BLURB_METADATA_KEY)


def test_blurb_generation_stores_metadata_and_skips_existing(
    runtime_db, monkeypatch: Any
) -> None:
    monkeypatch.setattr(settings, "contextual_chunks", "on")
    document, chunks = _document_with_chunks(
        runtime_db, ["relay housing details", "coil resistance details"]
    )
    client = _ScriptedClient(
        "Chunk from Section 0 of manual.pdf covering relay housing checks.",
        "Chunk from Section 1 of manual.pdf covering coil resistance limits.",
    )

    written = generate_document_chunk_blurbs(
        runtime_db, user_id=USER_ID, document_id=document.id, llm_client=client
    )

    assert written == 2
    blurbs = [
        (runtime_db.get(RuntimeDocumentChunk, chunk.id).metadata_json or {}).get(
            CONTEXT_BLURB_METADATA_KEY
        )
        for chunk in chunks
    ]
    assert all(isinstance(blurb, str) and "manual.pdf" in blurb for blurb in blurbs)
    assert "Section 0" in client.prompts[0]

    # Re-running does not regenerate existing blurbs.
    rerun = generate_document_chunk_blurbs(
        runtime_db,
        user_id=USER_ID,
        document_id=document.id,
        llm_client=_ScriptedClient(),
    )
    assert rerun == 0


def test_blurb_generation_respects_chunk_budget(runtime_db, monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "contextual_chunks", "on")
    monkeypatch.setattr(settings, "contextual_chunks_max_chunks", 1)
    document, _chunks = _document_with_chunks(runtime_db, ["one", "two"])

    written = generate_document_chunk_blurbs(
        runtime_db,
        user_id=USER_ID,
        document_id=document.id,
        llm_client=_ScriptedClient("never used"),
    )

    assert written == 0


def test_blurb_generation_survives_model_failure(runtime_db, monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "contextual_chunks", "on")
    document, chunks = _document_with_chunks(runtime_db, ["alpha", "beta"])

    class _Down:
        async def ainvoke(self, messages: Any) -> Any:
            raise RuntimeError("model down")

    written = generate_document_chunk_blurbs(
        runtime_db, user_id=USER_ID, document_id=document.id, llm_client=_Down()
    )

    assert written == 0
    refreshed = runtime_db.get(RuntimeDocumentChunk, chunks[0].id)
    assert not (refreshed.metadata_json or {}).get(CONTEXT_BLURB_METADATA_KEY)


def test_blurbs_prefix_embedding_text_but_not_stored_content(
    runtime_db, monkeypatch: Any
) -> None:
    monkeypatch.setattr(settings, "contextual_chunks", "on")
    document, chunks = _document_with_chunks(runtime_db, ["relay housing body"])
    chunk = chunks[0]
    chunk.metadata_json = {CONTEXT_BLURB_METADATA_KEY: "Context line about relays."}
    runtime_db.add(chunk)
    runtime_db.flush()

    embedded_texts: list[str] = []
    stored: list[dict[str, Any]] = []

    def fake_upsert(
        self: Any, user_id: int, *, source_type, source_id, content, embedding, **kw
    ) -> None:
        stored.append({"content": content})

    from anima_server.services.agent import pgvec_store as pgvec_module

    monkeypatch.setattr(pgvec_module.PgVecStore, "upsert_source", fake_upsert)

    def embedding_fn(text: str):
        embedded_texts.append(text)
        return [1.0] + [0.0] * 767

    embed_document_chunks(
        runtime_db,
        user_id=USER_ID,
        document_id=document.id,
        embedding_fn=embedding_fn,
    )

    assert embedded_texts == [
        "Context line about relays.\n\nSection 0\n\nrelay housing body"
    ]
    assert stored[0]["content"] == "relay housing body"


def test_chunk_index_text_ignores_blurbs_when_flag_off(runtime_db) -> None:
    _document, chunks = _document_with_chunks(runtime_db, ["body text"])
    chunk = chunks[0]
    chunk.metadata_json = {CONTEXT_BLURB_METADATA_KEY: "Stale blurb."}

    assert settings.contextual_chunks == "off"
    # Section titles always join the index text; the LLM blurb is flag-gated.
    assert chunk_index_text(chunk) == "Section 0\n\nbody text"


def test_section_titles_join_lexical_index_without_blurb_flag(runtime_db) -> None:
    assert settings.contextual_chunks == "off"
    document, chunks = _document_with_chunks(
        runtime_db, ["first body text", "second body text"]
    )
    target = chunks[1]
    target.section_title = "Calibration"
    runtime_db.add(target)
    runtime_db.flush()

    ranking = _lexical_document_chunk_ranking(
        runtime_db,
        user_id=USER_ID,
        document_ids={document.id},
        query="Calibration",
        limit=5,
    )

    # The heading term appears only in section_title, never in the body.
    assert ranking
    assert ranking[0][0] == target.id


def test_blurbs_join_lexical_index_but_not_evidence(
    runtime_db, monkeypatch: Any
) -> None:
    monkeypatch.setattr(settings, "contextual_chunks", "on")
    document, chunks = _document_with_chunks(
        runtime_db, ["first body text", "second body text"]
    )
    target = chunks[1]
    target.metadata_json = {
        CONTEXT_BLURB_METADATA_KEY: "Covers zephyrblade configuration."
    }
    runtime_db.add(target)
    runtime_db.flush()

    ranking = _lexical_document_chunk_ranking(
        runtime_db,
        user_id=USER_ID,
        document_ids={document.id},
        query="zephyrblade",
        limit=5,
    )

    assert ranking
    assert ranking[0][0] == target.id
    # Evidence text stays the raw chunk content.
    refreshed = runtime_db.get(RuntimeDocumentChunk, target.id)
    assert "zephyrblade" not in refreshed.content_text


# ── Reranker ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_reranker_cache():
    reranker_module._reset_model_cache_for_tests()
    yield
    reranker_module._reset_model_cache_for_tests()


def test_rerank_returns_none_when_off() -> None:
    assert settings.retrieval_reranker == "off"
    assert rerank_chunk_ids("query", [(1, "a"), (2, "b")]) is None


def test_rerank_degrades_to_none_when_extra_missing(monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "retrieval_reranker", "local")
    # The sentence-transformers extra is not installed in the test env, so
    # model load fails and rerank degrades gracefully.
    assert rerank_chunk_ids("query", [(1, "a"), (2, "b")]) is None
    # Failure is cached; the second call short-circuits.
    assert rerank_chunk_ids("query", [(1, "a"), (2, "b")]) is None


def test_rerank_orders_by_model_scores(monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "retrieval_reranker", "local")

    class _FakeModel:
        def predict(self, pairs):
            return [0.1 if "beta" in text else 0.9 for _query, text in pairs]

    monkeypatch.setattr(reranker_module, "_load_model", lambda: _FakeModel())

    ranked = rerank_chunk_ids(
        "relay", [(1, "beta text"), (2, "alpha relay text"), (3, "beta more")]
    )

    assert ranked is not None
    assert ranked[0] == 2


def test_search_uses_reranker_order(runtime_db, monkeypatch: Any) -> None:
    from anima_server.models.runtime_embedding import RuntimeEmbedding
    from anima_server.services.agent import pgvec_store as pgvec_module
    from anima_server.services.agent.embedding_integrity import (
        compute_embedding_checksum,
    )
    from anima_server.services.agent.vector_store import VectorSearchResult
    from anima_server.services.documents.rag import search_document_chunks

    document, chunks = _document_with_chunks(
        runtime_db, ["alpha relay body", "beta coil body"]
    )
    for chunk in chunks:
        vector = [1.0] + [0.0] * 767
        runtime_db.add(
            RuntimeEmbedding(
                user_id=USER_ID,
                source_type="document_chunk",
                source_id=chunk.id,
                content_hash=chunk.content_hash,
                embedding_checksum=compute_embedding_checksum(vector),
                embedding=vector,
                content_preview=chunk.content_text[:200],
                category="document",
                importance=3,
            )
        )
    runtime_db.flush()

    search_limits: list[int] = []

    def fake_search_by_vector(
        self: Any,
        user_id: int,
        *,
        query_embedding,
        limit: int = 10,
        category=None,
        source_types=None,
        source_ids=None,
        source_id_query=None,
    ) -> list[VectorSearchResult]:
        search_limits.append(limit)
        return [
            VectorSearchResult(
                item_id=chunks[0].id,
                content="alpha",
                category="document",
                importance=3,
                similarity=0.95,
                source_type="document_chunk",
            ),
            VectorSearchResult(
                item_id=chunks[1].id,
                content="beta",
                category="document",
                importance=3,
                similarity=0.90,
                source_type="document_chunk",
            ),
        ]

    monkeypatch.setattr(
        pgvec_module.PgVecStore, "search_by_vector", fake_search_by_vector
    )

    def fake_embedding(text: str):
        return [1.0] + [0.0] * 767

    monkeypatch.setattr(settings, "retrieval_reranker", "local")

    class _ReverseModel:
        def predict(self, pairs):
            # Score the beta chunk highest, inverting the dense order.
            return [1.0 if "beta" in text else 0.0 for _query, text in pairs]

    monkeypatch.setattr(reranker_module, "_load_model", lambda: _ReverseModel())

    results = search_document_chunks(
        runtime_db,
        USER_ID,
        "quorlix",  # no lexical hits; dense order is the fused order
        document_ids=[document.id],
        limit=1,
        embedding_fn=fake_embedding,
    )

    assert len(results) == 1
    assert "beta" in results[0].content
    # Over-fetch: the rerank stage widens the candidate pool.
    assert search_limits[-1] >= settings.retrieval_rerank_candidates

    # Flag off: dense order wins again, byte-identical to the fused path.
    monkeypatch.setattr(settings, "retrieval_reranker", "off")
    baseline = search_document_chunks(
        runtime_db,
        USER_ID,
        "quorlix",
        document_ids=[document.id],
        limit=1,
        embedding_fn=fake_embedding,
    )
    assert len(baseline) == 1
    assert "alpha" in baseline[0].content
    assert search_limits[-1] == 1


def test_hybrid_fusion_tie_prefers_exact_token_lexical_hit(
    runtime_db, monkeypatch: Any
) -> None:
    from anima_server.models.runtime_embedding import RuntimeEmbedding
    from anima_server.services.agent import pgvec_store as pgvec_module
    from anima_server.services.agent.embedding_integrity import (
        compute_embedding_checksum,
    )
    from anima_server.services.agent.vector_store import VectorSearchResult
    from anima_server.services.documents import rag as rag_module
    from anima_server.services.documents.rag import search_document_chunks

    document, chunks = _document_with_chunks(
        runtime_db, ["unrelated dense favorite", "the E-17 fault code chunk"]
    )
    for chunk in chunks:
        vector = [1.0] + [0.0] * 767
        runtime_db.add(
            RuntimeEmbedding(
                user_id=USER_ID,
                source_type="document_chunk",
                source_id=chunk.id,
                content_hash=chunk.content_hash,
                embedding_checksum=compute_embedding_checksum(vector),
                embedding=vector,
                content_preview=chunk.content_text[:200],
                category="document",
                importance=3,
            )
        )
    runtime_db.flush()

    def fake_search_by_vector(self: Any, user_id: int, **kwargs: Any):
        # Dense rank 1 is the unrelated chunk; the exact-token chunk is
        # invisible to the dense arm.
        return [
            VectorSearchResult(
                item_id=chunks[0].id,
                content="unrelated",
                category="document",
                importance=3,
                similarity=0.99,
                source_type="document_chunk",
            )
        ]

    monkeypatch.setattr(
        pgvec_module.PgVecStore, "search_by_vector", fake_search_by_vector
    )
    monkeypatch.setattr(
        rag_module,
        "_lexical_document_chunk_ranking",
        lambda *args, **kwargs: [(chunks[1].id, 9.0)],
    )

    results = search_document_chunks(
        runtime_db,
        USER_ID,
        "E-17",
        document_ids=[document.id],
        limit=1,
        embedding_fn=lambda text: [1.0] + [0.0] * 767,
    )

    # Both arms rank their hit first (an RRF tie); the exact-token lexical
    # hit must win the single slot.
    assert len(results) == 1
    assert "E-17" in results[0].content


def test_search_returns_lexical_hits_when_query_embedding_unavailable(
    runtime_db, monkeypatch: Any
) -> None:
    from anima_server.models.runtime_embedding import RuntimeEmbedding
    from anima_server.services.agent.embedding_integrity import (
        compute_embedding_checksum,
    )
    from anima_server.services.documents.rag import search_document_chunks

    document, chunks = _document_with_chunks(
        runtime_db, ["alpha filler body", "the E-17 fault code body"]
    )
    for chunk in chunks:
        vector = [1.0] + [0.0] * 767
        runtime_db.add(
            RuntimeEmbedding(
                user_id=USER_ID,
                source_type="document_chunk",
                source_id=chunk.id,
                content_hash=chunk.content_hash,
                embedding_checksum=compute_embedding_checksum(vector),
                embedding=vector,
                content_preview=chunk.content_text[:200],
                category="document",
                importance=3,
            )
        )
    runtime_db.flush()

    results = search_document_chunks(
        runtime_db,
        USER_ID,
        "E-17",
        document_ids=[document.id],
        limit=5,
        embedding_fn=lambda text: None,  # embedding provider outage
    )

    assert results
    assert "E-17" in results[0].content


def test_lexical_hits_hydrate_without_embedding_rows_during_outage(
    runtime_db,
) -> None:
    from anima_server.services.documents.rag import search_document_chunks

    # Indexed document whose embedding rows were lost to a vector reset,
    # while the embedding provider is also down: BM25 must still surface
    # and hydrate the exact-token chunk.
    document, _chunks = _document_with_chunks(
        runtime_db, ["alpha filler body", "the E-17 fault code body"]
    )

    results = search_document_chunks(
        runtime_db,
        USER_ID,
        "E-17",
        document_ids=[document.id],
        limit=5,
        embedding_fn=lambda text: None,
    )

    assert results
    assert "E-17" in results[0].content


def test_search_surfaces_full_section_path_from_metadata(runtime_db) -> None:
    from anima_server.services.documents.rag import _result_section_title

    _document, chunks = _document_with_chunks(runtime_db, ["deep body text"])
    chunk = chunks[0]
    long_path = "Deep Heading " + "x" * 300
    chunk.section_title = long_path[:255]  # what the column stores
    chunk.metadata_json = {"section_paths": [long_path]}

    assert _result_section_title(chunk) == long_path
