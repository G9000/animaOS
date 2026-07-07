from __future__ import annotations

import hashlib
from typing import Any

from anima_server.models.runtime import (
    RuntimeKnowledgeConcept,
    RuntimeKnowledgeConceptSource,
    RuntimeSource,
    RuntimeSourceArtifact,
    RuntimeSourceSpan,
)
from anima_server.services.agent import service as agent_service
from anima_server.services.documents.rag import DocumentRagResult
from anima_server.services.ingestion.retrieval import KnowledgeConceptHit
from sqlalchemy.orm import Session

pytest_plugins = ("conftest_runtime",)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_build_document_context_block_uses_selected_pdf_hits(monkeypatch: Any) -> None:
    calls: list[dict[str, object]] = []

    def fake_search_document_chunks(
        runtime_db: object,
        user_id: int,
        query: str,
        *,
        document_ids: list[int],
        limit: int,
    ) -> list[DocumentRagResult]:
        calls.append(
            {
                "runtime_db": runtime_db,
                "user_id": user_id,
                "query": query,
                "document_ids": document_ids,
                "limit": limit,
            }
        )
        return [
            DocumentRagResult(
                chunk_id=12,
                document_id=4,
                filename="manual.pdf",
                content="Install the relay before enabling checkpoint restart.",
                similarity=0.91,
                page_start=2,
                page_end=3,
                section_title="Install",
            )
        ]

    monkeypatch.setattr(agent_service, "search_document_chunks", fake_search_document_chunks)

    sentinel_db = object()
    block = agent_service._build_document_context_block(
        sentinel_db,
        user_id=7,
        user_message="How do I restart the checkpoint?",
        document_ids=[4],
    )

    assert calls == [
        {
            "runtime_db": sentinel_db,
            "user_id": 7,
            "query": "How do I restart the checkpoint?",
            "document_ids": [4],
            "limit": 5,
        }
    ]
    assert block is not None
    assert block.label == "document_context"
    assert "manual.pdf" in block.value
    assert "pages 2-3" in block.value
    assert "Install the relay" in block.value


def test_build_document_context_block_defaults_document_only_query(
    monkeypatch: Any,
) -> None:
    calls: list[str] = []

    def fake_search_document_chunks(
        runtime_db: object,
        user_id: int,
        query: str,
        *,
        document_ids: list[int],
        limit: int,
    ) -> list[DocumentRagResult]:
        calls.append(query)
        return [
            DocumentRagResult(
                chunk_id=12,
                document_id=4,
                filename="manual.pdf",
                content="The selected document can be summarized from indexed chunks.",
                similarity=0.91,
                page_start=1,
                page_end=1,
                section_title=None,
            )
        ]

    monkeypatch.setattr(agent_service, "search_document_chunks", fake_search_document_chunks)

    block = agent_service._build_document_context_block(
        object(),
        user_id=7,
        user_message="   ",
        document_ids=[4],
    )

    assert calls == ["Summarize the selected document."]
    assert block is not None
    assert "selected document" in block.value


