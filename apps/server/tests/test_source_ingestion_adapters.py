from __future__ import annotations

import hashlib

import pytest
from anima_server.models.runtime import (
    RuntimeDocumentChunk,
    RuntimeImageAnnotation,
    RuntimeKnowledgeBundleRun,
    RuntimeKnowledgeConcept,
    RuntimeKnowledgeConceptSource,
    RuntimeSource,
    RuntimeSourceArtifact,
    RuntimeSourceSpan,
)
from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.services.documents.models import DocumentRegistration, ExtractedDocumentChunk
from anima_server.services.documents.store import register_document, replace_document_chunks
from anima_server.services.images.store import register_image_asset
from anima_server.services.ingestion.adapters.base import IngestionAdapter
from anima_server.services.ingestion.artifacts import replace_source_artifacts_and_spans
from anima_server.services.ingestion.models import (
    IngestionAdapterResult,
    SourceArtifactInput,
    SourceIdentity,
    SourceSpanInput,
)
from anima_server.services.ingestion.sources import ingest_with_adapter, register_source
from sqlalchemy import func, select, text

pytest_plugins = ("conftest_runtime",)


@pytest.fixture(autouse=True)
def _enable_foreign_keys_for_adapter_tests(runtime_db) -> None:
    runtime_db.execute(text("PRAGMA foreign_keys = ON"))


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _embedding_for(text: str) -> list[float]:
    return [1.0, *([0.0] * 767)]


def _identity(
    *,
    user_id: int = 1,
    source_uri: str = "file://notes.md",
    content_hash: str | None = None,
    kind: str = "markdown",
) -> SourceIdentity:
    return SourceIdentity(
        user_id=user_id,
        kind=kind,
        source_uri=source_uri,
        content_hash=content_hash or _sha("raw source"),
        title="Notes",
        media_type="text/markdown",
        metadata_json={"origin": "test"},
    )


def test_register_source_is_idempotent_by_full_source_identity(runtime_db) -> None:
    original = register_source(runtime_db, _identity(source_uri="file://a.md"))

    same_identity = register_source(runtime_db, _identity(source_uri="file://a.md"))
    same_hash_different_uri = register_source(
        runtime_db,
        _identity(source_uri="file://renamed.md"),
    )
    same_uri_different_hash = register_source(
        runtime_db,
        _identity(source_uri="file://a.md", content_hash=_sha("changed content")),
    )
    same_uri_hash_different_kind = register_source(
        runtime_db,
        _identity(source_uri="file://a.md", kind="text"),
    )
    other_user = register_source(
        runtime_db,
        _identity(user_id=2, source_uri="file://a.md"),
    )

    assert same_identity.id == original.id
    assert same_hash_different_uri.id != original.id
    assert same_uri_different_hash.id != original.id
    assert same_uri_hash_different_kind.id != original.id
    assert other_user.id != original.id
    assert runtime_db.scalar(select(func.count(RuntimeSource.id))) == 5


def test_adapter_result_stores_normalized_artifacts_and_spans(runtime_db) -> None:
    source = register_source(runtime_db, _identity())
    artifacts = [
        SourceArtifactInput(
            artifact_kind="page_text",
            content_text="page one text",
            content_hash=_sha("page one text"),
            metadata_json={"page": 1},
        ),
        SourceArtifactInput(
            artifact_kind="table",
            content_text="a,b\n1,2",
            content_hash=_sha("a,b\n1,2"),
        ),
    ]
    spans = [
        SourceSpanInput(
            artifact_kind="page_text",
            span_kind="page",
            locator_json={"page_start": 1, "page_end": 1},
            content_text="page one text",
            content_hash=_sha("page one text"),
        ),
        SourceSpanInput(
            artifact_kind="table",
            span_kind="cell",
            locator_json={"row_start": 1, "row_end": 1, "cell": "B1"},
            content_text="2",
            content_hash=_sha("2"),
        ),
    ]

    stored_artifacts, stored_spans = replace_source_artifacts_and_spans(
        runtime_db,
        source=source,
        artifacts=artifacts,
        spans=spans,
    )

    assert [artifact.artifact_kind for artifact in stored_artifacts] == ["page_text", "table"]
    assert [span.span_kind for span in stored_spans] == ["page", "cell"]
    assert stored_spans[0].locator_json == {"page_start": 1, "page_end": 1}
    assert source.status == "indexed"
    assert source.indexed_at is not None


