from __future__ import annotations

import hashlib
from typing import Any

from anima_server.config import settings
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


def _register_owned_document(runtime_db: Session, *, user_id: int = 7) -> int:
    """A real RuntimeDocument row so the ownership gate admits the selection."""
    from anima_server.services.documents.models import DocumentRegistration
    from anima_server.services.documents.store import register_document

    document = register_document(
        runtime_db,
        DocumentRegistration(
            user_id=user_id,
            filename="manual.pdf",
            mime_type="application/pdf",
            storage_path=f".anima/documents/{user_id}/manual.pdf",
            sha256="9" * 64,
            size_bytes=100,
        ),
    )
    return document.id


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
            "limit": settings.document_context_chunk_limit,
        }
    ]
    assert settings.document_context_chunk_limit == 15
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
    monkeypatch.setattr(
        agent_service,
        "_compiled_document_chunk_ids",
        lambda runtime_db, *, user_id, concept_ids, document_chunk_ids: {12},
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


def test_build_document_context_block_keeps_raw_chunks_when_coverage_lookup_fails(
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
                content="Raw relay instructions survive a coverage lookup failure.",
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

    def raising_coverage_lookup(
        runtime_db: object,
        *,
        user_id: int,
        concept_ids: list[int],
        document_chunk_ids: set[int],
    ) -> set[int]:
        raise RuntimeError("coverage lookup unavailable")

    monkeypatch.setattr(agent_service, "search_document_chunks", fake_search_document_chunks)
    monkeypatch.setattr(
        agent_service,
        "_document_knowledge_hits",
        fake_document_knowledge_hits,
        raising=False,
    )
    monkeypatch.setattr(
        agent_service,
        "_compiled_document_chunk_ids",
        raising_coverage_lookup,
        raising=False,
    )

    block = agent_service._build_document_context_block(
        object(),
        user_id=7,
        user_message="maintenance schedule?",
        document_ids=[4],
    )

    assert block is not None
    assert "Raw evidence excerpts from selected PDFs" in block.value
    assert "Raw relay instructions survive a coverage lookup failure." in block.value


def test_build_document_context_block_uses_query_matched_compiled_pdf_concept(
    monkeypatch: Any,
    runtime_db: Session,
) -> None:
    document_id = _register_owned_document(runtime_db)
    source = RuntimeSource(
        user_id=7,
        kind="document",
        source_uri=f"runtime-document://{document_id}",
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
        document_ids=[document_id],
    )

    assert block is not None
    assert "Compiled relay timing answer" in block.value
    assert "Broad overview only" not in block.value
    assert "General overview only" not in block.value
    assert "Raw relay timing answer" not in block.value


def test_build_document_context_block_keeps_raw_excerpts_for_unmatched_chunks(
    monkeypatch: Any,
    runtime_db: Session,
) -> None:
    document_id = _register_owned_document(runtime_db)
    source = RuntimeSource(
        user_id=7,
        kind="document",
        source_uri=f"runtime-document://{document_id}",
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
        content_text="Compiled relay timing.\n\nUncompiled torque setting.",
        content_hash=_sha("artifact"),
    )
    runtime_db.add(artifact)
    runtime_db.flush()
    compiled_span = RuntimeSourceSpan(
        user_id=7,
        source_id=source.id,
        artifact_id=artifact.id,
        span_kind="document_chunk",
        locator_json={"runtime_document_chunk_id": 12, "chunk_index": 0},
        locator_hash=RuntimeSourceSpan.compute_locator_hash(
            {"runtime_document_chunk_id": 12, "chunk_index": 0}
        ),
        content_text="Compiled relay timing.",
        content_hash=_sha("compiled"),
    )
    unmatched_span = RuntimeSourceSpan(
        user_id=7,
        source_id=source.id,
        artifact_id=artifact.id,
        span_kind="document_chunk",
        locator_json={"runtime_document_chunk_id": 13, "chunk_index": 1},
        locator_hash=RuntimeSourceSpan.compute_locator_hash(
            {"runtime_document_chunk_id": 13, "chunk_index": 1}
        ),
        content_text="Uncompiled torque setting.",
        content_hash=_sha("unmatched"),
    )
    runtime_db.add_all([compiled_span, unmatched_span])
    runtime_db.flush()
    topic = RuntimeKnowledgeConcept(
        user_id=7,
        concept_type="topic",
        slug="source-4-relay",
        title="Relay Timing",
        description=None,
        body_markdown="# Relay Timing\n\nCompiled relay timing answer.",
        frontmatter_json={"type": "topic"},
        metadata_json={"compiled_from_source_id": source.id},
        content_hash=_sha("topic"),
        status="active",
    )
    runtime_db.add(topic)
    runtime_db.flush()
    runtime_db.add(
        RuntimeKnowledgeConceptSource(
            user_id=7,
            concept_id=topic.id,
            source_id=source.id,
            span_id=compiled_span.id,
            citation_label="S1",
        )
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
            ),
            DocumentRagResult(
                chunk_id=13,
                document_id=4,
                filename="manual.pdf",
                content="Raw torque answer should stay in the prompt.",
                similarity=0.89,
                page_start=None,
                page_end=None,
                section_title=None,
            ),
        ]

    monkeypatch.setattr(agent_service, "search_document_chunks", fake_search_document_chunks)

    block = agent_service._build_document_context_block(
        runtime_db,
        user_id=7,
        user_message="How should I set the relay and torque?",
        document_ids=[document_id],
    )

    assert block is not None
    assert "Compiled relay timing answer" in block.value
    assert "Raw torque answer should stay in the prompt." in block.value
    assert "Raw relay timing answer should stay out of the prompt." not in block.value


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


def test_build_document_context_block_emits_tool_primer_when_retrieval_is_empty(
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
        return []

    monkeypatch.setattr(
        agent_service, "search_document_chunks", fake_search_document_chunks
    )

    block = agent_service._build_document_context_block(
        object(),
        user_id=7,
        user_message="what does chapter three say?",
        document_ids=[4],
    )

    # Zero hits must still produce the primer: the block is the model's only
    # signal that documents were selected and that the tools exist.
    assert block is not None
    assert "No excerpts were retrieved" in block.value
    assert "read_document_section" in block.value
    assert "get_document_outline" in block.value


def test_build_document_context_block_emits_tool_primer_when_retrieval_raises(
    monkeypatch: Any,
) -> None:
    def failing_search(*args: Any, **kwargs: Any) -> list[DocumentRagResult]:
        raise RuntimeError("embedding provider down")

    monkeypatch.setattr(agent_service, "search_document_chunks", failing_search)

    block = agent_service._build_document_context_block(
        object(),
        user_id=7,
        user_message="what does chapter three say?",
        document_ids=[4],
    )

    assert block is not None
    assert "read_document_section" in block.value


def _document_with_chunks(
    runtime_db: Session,
    *,
    user_id: int = 7,
    filename: str = "manual.pdf",
    sha256: str = "3" * 64,
    chunk_texts: list[str],
) -> int:
    from anima_server.services.documents.models import (
        DocumentRegistration,
        ExtractedDocumentChunk,
    )
    from anima_server.services.documents.store import (
        register_document,
        replace_document_chunks,
    )

    document = register_document(
        runtime_db,
        DocumentRegistration(
            user_id=user_id,
            filename=filename,
            mime_type="application/pdf",
            storage_path=f".anima/documents/{user_id}/{filename}",
            sha256=sha256,
            size_bytes=100,
        ),
    )
    replace_document_chunks(
        runtime_db,
        document_id=document.id,
        chunks=[
            ExtractedDocumentChunk(chunk_index=index, content_text=text)
            for index, text in enumerate(chunk_texts)
        ],
        parse_quality="docling",
    )
    return document.id


def test_small_document_injected_whole(
    monkeypatch: Any, runtime_db: Session
) -> None:
    document_id = _document_with_chunks(
        runtime_db,
        chunk_texts=[
            "First chunk covers the installation steps for the relay.",
            "Second chunk covers the calibration window for the pump.",
        ],
    )

    def fail_if_called(*args: Any, **kwargs: Any) -> list[DocumentRagResult]:
        raise AssertionError("search_document_chunks must not be called for full-doc mode")

    monkeypatch.setattr(agent_service, "search_document_chunks", fail_if_called)

    block = agent_service._build_document_context_block(
        runtime_db,
        user_id=7,
        user_message="Summarize this document.",
        document_ids=[document_id],
    )

    assert block is not None
    assert block.label == "document_context"
    assert "(complete document)" in block.value
    first_index = block.value.index("First chunk covers the installation steps")
    second_index = block.value.index("Second chunk covers the calibration window")
    assert first_index < second_index


def test_oversized_selection_falls_back_to_retrieval(
    monkeypatch: Any, runtime_db: Session
) -> None:
    monkeypatch.setattr(settings, "document_full_context_char_cap", 50)
    document_id = _document_with_chunks(
        runtime_db,
        chunk_texts=[
            "A chunk with enough text to blow past a fifty character cap easily.",
        ],
    )

    calls: list[list[int]] = []

    def fake_search_document_chunks(
        runtime_db: object,
        user_id: int,
        query: str,
        *,
        document_ids: list[int],
        limit: int,
    ) -> list[DocumentRagResult]:
        calls.append(document_ids)
        return [
            DocumentRagResult(
                chunk_id=1,
                document_id=document_id,
                filename="manual.pdf",
                content="A chunk with enough text to blow past a fifty character cap easily.",
                similarity=0.5,
                page_start=None,
                page_end=None,
                section_title=None,
            )
        ]

    monkeypatch.setattr(agent_service, "search_document_chunks", fake_search_document_chunks)

    block = agent_service._build_document_context_block(
        runtime_db,
        user_id=7,
        user_message="Summarize this document.",
        document_ids=[document_id],
    )

    assert calls == [[document_id]]
    assert block is not None
    assert "(complete document)" not in block.value


def test_full_context_off_uses_retrieval(monkeypatch: Any, runtime_db: Session) -> None:
    monkeypatch.setattr(settings, "document_full_context", "off")
    document_id = _document_with_chunks(
        runtime_db,
        chunk_texts=["Tiny chunk well under any budget."],
    )

    calls: list[list[int]] = []

    def fake_search_document_chunks(
        runtime_db: object,
        user_id: int,
        query: str,
        *,
        document_ids: list[int],
        limit: int,
    ) -> list[DocumentRagResult]:
        calls.append(document_ids)
        return []

    monkeypatch.setattr(agent_service, "search_document_chunks", fake_search_document_chunks)

    block = agent_service._build_document_context_block(
        runtime_db,
        user_id=7,
        user_message="Summarize this document.",
        document_ids=[document_id],
    )

    assert calls == [[document_id]]
    assert block is not None
    assert "(complete document)" not in block.value


def test_budget_scales_with_context_window(monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "agent_context_window_tokens", None)
    small_window_budget = agent_service.resolve_document_context_budget_chars()

    monkeypatch.setattr(settings, "agent_context_window_tokens", 200_000)
    large_window_budget = agent_service.resolve_document_context_budget_chars()

    assert small_window_budget != large_window_budget
    assert large_window_budget > small_window_budget


def test_full_document_block_survives_prompt_budget(
    monkeypatch: Any, runtime_db: Session
) -> None:
    """End-to-end: a full-document block above the old static 4000-char
    per-block cap must survive plan_prompt_budget untruncated (the prompt
    assembly path every chat turn runs through)."""
    from anima_server.services.agent.prompt_budget import (
        plan_prompt_budget,
        resolve_budget_config,
    )

    monkeypatch.setattr(settings, "agent_context_window_tokens", None)
    chunk_texts = [
        f"Chunk {index} sentinel text. " + ("Body sentence for padding. " * 55)
        for index in range(8)
    ]
    document_id = _document_with_chunks(runtime_db, chunk_texts=chunk_texts)

    def fail_if_called(*args: Any, **kwargs: Any) -> list[DocumentRagResult]:
        raise AssertionError("search_document_chunks must not be called for full-doc mode")

    monkeypatch.setattr(agent_service, "search_document_chunks", fail_if_called)

    block = agent_service._build_document_context_block(
        runtime_db,
        user_id=7,
        user_message="Summarize this document.",
        document_ids=[document_id],
    )

    assert block is not None
    assert len(block.value) > 4000

    plan = plan_prompt_budget([block], budget=resolve_budget_config())

    assert len(plan.blocks) == 1
    assert plan.blocks[0].value == block.value
    assert "Chunk 7 sentinel text." in plan.blocks[0].value


def test_build_document_context_block_ignores_unowned_document_ids(
    monkeypatch: Any, runtime_db: Session
) -> None:
    from anima_server.services.documents.models import DocumentRegistration
    from anima_server.services.documents.store import register_document

    owned = register_document(
        runtime_db,
        DocumentRegistration(
            user_id=7,
            filename="mine.pdf",
            mime_type="application/pdf",
            storage_path=".anima/documents/7/mine.pdf",
            sha256="1" * 64,
            size_bytes=100,
        ),
    )
    foreign = register_document(
        runtime_db,
        DocumentRegistration(
            user_id=8,
            filename="theirs.pdf",
            mime_type="application/pdf",
            storage_path=".anima/documents/8/theirs.pdf",
            sha256="2" * 64,
            size_bytes=100,
        ),
    )

    def fake_search_document_chunks(*args: Any, **kwargs: Any):
        return []

    monkeypatch.setattr(
        agent_service, "search_document_chunks", fake_search_document_chunks
    )

    # Only invalid/foreign ids: behaves like no selection at all.
    none_valid = agent_service._build_document_context_block(
        runtime_db,
        user_id=7,
        user_message="question",
        document_ids=[foreign.id, 999_999],
    )
    assert none_valid is None

    # Mixed selection keeps only the owned document in the primer.
    mixed = agent_service._build_document_context_block(
        runtime_db,
        user_id=7,
        user_message="question",
        document_ids=[foreign.id, owned.id],
    )
    assert mixed is not None
    assert "mine.pdf" in mixed.value
    assert "theirs.pdf" not in mixed.value
