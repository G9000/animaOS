"""Retrieval quality eval harness (PDP-009).

Non-default: excluded via the ``retrieval_eval`` marker; run explicitly with

    uv run --project apps/server pytest apps/server/tests/test_retrieval_eval.py \
        -m retrieval_eval -s

Ingests a small gold corpus (markdown manual with tables, HTML article,
paged plain-text log standing in for a PDF) through the shipped structured
chunking pipeline and measures recall@5, recall@15, and nDCG@10 per
retrieval configuration. Deterministic by default: a token-hash embedding
stub replaces the real embedder, and dense search runs the same cosine
ranking pgvector would over the scratch DB. Set ``ANIMA_EVAL_EMBEDDINGS=real``
to use the configured embedding model instead (requires a running provider),
which also enables the reranker configuration when the extra is installed.

The harness gates pipeline *changes* on aggregate metrics; per the
no-eval-driven-heuristics rule it must not be used to tune heuristics
against individual failing queries.

Results are written as JSON to ``ANIMA_EVAL_OUTPUT`` (default:
``<data_dir>/retrieval_eval.json``) and printed.
"""

from __future__ import annotations

import json
import math
import os
import re
import zlib
from pathlib import Path
from typing import Any

import pytest
from anima_server.config import settings
from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.services.agent import pgvec_store as pgvec_module
from anima_server.services.agent.vector_store import VectorSearchResult
from anima_server.services.documents import rag as rag_module
from anima_server.services.documents.chunking import chunk_pages_structured
from anima_server.services.documents.indexing import embed_document_chunks
from anima_server.services.documents.models import (
    DocumentRegistration,
    ExtractedDocumentChunk,
)
from anima_server.services.documents.pdf_text import PageText
from anima_server.services.documents.rag import search_document_chunks
from anima_server.services.documents.store import (
    register_document,
    replace_document_chunks,
)
from anima_server.services.ingestion.html_extract import extract_html_article
from anima_server.services.ingestion.structured import (
    chunk_structured_document,
    parse_markdown_structure,
)
from sqlalchemy import delete, select

pytestmark = pytest.mark.retrieval_eval

pytest_plugins = ("conftest_runtime",)

USER_ID = 1
FIXTURES = Path(__file__).parent / "fixtures" / "retrieval_eval"
CHUNK_TARGET_CHARS = 600
EMBED_DIM = 768  # matches the embeddings column dimension
RECALL_LIMIT = 15

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-]*")


def _stub_embedding(text: str) -> list[float]:
    """Deterministic token-hash embedding: stable across runs and platforms."""
    vector = [0.0] * EMBED_DIM
    for token in _TOKEN_RE.findall(text.lower()):
        vector[zlib.crc32(token.encode()) % EMBED_DIM] += 1.0
    norm = math.sqrt(sum(value * value for value in vector))
    if norm:
        vector = [value / norm for value in vector]
    return vector


def _use_real_embeddings() -> bool:
    return os.environ.get("ANIMA_EVAL_EMBEDDINGS", "").lower() == "real"


def _embedding_fn():
    if _use_real_embeddings():
        from anima_server.services.agent.embeddings import generate_embedding

        return generate_embedding
    return _stub_embedding


def _cosine_search_by_vector(
    self: Any,
    user_id: int,
    *,
    query_embedding: list[float],
    limit: int = 10,
    category: str | None = None,
    source_types: list[str] | None = None,
    source_ids: list[int] | None = None,
    source_id_query: Any | None = None,
) -> list[VectorSearchResult]:
    """The cosine ranking pgvector's `<=>` performs, over the scratch DB."""
    stmt = select(RuntimeEmbedding).where(RuntimeEmbedding.user_id == user_id)
    if source_types is not None:
        stmt = stmt.where(RuntimeEmbedding.source_type.in_(source_types))
    if source_ids is not None:
        stmt = stmt.where(RuntimeEmbedding.source_id.in_(source_ids))
    if source_id_query is not None:
        allowed = set(self._db.scalars(source_id_query).all())
        stmt = stmt.where(RuntimeEmbedding.source_id.in_(allowed))
    rows = list(self._db.scalars(stmt).all())

    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=False))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if not norm_a or not norm_b:
            return 0.0
        return dot / (norm_a * norm_b)

    scored = sorted(
        (
            VectorSearchResult(
                item_id=row.source_id,
                content=row.content_preview or "",
                category=row.category,
                importance=row.importance,
                similarity=_cosine(query_embedding, list(row.embedding)),
                source_type=row.source_type,
            )
            for row in rows
        ),
        key=lambda result: result.similarity,
        reverse=True,
    )
    return scored[:limit]