def test_build_document_context_block_uses_compiled_document_knowledge_when_chunks_miss(
    monkeypatch: Any,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_search_document_chunks(
        runtime_db: object,
        user_id: int,
        query: str,
        *,
        document_ids: list[int],
        limit: int,
    ) -> list[DocumentRagResult]:
        return []

    def fake_document_knowledge_hits(
        runtime_db: object,
        *,
        user_id: int,
        document_ids: list[int],
        document_chunk_ids: list[int],
        limit: int,
    ) -> list[KnowledgeConceptHit]:
        calls.append(
            {
                "runtime_db": runtime_db,
                "user_id": user_id,
                "document_ids": document_ids,
                "document_chunk_ids": document_chunk_ids,
                "limit": limit,
            }
        )
        return [
            KnowledgeConceptHit(
                concept_id=22,
                title="Pump Maintenance",
                slug="document-4-pump-maintenance",
                concept_type="topic",
                summary="Inspect the relay and calibration window before restart.",
                score=1.0,
            )
        ]

    monkeypatch.setattr(agent_service, "search_document_chunks", fake_search_document_chunks)
    monkeypatch.setattr(
        agent_service,
        "_document_knowledge_hits",
        fake_document_knowledge_hits,
        raising=False,
    )

    sentinel_db = object()
    block = agent_service._build_document_context_block(
        sentinel_db,
        user_id=7,
        user_message="maintenance schedule?",
        document_ids=[4],
    )

    assert calls == [
        {
            "runtime_db": sentinel_db,
            "user_id": 7,
            "document_ids": [4],
            "document_chunk_ids": [],
            "limit": 8,
        }
    ]
    assert block is not None
    assert "Compiled knowledge from selected PDFs" in block.value
    assert "Pump Maintenance" in block.value
    assert "relay and calibration" in block.value


def test_build_document_context_block_uses_compiled_knowledge_instead_of_raw_chunks(
    monkeypatch: Any,
) -> None:
    def fake_search_document_chunks(
        runtime_db: object,
        user_id: int,
        query: str,
        *,
        document_ids: list[int],
        limit: int,
    ) -> list[DocumentRagResult]:
        return [
            DocumentRagResult(
                chunk_id=12,
                document_id=4,
                filename="manual.pdf",
                content="Raw relay instructions should stay out of the prompt.",
                similarity=0.91,
                page_start=2,
                page_end=2,
                section_title="Install",
            )
        ]

    def fake_document_knowledge_hits(
        runtime_db: object,
        *,
        user_id: int,
        document_ids: list[int],
        document_chunk_ids: list[int],
        limit: int,
    ) -> list[KnowledgeConceptHit]:
        return [
            KnowledgeConceptHit(
                concept_id=22,
                title="Pump Maintenance",
                slug="document-4-pump-maintenance",
                concept_type="topic",
                summary="Use the compiled maintenance concept.",
                score=1.0,
            )
        ]

    monkeypatch.setattr(agent_service, "search_document_chunks", fake_search_document_chunks)
    monkeypatch.setattr(
        agent_service,
        "_document_knowledge_hits",
        fake_document_knowledge_hits,
        raising=False,
    )

    block = agent_service._build_document_context_block(
        object(),
        user_id=7,
        user_message="maintenance schedule?",
        document_ids=[4],
    )

    assert block is not None
    assert "Compiled knowledge from selected PDFs" in block.value
    assert "Use the compiled maintenance concept" in block.value
    assert "Raw evidence excerpts from selected PDFs" not in block.value
    assert "Raw relay instructions" not in block.value


def test_build_document_context_block_uses_query_matched_compiled_pdf_concept(
    monkeypatch: Any,
    runtime_db: Session,
) -> None:
    source = RuntimeSource(
        user_id=7,
        kind="document",
        source_uri="runtime-document://4",
        content_hash=_sha("selected"),
        title="manual.pdf",
        media_type="application/pdf",
        status="indexed",
    )
    runtime_db.add(source)
    runtime_db.flush()
    artifact = RuntimeSourceArtifact(
        user_id=7,
        source_id=source.id,
        artifact_kind="document_text",
        content_text="Broad overview.\n\nCalibrate relay timing.",
        content_hash=_sha("artifact"),
    )
    runtime_db.add(artifact)
    runtime_db.flush()
    broad_span = RuntimeSourceSpan(
        user_id=7,
        source_id=source.id,
        artifact_id=artifact.id,
        span_kind="document_chunk",
        locator_json={"runtime_document_chunk_id": 99, "chunk_index": 0},
        locator_hash=RuntimeSourceSpan.compute_locator_hash(
            {"runtime_document_chunk_id": 99, "chunk_index": 0}
        ),
        content_text="Broad overview.",
        content_hash=_sha("broad"),
    )
    matched_span = RuntimeSourceSpan(
        user_id=7,
        source_id=source.id,
        artifact_id=artifact.id,
        span_kind="document_chunk",
        locator_json={"runtime_document_chunk_id": 12, "chunk_index": 1},
        locator_hash=RuntimeSourceSpan.compute_locator_hash(
            {"runtime_document_chunk_id": 12, "chunk_index": 1}
        ),
        content_text="Calibrate relay timing.",
        content_hash=_sha("matched"),
    )
    runtime_db.add_all([broad_span, matched_span])
    runtime_db.flush()
    broad_summary = RuntimeKnowledgeConcept(
        user_id=7,
        concept_type="source_summary",
        slug="source-4-manual",
        title="manual.pdf",
        description="Broad compiled source summary.",
        body_markdown="# manual.pdf\n\nBroad overview only.",
        frontmatter_json={"type": "source_summary"},
        metadata_json={"compiled_from_source_id": source.id},
        content_hash=_sha("summary"),
        status="active",
    )
    matched_topic = RuntimeKnowledgeConcept(
        user_id=7,
        concept_type="topic",
        slug="source-4-calibration",
        title="Calibration Procedure",
        description=None,
        body_markdown="# Calibration Procedure\n\nCompiled relay timing answer.",
        frontmatter_json={"type": "topic"},
        metadata_json={"compiled_from_source_id": source.id},
        content_hash=_sha("matched topic"),
        status="active",
    )
    broad_topic = RuntimeKnowledgeConcept(
        user_id=7,
        concept_type="topic",
        slug="source-4-overview",
        title="Overview",
        description=None,
        body_markdown="# Overview\n\nGeneral overview only.",
        frontmatter_json={"type": "topic"},
        metadata_json={"compiled_from_source_id": source.id},
        content_hash=_sha("broad topic"),
        status="active",
    )
    runtime_db.add_all([broad_summary, matched_topic, broad_topic])
    runtime_db.flush()
    runtime_db.add_all(
        [
            RuntimeKnowledgeConceptSource(
                user_id=7,
                concept_id=broad_summary.id,
                source_id=source.id,
                span_id=broad_span.id,
                citation_label="S1",
            ),
            RuntimeKnowledgeConceptSource(
                user_id=7,
                concept_id=broad_summary.id,
                source_id=source.id,
                span_id=matched_span.id,
                citation_label="S2",
            ),
            RuntimeKnowledgeConceptSource(
                user_id=7,
                concept_id=matched_topic.id,
                source_id=source.id,
                span_id=matched_span.id,
                citation_label="S1",
            ),
            RuntimeKnowledgeConceptSource(
                user_id=7,
                concept_id=broad_topic.id,
                source_id=source.id,
                span_id=broad_span.id,
                citation_label="S1",
            ),
        ]
    )
    runtime_db.flush()

    def fake_search_document_chunks(
        runtime_db: object,
        user_id: int,
        query: str,
        *,
        document_ids: list[int],
        limit: int,
    ) -> list[DocumentRagResult]:
        return [
            DocumentRagResult(
                chunk_id=12,
                document_id=4,
                filename="manual.pdf",
                content="Raw relay timing answer should stay out of the prompt.",
                similarity=0.91,
                page_start=None,
                page_end=None,
                section_title=None,
            )
        ]

    monkeypatch.setattr(agent_service, "search_document_chunks", fake_search_document_chunks)

    block = agent_service._build_document_context_block(
        runtime_db,
        user_id=7,
        user_message="How should I calibrate the relay?",
        document_ids=[4],
    )

    assert block is not None
    assert "Compiled relay timing answer" in block.value
    assert "Broad overview only" not in block.value
    assert "General overview only" not in block.value
    assert "Raw relay timing answer" not in block.value


def test_document_knowledge_hits_are_scoped_to_selected_document(
    runtime_db: Session,
) -> None:
    selected_source = RuntimeSource(
        user_id=7,
        kind="document",
        source_uri="runtime-document://4",
        content_hash=_sha("selected"),
        title="manual.pdf",
        media_type="application/pdf",
        status="indexed",
    )
    other_source = RuntimeSource(
        user_id=7,
        kind="document",
        source_uri="runtime-document://5",
        content_hash=_sha("other"),
        title="other.pdf",
        media_type="application/pdf",
        status="indexed",
    )
    runtime_db.add_all([selected_source, other_source])
    runtime_db.flush()
    runtime_db.add_all(
        [
            RuntimeKnowledgeConcept(
                user_id=7,
                concept_type="source_summary",
                slug="document-4-manual-pdf",
                title="manual.pdf",
                description="Compiled source summary.",
                body_markdown="# manual.pdf\n\n## Pump Maintenance\nInspect relay timing.",
                frontmatter_json={"type": "source_summary"},
                metadata_json={"compiled_from_source_id": selected_source.id},
                content_hash=_sha("selected summary"),
                status="active",
            ),
            RuntimeKnowledgeConcept(
                user_id=7,
                concept_type="topic",
                slug="document-4-pump-maintenance",
                title="Pump Maintenance",
                description=None,
                body_markdown="# Pump Maintenance\n\nInspect relay timing.",
                frontmatter_json={"type": "topic"},
                metadata_json={"compiled_from_source_id": selected_source.id},
                content_hash=_sha("selected topic"),
                status="active",
            ),
            RuntimeKnowledgeConcept(
                user_id=7,
                concept_type="topic",
                slug="document-5-other",
                title="Other document topic",
                description=None,
                body_markdown="# Other\n\nThis belongs to another document.",
                frontmatter_json={"type": "topic"},
                metadata_json={"compiled_from_source_id": other_source.id},
                content_hash=_sha("other topic"),
                status="active",
            ),
        ]
    )
    runtime_db.flush()

    hits = agent_service._document_knowledge_hits(
        runtime_db,
        user_id=7,
        document_ids=[4],
        limit=8,
    )

    assert [hit.title for hit in hits] == ["manual.pdf", "Pump Maintenance"]
    assert "Inspect relay timing" in hits[0].summary


def test_build_document_context_block_skips_empty_selection() -> None:
    assert (
        agent_service._build_document_context_block(
            object(),
            user_id=7,
            user_message="How do I restart the checkpoint?",
            document_ids=[],
        )
        is None
    )