def test_adapter_result_embeds_stored_spans(runtime_db) -> None:
    source = register_source(runtime_db, _identity())

    _stored_artifacts, stored_spans = replace_source_artifacts_and_spans(
        runtime_db,
        source=source,
        artifacts=[
            SourceArtifactInput(
                artifact_kind="plain_text",
                content_text="semantic evidence",
                content_hash=_sha("semantic evidence"),
            )
        ],
        spans=[
            SourceSpanInput(
                artifact_kind="plain_text",
                span_kind="paragraph",
                locator_json={"paragraph_index": 0},
                content_text="semantic evidence",
                content_hash=_sha("semantic evidence"),
            )
        ],
        embedding_fn=_embedding_for,
    )

    embedding = runtime_db.scalar(select(RuntimeEmbedding))
    assert embedding.source_type == "source_span"
    assert embedding.source_id == stored_spans[0].id
    assert embedding.category == "source"
    assert embedding.content_preview == "semantic evidence"


def test_artifact_replacement_can_compile_any_source_to_knowledge(runtime_db) -> None:
    source = register_source(
        runtime_db,
        _identity(
            kind="markdown",
            source_uri="markdown://service-manual.md",
            content_hash=_sha("service manual"),
        ),
    )

    _stored_artifacts, stored_spans = replace_source_artifacts_and_spans(
        runtime_db,
        source=source,
        artifacts=[
            SourceArtifactInput(
                artifact_kind="markdown",
                content_text="# Pump Maintenance\n\nInspect relay timing.",
                content_hash=_sha("# Pump Maintenance\n\nInspect relay timing."),
            )
        ],
        spans=[
            SourceSpanInput(
                artifact_kind="markdown",
                span_kind="heading",
                locator_json={"line_start": 1, "line_end": 1},
                content_text="Pump Maintenance",
                content_hash=_sha("Pump Maintenance"),
                metadata_json={"heading": "Pump Maintenance"},
            ),
            SourceSpanInput(
                artifact_kind="markdown",
                span_kind="paragraph",
                locator_json={"line_start": 3, "line_end": 3},
                content_text="Inspect relay timing.",
                content_hash=_sha("Inspect relay timing."),
                metadata_json={"heading": "Pump Maintenance"},
            ),
        ],
        compile_knowledge=True,
    )

    concepts = list(
        runtime_db.scalars(
            select(RuntimeKnowledgeConcept).order_by(RuntimeKnowledgeConcept.slug)
        ).all()
    )
    citations = list(runtime_db.scalars(select(RuntimeKnowledgeConceptSource)).all())
    compile_run = runtime_db.scalar(
        select(RuntimeKnowledgeBundleRun).where(
            RuntimeKnowledgeBundleRun.run_type == "compile:initial"
        )
    )

    assert {concept.concept_type for concept in concepts} >= {"source_summary", "topic"}
    assert {concept.metadata_json["compiled_from_source_id"] for concept in concepts} == {
        source.id
    }
    assert any("Inspect relay timing" in concept.body_markdown for concept in concepts)
    assert {citation.span_id for citation in citations} == {
        span.id for span in stored_spans
    }
    assert compile_run is not None
    assert compile_run.source_id == source.id
    assert compile_run.status == "completed"


def test_span_inputs_support_page_time_line_row_cell_and_image_locators() -> None:
    locators = [
        {"page_start": 2, "page_end": 3},
        {"time_start_ms": 1000, "time_end_ms": 2500},
        {"line_start": 10, "line_end": 20},
        {"row_start": 5, "row_end": 8},
        {"cell": "C7"},
        {"image_asset_id": 9, "annotation_id": 11, "annotation_kind": "ocr_text"},
    ]

    hashes = {
        SourceSpanInput(
            artifact_kind="artifact",
            span_kind="evidence",
            locator_json=locator,
            content_text="evidence",
            content_hash=_sha("evidence"),
        ).locator_hash
        for locator in locators
    }

    assert len(hashes) == len(locators)