def _markdown_chunks(markdown: str) -> list[ExtractedDocumentChunk]:
    document = parse_markdown_structure(markdown)
    return [
        ExtractedDocumentChunk(
            chunk_index=chunk.chunk_index,
            content_text=chunk.content_text,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            section_title=chunk.section_path or None,
        )
        for chunk in chunk_structured_document(
            document, target_chars=CHUNK_TARGET_CHARS, min_chars=100
        )
    ]


def _corpus_chunks() -> dict[str, list[ExtractedDocumentChunk]]:
    markdown = (FIXTURES / "relay_manual.md").read_text(encoding="utf-8")
    html = (FIXTURES / "garden_article.html").read_text(encoding="utf-8")
    log_text = (FIXTURES / "starship_log.txt").read_text(encoding="utf-8")

    article_markdown = extract_html_article(html).markdown
    # The paged log stands in for a born-digital PDF: paragraphs become
    # pages and flow through the same structured page chunker.
    paragraphs = [part.strip() for part in log_text.split("\n\n") if part.strip()]
    pages = [
        PageText(page_number=index + 1, text=paragraph)
        for index, paragraph in enumerate(paragraphs)
    ]
    return {
        "relay_manual": _markdown_chunks(markdown),
        "garden_article": _markdown_chunks(article_markdown),
        "starship_log": chunk_pages_structured(
            pages, target_chars=CHUNK_TARGET_CHARS
        ),
    }


@pytest.fixture()
def corpus(runtime_db, monkeypatch: Any) -> dict[str, Any]:
    monkeypatch.setattr(
        pgvec_module.PgVecStore, "search_by_vector", _cosine_search_by_vector
    )
    document_ids: list[int] = []
    for index, (doc_key, chunks) in enumerate(_corpus_chunks().items()):
        document = register_document(
            runtime_db,
            DocumentRegistration(
                user_id=USER_ID,
                filename=f"{doc_key}.pdf",
                mime_type="application/pdf",
                storage_path=f".anima/documents/{USER_ID}/{doc_key}.pdf",
                sha256=chr(ord("a") + index) * 64,
                size_bytes=1024,
            ),
        )
        replace_document_chunks(runtime_db, document_id=document.id, chunks=chunks)
        embedded = embed_document_chunks(
            runtime_db,
            user_id=USER_ID,
            document_id=document.id,
            embedding_fn=_embedding_fn(),
        )
        assert embedded == len(chunks)
        document_ids.append(document.id)
    gold = json.loads((FIXTURES / "gold.json").read_text(encoding="utf-8"))
    return {"document_ids": document_ids, "queries": gold["queries"]}


def _first_hit_rank(results, expect: str) -> int | None:
    needle = expect.lower()
    for rank, result in enumerate(results, start=1):
        if needle in result.content.lower():
            return rank
    return None


def _evaluate_config(runtime_db, corpus: dict[str, Any]) -> dict[str, Any]:
    ranks: dict[str, int | None] = {}
    for item in corpus["queries"]:
        results = search_document_chunks(
            runtime_db,
            USER_ID,
            item["query"],
            document_ids=corpus["document_ids"],
            limit=RECALL_LIMIT,
            embedding_fn=_embedding_fn(),
        )
        ranks[item["id"]] = _first_hit_rank(results, item["expect"])
    total = len(ranks)
    recall_5 = sum(1 for rank in ranks.values() if rank is not None and rank <= 5)
    recall_15 = sum(1 for rank in ranks.values() if rank is not None and rank <= 15)
    ndcg_10 = sum(
        1.0 / math.log2(rank + 1)
        for rank in ranks.values()
        if rank is not None and rank <= 10
    )
    return {
        "recall_at_5": round(recall_5 / total, 4),
        "recall_at_15": round(recall_15 / total, 4),
        "ndcg_at_10": round(ndcg_10 / total, 4),
        "misses": sorted(
            item_id for item_id, rank in ranks.items() if rank is None
        ),
    }


def _seed_template_blurbs(runtime_db, corpus: dict[str, Any]) -> None:
    """Deterministic blurb stand-in: section path + document + lead words."""
    from anima_server.models.runtime import RuntimeDocument, RuntimeDocumentChunk
    from anima_server.services.documents.contextual import (
        CONTEXT_BLURB_METADATA_KEY,
    )

    rows = runtime_db.execute(
        select(RuntimeDocumentChunk, RuntimeDocument)
        .join(RuntimeDocument, RuntimeDocumentChunk.document_id == RuntimeDocument.id)
        .where(RuntimeDocument.id.in_(corpus["document_ids"]))
    ).all()
    for chunk, document in rows:
        lead = " ".join(chunk.content_text.split()[:12])
        section = f" section {chunk.section_title}" if chunk.section_title else ""
        blurb = f"This chunk is from{section} of {document.filename} and covers: {lead}"
        chunk.metadata_json = {
            **(chunk.metadata_json or {}),
            CONTEXT_BLURB_METADATA_KEY: blurb,
        }
        runtime_db.add(chunk)
    runtime_db.flush()
    # Re-embed with blurbs prepended.
    runtime_db.execute(
        delete(RuntimeEmbedding).where(
            RuntimeEmbedding.user_id == USER_ID,
            RuntimeEmbedding.source_type == "document_chunk",
        )
    )
    runtime_db.flush()
    for document_id in corpus["document_ids"]:
        embed_document_chunks(
            runtime_db,
            user_id=USER_ID,
            document_id=document_id,
            embedding_fn=_embedding_fn(),
        )


def test_retrieval_eval(runtime_db, corpus: dict[str, Any], monkeypatch: Any) -> None:
    report: dict[str, Any] = {
        "embeddings": "real" if _use_real_embeddings() else "token-hash-stub",
        "corpus_documents": len(corpus["document_ids"]),
        "queries": len(corpus["queries"]),
        "configurations": {},
    }

    # Dense only: the lexical arm degraded away (PDP-002 fallback path).
    original_lexical = rag_module._lexical_document_chunk_ranking
    monkeypatch.setattr(
        rag_module, "_lexical_document_chunk_ranking", lambda *a, **kw: []
    )
    report["configurations"]["dense"] = _evaluate_config(runtime_db, corpus)
    monkeypatch.setattr(
        rag_module, "_lexical_document_chunk_ranking", original_lexical
    )

    # Hybrid: shipped default (dense + BM25 + RRF).
    report["configurations"]["hybrid"] = _evaluate_config(runtime_db, corpus)

    # Hybrid + contextual blurbs (deterministic template blurbs in stub mode).
    monkeypatch.setattr(settings, "contextual_chunks", "on")
    _seed_template_blurbs(runtime_db, corpus)
    report["configurations"]["hybrid_blurbs"] = _evaluate_config(runtime_db, corpus)
    monkeypatch.setattr(settings, "contextual_chunks", "off")

    # Hybrid + reranker: only meaningful with a real cross-encoder.
    if _use_real_embeddings():
        monkeypatch.setattr(settings, "retrieval_reranker", "local")
        report["configurations"]["hybrid_rerank"] = _evaluate_config(
            runtime_db, corpus
        )
        monkeypatch.setattr(settings, "retrieval_reranker", "off")
    else:
        report["configurations"]["hybrid_rerank"] = {
            "skipped": "requires ANIMA_EVAL_EMBEDDINGS=real and the reranker extra"
        }

    output_path = Path(
        os.environ.get(
            "ANIMA_EVAL_OUTPUT",
            str(settings.data_dir / "retrieval_eval.json"),
        )
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"\nretrieval eval report -> {output_path}")
    print(json.dumps(report, indent=2))

    dense = report["configurations"]["dense"]
    hybrid = report["configurations"]["hybrid"]
    # Acceptance: hybrid must not regress below dense on the gold set.
    assert hybrid["recall_at_5"] >= dense["recall_at_5"]
    assert hybrid["recall_at_15"] >= dense["recall_at_15"]
    # The corpus must be genuinely retrievable end to end.
    assert hybrid["recall_at_15"] >= 0.9