def test_replace_source_artifacts_and_spans_removes_stale_rows(runtime_db) -> None:
    source = register_source(runtime_db, _identity())
    old_artifacts, old_spans = replace_source_artifacts_and_spans(
        runtime_db,
        source=source,
        artifacts=[
            SourceArtifactInput(
                artifact_kind="plain_text",
                content_text="old",
                content_hash=_sha("old"),
            )
        ],
        spans=[
            SourceSpanInput(
                artifact_kind="plain_text",
                span_kind="paragraph",
                locator_json={"paragraph_index": 0},
                content_text="old",
                content_hash=_sha("old"),
            )
        ],
    )

    _new_artifacts, new_spans = replace_source_artifacts_and_spans(
        runtime_db,
        source=source,
        artifacts=[
            SourceArtifactInput(
                artifact_kind="plain_text",
                content_text="new",
                content_hash=_sha("new"),
            )
        ],
        spans=[
            SourceSpanInput(
                artifact_kind="plain_text",
                span_kind="paragraph",
                locator_json={"paragraph_index": 0},
                content_text="new",
                content_hash=_sha("new"),
            )
        ],
    )

    assert old_artifacts[0].content_text == "old"
    assert old_spans[0].content_text == "old"
    assert runtime_db.scalar(select(func.count(RuntimeSourceArtifact.id))) == 1
    assert runtime_db.scalar(select(func.count(RuntimeSourceSpan.id))) == 1
    assert new_spans[0].content_text == "new"


def test_replace_source_artifacts_and_spans_reuses_stable_spans_after_artifact_refresh(
    runtime_db,
) -> None:
    source = register_source(runtime_db, _identity())
    old_artifacts, old_spans = replace_source_artifacts_and_spans(
        runtime_db,
        source=source,
        artifacts=[
            SourceArtifactInput(
                artifact_kind="plain_text",
                content_text="Stable evidence with old wrapper.",
                content_hash=_sha("old wrapper"),
            )
        ],
        spans=[
            SourceSpanInput(
                artifact_kind="plain_text",
                span_kind="paragraph",
                locator_json={"paragraph_index": 0},
                content_text="Stable evidence.",
                content_hash=_sha("Stable evidence."),
            )
        ],
    )
    concept = RuntimeKnowledgeConcept(
        user_id=1,
        concept_type="topic",
        slug="topic-stable-evidence",
        title="Stable evidence",
        body_markdown="Compiled stable evidence.",
        frontmatter_json={"type": "topic"},
        content_hash=_sha("Compiled stable evidence."),
        status="active",
    )
    runtime_db.add(concept)
    runtime_db.flush()
    runtime_db.add(
        RuntimeKnowledgeConceptSource(
            user_id=1,
            concept_id=concept.id,
            source_id=source.id,
            span_id=old_spans[0].id,
            citation_label="S1",
            quote_text="Stable evidence.",
        )
    )
    runtime_db.flush()

    new_artifacts, new_spans = replace_source_artifacts_and_spans(
        runtime_db,
        source=source,
        artifacts=[
            SourceArtifactInput(
                artifact_kind="plain_text",
                content_text="Stable evidence with new annotation.",
                content_hash=_sha("new wrapper"),
            )
        ],
        spans=[
            SourceSpanInput(
                artifact_kind="plain_text",
                span_kind="paragraph",
                locator_json={"paragraph_index": 0},
                content_text="Stable evidence.",
                content_hash=_sha("Stable evidence."),
            )
        ],
    )

    citation = runtime_db.scalar(select(RuntimeKnowledgeConceptSource))
    assert old_artifacts[0].id != new_artifacts[0].id
    assert [span.id for span in new_spans] == [old_spans[0].id]
    assert new_spans[0].artifact_id == new_artifacts[0].id
    assert citation is not None
    assert citation.span_id == old_spans[0].id
    assert runtime_db.scalar(select(func.count(RuntimeSourceArtifact.id))) == 1
    assert runtime_db.scalar(select(func.count(RuntimeSourceSpan.id))) == 1


def test_failed_adapter_records_failed_run_without_half_written_spans(runtime_db) -> None:
    class FailingAdapter(IngestionAdapter):
        name = "failing"

        def extract(self, identity: SourceIdentity) -> IngestionAdapterResult:
            raise RuntimeError("extractor unavailable")

    source, run = ingest_with_adapter(runtime_db, adapter=FailingAdapter(), identity=_identity())

    assert source.status == "failed"
    assert run.status == "failed"
    assert run.run_type == "adapter:failing"
    assert run.error_json == {"message": "extractor unavailable", "type": "RuntimeError"}
    assert runtime_db.scalar(select(func.count(RuntimeSourceArtifact.id))) == 0
    assert runtime_db.scalar(select(func.count(RuntimeSourceSpan.id))) == 0


def test_successful_adapter_writes_completed_run(runtime_db) -> None:
    class TextAdapter(IngestionAdapter):
        name = "text"

        def extract(self, identity: SourceIdentity) -> IngestionAdapterResult:
            return IngestionAdapterResult(
                identity=identity,
                artifacts=[
                    SourceArtifactInput(
                        artifact_kind="plain_text",
                        content_text="hello",
                        content_hash=_sha("hello"),
                    )
                ],
                spans=[
                    SourceSpanInput(
                        artifact_kind="plain_text",
                        span_kind="paragraph",
                        locator_json={"paragraph_index": 0},
                        content_text="hello",
                        content_hash=_sha("hello"),
                    )
                ],
                metadata_json={"adapter": "text"},
            )

    source, run = ingest_with_adapter(runtime_db, adapter=TextAdapter(), identity=_identity())

    assert source.status == "indexed"
    assert run.status == "completed"
    assert run.result_json == {"artifacts": 1, "spans": 1, "adapter": "text"}
    assert runtime_db.scalar(select(RuntimeSourceArtifact)).source_id == source.id
    assert runtime_db.scalar(select(RuntimeSourceSpan)).source_id == source.id
    assert runtime_db.scalar(select(RuntimeKnowledgeBundleRun)).id == run.id


def test_sync_document_source_preserves_chunks_and_writes_page_spans(runtime_db) -> None:
    from anima_server.services.ingestion.adapters.documents import sync_document_source

    document = register_document(
        runtime_db,
        DocumentRegistration(
            user_id=1,
            filename="field-guide.pdf",
            mime_type="application/pdf",
            storage_path=".anima/documents/1/field-guide.pdf",
            sha256=_sha("pdf bytes"),
            size_bytes=2048,
            metadata_json={"origin": "upload"},
        ),
    )
    chunks = replace_document_chunks(
        runtime_db,
        document_id=document.id,
        chunks=[
            ExtractedDocumentChunk(
                chunk_index=0,
                content_text="Page one claim.",
                page_start=1,
                page_end=1,
                section_title="Opening",
                token_count=4,
                metadata_json={"lang": "en"},
            ),
            ExtractedDocumentChunk(
                chunk_index=1,
                content_text="Page two evidence.",
                page_start=2,
                page_end=3,
                section_title="Details",
            ),
        ],
    )

    source, artifacts, spans = sync_document_source(runtime_db, document=document)

    assert source.kind == "document"
    assert source.source_uri == f"runtime-document://{document.id}"
    assert source.content_hash == document.sha256
    assert source.metadata_json["runtime_document_id"] == document.id
    assert artifacts[0].artifact_kind == "document_text"
    assert [span.content_text for span in spans] == [chunk.content_text for chunk in chunks]
    assert spans[0].locator_json == {
        "runtime_document_id": document.id,
        "runtime_document_chunk_id": chunks[0].id,
        "chunk_index": 0,
        "page_start": 1,
        "page_end": 1,
    }
    assert spans[0].metadata_json["section_title"] == "Opening"
    assert runtime_db.scalar(select(func.count(RuntimeDocumentChunk.id))) == 2


def test_sync_image_source_preserves_annotations_and_writes_image_locators(
    runtime_db,
    tmp_path,
    monkeypatch,
) -> None:
    from anima_server.config import settings
    from anima_server.services.ingestion.adapters.images import sync_image_source

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    asset = register_image_asset(
        runtime_db,
        user_id=7,
        data=(
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
            b"\x90wS\xde"
        ),
        mime_type="image/png",
        filename="screen.png",
        metadata_json={"screen": "settings"},
    ).asset
    asset.width = 1440
    asset.height = 900
    annotation = RuntimeImageAnnotation(
        user_id=7,
        image_asset_id=asset.id,
        annotation_kind="ocr_text",
        content_text="Settings pane label",
        content_hash=RuntimeImageAnnotation.compute_content_hash("Settings pane label"),
        source_model="test-ocr",
        status="active",
    )
    runtime_db.add(annotation)
    runtime_db.flush()

    source, artifacts, spans = sync_image_source(runtime_db, asset=asset)

    assert source.kind == "image"
    assert source.source_uri == f"runtime-image://{asset.id}"
    assert source.content_hash == asset.sha256
    assert source.metadata_json["runtime_image_asset_id"] == asset.id
    assert artifacts[0].artifact_kind == "image_annotations"
    assert spans[0].span_kind == "image_annotation"
    assert spans[0].locator_json == {
        "runtime_image_asset_id": asset.id,
        "runtime_image_annotation_id": annotation.id,
        "annotation_kind": "ocr_text",
    }
    assert spans[0].metadata_json["source_model"] == "test-ocr"
    assert runtime_db.scalar(select(func.count(RuntimeImageAnnotation.id))) == 1


def test_markdown_adapter_splits_headings_and_paragraphs(runtime_db) -> None:
    from anima_server.services.ingestion.adapters.text import ingest_markdown_content

    source, artifacts, spans = ingest_markdown_content(
        runtime_db,
        user_id=3,
        content="# Architecture\n\nPortable core details.\n\n## Notes\n\nSpan evidence.",
        filename="../Architecture Notes.md",
        title="Architecture Notes",
        embedding_fn=_embedding_for,
    )

    assert source.kind == "markdown"
    assert source.source_uri == "markdown://Architecture Notes.md"
    assert artifacts[0].artifact_kind == "markdown"
    assert [span.span_kind for span in spans] == [
        "heading",
        "paragraph",
        "heading",
        "paragraph",
    ]
    assert spans[1].metadata_json["heading"] == "Architecture"
    assert spans[2].metadata_json["heading_level"] == 2
    assert runtime_db.scalar(select(func.count(RuntimeKnowledgeConcept.id))) >= 2
    assert (
        runtime_db.scalar(
            select(func.count(RuntimeEmbedding.id)).where(
                RuntimeEmbedding.source_type == "source_span"
            )
        )
        == len(spans)
    )
    assert (
        runtime_db.scalar(
            select(func.count(RuntimeEmbedding.id)).where(
                RuntimeEmbedding.source_type == "knowledge_concept"
            )
        )
        >= 2
    )


def test_text_adapter_rejects_empty_content(runtime_db) -> None:
    from anima_server.services.ingestion.adapters.text import ingest_text_content

    with pytest.raises(ValueError, match="content must not be empty"):
        ingest_text_content(
            runtime_db,
            user_id=3,
            content=" \n\t",
            filename="empty.txt",
        )


def test_web_capture_adapter_preserves_url_metadata(runtime_db) -> None:
    from anima_server.services.ingestion.adapters.web import ingest_web_capture

    source, artifacts, spans = ingest_web_capture(
        runtime_db,
        user_id=3,
        url=" https://example.com/path?q=1 ",
        readable_text="Intro paragraph.\n\nSecond paragraph.",
        title="Example Page",
        canonical_url="https://example.com/path",
    )

    assert source.kind == "web_capture"
    assert source.source_uri == "https://example.com/path?q=1"
    assert source.title == "Example Page"
    assert source.metadata_json["canonical_url"] == "https://example.com/path"
    assert artifacts[0].artifact_kind == "readable_text"
    assert [span.locator_json["paragraph_index"] for span in spans] == [0, 1]
