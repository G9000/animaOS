from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from anima_server.config import settings
from anima_server.models.runtime import (
    RuntimeDocument,
    RuntimeDocumentChunk,
    RuntimeWorkflowCheckpoint,
)
from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.models.runtime_memory import MemoryCandidate
from anima_server.services.agent import pgvec_store as pgvec_module
from anima_server.services.agent.embedding_integrity import compute_embedding_checksum
from anima_server.services.documents import (
    DocumentRegistration,
    ExtractedDocumentChunk,
    list_document_chunks,
    pdf_workflow,
    register_document,
    replace_document_chunks,
)
from anima_server.services.documents.pdf_text import PageText
from anima_server.services.documents.pdf_workflow import (
    PDF_WORKFLOW_STATES,
    PDFIngestionDependencies,
    PDFIngestionRequest,
    approve_pdf_memory_proposals,
    reject_pdf_memory_proposals,
    resume_pdf_ingestion_workflow,
    run_pdf_ingestion_until_wait_or_done,
    start_pdf_ingestion_workflow,
)
from anima_server.services.workflows import (
    append_checkpoint,
    cancel_workflow,
    mark_workflow_completed,
    mark_workflow_failed,
    start_workflow,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

pytest_plugins = ("conftest_runtime",)

_TEST_EMBEDDING_DIM = 768


@dataclass
class _Calls:
    extracted: int = 0
    chunked: int = 0
    embedded: list[str] | None = None
    summarized: int = 0
    proposed: int = 0


def _embedding(*values: float) -> list[float]:
    return [*values, *([0.0] * (_TEST_EMBEDDING_DIM - len(values)))]


def _request(*, user_id: int = 7, sha256: str = "a" * 64) -> PDFIngestionRequest:
    return PDFIngestionRequest(
        user_id=user_id,
        filename="manual.pdf",
        mime_type="application/pdf",
        storage_path=f".anima/documents/{user_id}/manual.pdf",
        sha256=sha256,
        size_bytes=2048,
        thread_id=42,
        metadata_json={"source": "test"},
    )


def _registration(request: PDFIngestionRequest) -> DocumentRegistration:
    return DocumentRegistration(
        user_id=request.user_id,
        filename=request.filename,
        mime_type=request.mime_type,
        storage_path=request.storage_path,
        sha256=request.sha256,
        size_bytes=request.size_bytes,
        thread_id=request.thread_id,
        metadata_json=request.metadata_json,
    )


def _patch_pgvec_upsert(monkeypatch: Any) -> None:
    def fake_upsert_source(
        self: Any,
        user_id: int,
        *,
        source_type: str,
        source_id: int,
        content: str,
        embedding: list[float],
        category: str = "document",
        importance: int = 3,
    ) -> None:
        row = self._db.scalar(
            select(RuntimeEmbedding).where(
                RuntimeEmbedding.user_id == user_id,
                RuntimeEmbedding.source_type == source_type,
                RuntimeEmbedding.source_id == source_id,
            )
        )
        if row is None:
            row = RuntimeEmbedding(
                user_id=user_id,
                source_type=source_type,
                source_id=source_id,
                content_hash=RuntimeEmbedding.compute_content_hash(content),
                embedding_checksum=compute_embedding_checksum(embedding),
                embedding=embedding,
                content_preview=content[:200],
                category=category,
                importance=importance,
            )
            self._db.add(row)
        else:
            row.content_hash = RuntimeEmbedding.compute_content_hash(content)
            row.embedding_checksum = compute_embedding_checksum(embedding)
            row.embedding = embedding
            row.content_preview = content[:200]
            row.category = category
            row.importance = importance
        self._db.flush()

    monkeypatch.setattr(pgvec_module.PgVecStore, "upsert_source", fake_upsert_source)


def _dependencies(
    calls: _Calls,
    *,
    fail_extract: bool = False,
    fail_chunk: bool = False,
    fail_embed: bool = False,
    fail_summarize: bool = False,
    fail_propose: bool = False,
    embedding_override: Any | None = None,
) -> PDFIngestionDependencies:
    calls.embedded = []
    embedding_fn_override = embedding_override

    def extract_text(path: str) -> list[PageText]:
        if fail_extract:
            raise AssertionError("extract_text should not run")
        calls.extracted += 1
        filename = Path(path).name
        return [
            PageText(page_number=1, text=f"{filename} alpha"),
            PageText(page_number=2, text="beta"),
        ]

    def chunk_text(pages: list[PageText]) -> list[ExtractedDocumentChunk]:
        if fail_chunk:
            raise AssertionError("chunk_text should not run")
        calls.chunked += 1
        assert isinstance(pages, list)
        return [
            ExtractedDocumentChunk(
                chunk_index=0,
                content_text=pages[0].text,
                page_start=1,
                page_end=1,
            ),
            ExtractedDocumentChunk(
                chunk_index=1,
                content_text=pages[1].text,
                page_start=2,
                page_end=2,
            ),
        ]

    def embed_text(text: str) -> list[float] | None:
        if embedding_fn_override is not None:
            result = embedding_fn_override(text)
            assert calls.embedded is not None
            calls.embedded.append(text)
            return result
        if fail_embed:
            raise AssertionError("embedding_fn should not run")
        assert calls.embedded is not None
        calls.embedded.append(text)
        return _embedding(float(len(text)), 1.0)

    def summarize(
        document: RuntimeDocument,
        chunks: list[RuntimeDocumentChunk],
    ) -> dict[str, object]:
        if fail_summarize:
            raise AssertionError("summarize should not run")
        calls.summarized += 1
        return {
            "title": document.filename,
            "chunk_count": len(chunks),
            "summary": "alpha and beta",
        }

    def propose_facts(
        document: RuntimeDocument,
        chunks: list[RuntimeDocumentChunk],
        summary: dict[str, object],
    ) -> list[dict[str, object]]:
        if fail_propose:
            raise AssertionError("propose_facts should not run")
        calls.proposed += 1
        return [
            {
                "content": f"{document.filename}: {summary['summary']}",
                "source": "pdf",
                "chunk_count": len(chunks),
            }
        ]

    return PDFIngestionDependencies(
        extract_text=extract_text,
        chunk_text=chunk_text,
        embedding_fn=embed_text,
        summarize=summarize,
        propose_facts=propose_facts,
    )


def _checkpoint_names(runtime_db: Session, workflow_run_id: int) -> list[str]:
    return list(
        runtime_db.scalars(
            select(RuntimeWorkflowCheckpoint.state_name)
            .where(RuntimeWorkflowCheckpoint.workflow_run_id == workflow_run_id)
            .order_by(RuntimeWorkflowCheckpoint.checkpoint_index)
        ).all()
    )


def _candidate_rows(runtime_db: Session) -> list[MemoryCandidate]:
    return list(
        runtime_db.scalars(
            select(MemoryCandidate).order_by(MemoryCandidate.id)
        ).all()
    )


def _checkpoint(
    runtime_db: Session,
    *,
    workflow_run_id: int,
    state_name: str,
    output_json: dict[str, object] | None = None,
    artifact_refs_json: dict[str, object] | None = None,
) -> None:
    append_checkpoint(
        runtime_db,
        workflow_run_id=workflow_run_id,
        state_name=state_name,
        status="completed",
        idempotency_key=f"pdf:{workflow_run_id}:{state_name}",
        output_json=output_json,
        artifact_refs_json=artifact_refs_json,
    )


def _seed_run_with_document(
    runtime_db: Session,
    request: PDFIngestionRequest,
) -> tuple[int, RuntimeDocument]:
    run = start_workflow(
        runtime_db,
        user_id=request.user_id,
        thread_id=request.thread_id,
        workflow_type="pdf_ingestion",
        input_json=request.to_input_json(),
    )
    document = register_document(
        runtime_db,
        DocumentRegistration(
            user_id=request.user_id,
            filename=request.filename,
            mime_type=request.mime_type,
            storage_path=request.storage_path,
            sha256=request.sha256,
            size_bytes=request.size_bytes,
            thread_id=request.thread_id,
            workflow_run_id=run.id,
            metadata_json=request.metadata_json,
        ),
    )
    _checkpoint(
        runtime_db,
        workflow_run_id=run.id,
        state_name="file_registered",
        output_json={"document_id": document.id},
        artifact_refs_json={"document_id": document.id},
    )
    return run.id, document


def _seed_text_extracted(
    runtime_db: Session,
    *,
    workflow_run_id: int,
    document: RuntimeDocument,
) -> None:
    _checkpoint(
        runtime_db,
        workflow_run_id=workflow_run_id,
        state_name="text_extracted",
        output_json={
            "document_id": document.id,
            "pages": [
                {"page": 1, "text": "seed alpha"},
                {"page": 2, "text": "seed beta"},
            ],
        },
        artifact_refs_json={"document_id": document.id},
    )


def _seed_chunked(
    runtime_db: Session,
    *,
    workflow_run_id: int,
    document: RuntimeDocument,
) -> list[RuntimeDocumentChunk]:
    chunks = replace_document_chunks(
        runtime_db,
        document_id=document.id,
        chunks=[
            ExtractedDocumentChunk(chunk_index=0, content_text="seed alpha"),
            ExtractedDocumentChunk(chunk_index=1, content_text="seed beta"),
        ],
    )
    _checkpoint(
        runtime_db,
        workflow_run_id=workflow_run_id,
        state_name="chunked",
        output_json={"document_id": document.id, "chunk_count": len(chunks)},
        artifact_refs_json={
            "document_id": document.id,
            "chunk_ids": [chunk.id for chunk in chunks],
        },
    )
    return chunks


def _seed_indexed(
    runtime_db: Session,
    monkeypatch: Any,
    *,
    workflow_run_id: int,
    document: RuntimeDocument,
) -> None:
    _patch_pgvec_upsert(monkeypatch)
    from anima_server.services.documents import embed_document_chunks

    embed_document_chunks(
        runtime_db,
        user_id=document.user_id,
        document_id=document.id,
        embedding_fn=lambda text: _embedding(float(len(text))),
    )
    _checkpoint(
        runtime_db,
        workflow_run_id=workflow_run_id,
        state_name="indexed",
        output_json={"document_id": document.id, "embedded_count": 2},
        artifact_refs_json={"document_id": document.id},
    )


def test_pdf_workflow_state_order_is_explicit() -> None:
    assert PDF_WORKFLOW_STATES == (
        "created",
        "file_registered",
        "text_extracted",
        "chunked",
        "embedded",
        "indexed",
        "summarized",
        "facts_proposed",
        "awaiting_approval",
        "memory_saved",
        "memory_rejected",
        "completed",
    )


def test_pdf_workflow_uses_candidate_pipeline_not_direct_memory_items() -> None:
    source = inspect.getsource(pdf_workflow)

    assert "create_memory_candidate" in source
    assert "MemoryItem" not in source
    assert "add_memory_item" not in source
    assert "sync_memory_item_to_retrieval_index" not in source


def test_start_pdf_ingestion_registers_file_then_pauses_for_approval(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    _patch_pgvec_upsert(monkeypatch)
    calls = _Calls()
    request = _request()

    run = start_pdf_ingestion_workflow(runtime_db, request)
    result = run_pdf_ingestion_until_wait_or_done(
        runtime_db,
        workflow_run_id=run.id,
        dependencies=_dependencies(calls),
    )
    run_pdf_ingestion_until_wait_or_done(
        runtime_db,
        workflow_run_id=run.id,
        dependencies=_dependencies(
            _Calls(),
            fail_extract=True,
            fail_chunk=True,
            fail_embed=True,
            fail_summarize=True,
            fail_propose=True,
        ),
    )

    assert result.status == "awaiting_input"
    assert result.current_state == "awaiting_approval"
    assert result.result_json == {
        "document_id": 1,
        "summary": {
            "title": "manual.pdf",
            "chunk_count": 2,
            "summary": "alpha and beta",
        },
        "proposed_facts": [
            {
                "content": "manual.pdf: alpha and beta",
                "source": "pdf",
                "chunk_count": 2,
            }
        ],
    }
    assert _checkpoint_names(runtime_db, run.id) == [
        "file_registered",
        "text_extracted",
        "chunked",
        "embedded",
        "indexed",
        "summarized",
        "facts_proposed",
        "awaiting_approval",
    ]
    assert runtime_db.scalar(select(func.count(RuntimeDocument.id))) == 1
    assert runtime_db.scalar(select(func.count(RuntimeDocumentChunk.id))) == 2
    assert runtime_db.scalar(select(func.count(RuntimeEmbedding.id))) == 2
    assert runtime_db.scalar(select(func.count(MemoryCandidate.id))) == 0
    assert runtime_db.scalar(select(func.count(RuntimeWorkflowCheckpoint.id))) == 8


def test_default_pdf_dependencies_wire_document_services_end_to_end(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    _patch_pgvec_upsert(monkeypatch)
    request = _request()
    extracted_paths: list[str] = []
    embedded_texts: list[str] = []
    page_one_text = " ".join(["alpha"] * 280)
    page_two_text = " ".join(["beta"] * 50)

    def fake_extract_pdf_text(path: str) -> list[PageText]:
        extracted_paths.append(path)
        return [
            PageText(page_number=1, text=page_one_text),
            PageText(page_number=2, text=page_two_text),
        ]

    def fake_embedding(text: str) -> list[float]:
        embedded_texts.append(text)
        return _embedding(float(len(text)), 2.0)

    def summarize(
        document: RuntimeDocument,
        chunks: list[RuntimeDocumentChunk],
    ) -> dict[str, object]:
        return {
            "title": document.filename,
            "chunk_count": len(chunks),
            "summary": "default dependency summary",
        }

    def propose_facts(
        document: RuntimeDocument,
        chunks: list[RuntimeDocumentChunk],
        summary: dict[str, object],
    ) -> list[dict[str, object]]:
        return [
            {
                "content": f"{document.filename}: {summary['summary']}",
                "chunk_count": len(chunks),
            }
        ]

    monkeypatch.setattr(pdf_workflow, "extract_pdf_text", fake_extract_pdf_text)
    dependencies = pdf_workflow.default_pdf_ingestion_dependencies(
        embedding_fn=fake_embedding,
        summarize=summarize,
        propose_facts=propose_facts,
    )

    run = start_pdf_ingestion_workflow(runtime_db, request)
    result = run_pdf_ingestion_until_wait_or_done(
        runtime_db,
        workflow_run_id=run.id,
        dependencies=dependencies,
    )

    chunk_rows = runtime_db.scalars(
        select(RuntimeDocumentChunk).order_by(RuntimeDocumentChunk.chunk_index)
    ).all()
    expected_extract_path = str((settings.data_dir / request.storage_path).resolve())
    assert result.status == "awaiting_input"
    assert extracted_paths == [expected_extract_path]
    assert [chunk.content_text for chunk in chunk_rows] == [
        page_one_text,
        page_two_text,
    ]
    assert [(chunk.page_start, chunk.page_end) for chunk in chunk_rows] == [
        (1, 1),
        (2, 2),
    ]
    assert embedded_texts == [
        page_one_text,
        page_two_text,
    ]
    assert _checkpoint_names(runtime_db, run.id) == [
        "file_registered",
        "text_extracted",
        "chunked",
        "embedded",
        "indexed",
        "summarized",
        "facts_proposed",
        "awaiting_approval",
    ]
    assert runtime_db.scalar(select(func.count(RuntimeDocument.id))) == 1
    assert runtime_db.scalar(select(func.count(RuntimeDocumentChunk.id))) == 2
    assert runtime_db.scalar(select(func.count(RuntimeEmbedding.id))) == 2
    assert runtime_db.scalar(select(func.count(MemoryCandidate.id))) == 0
    assert runtime_db.scalar(select(func.count(RuntimeWorkflowCheckpoint.id))) == 8

    monkeypatch.setattr(
        pdf_workflow,
        "extract_pdf_text",
        lambda _path: pytest.fail("rerun should resume from checkpoints"),
    )
    rerun = run_pdf_ingestion_until_wait_or_done(
        runtime_db,
        workflow_run_id=run.id,
        dependencies=pdf_workflow.default_pdf_ingestion_dependencies(
            embedding_fn=lambda _text: pytest.fail(
                "rerun should not write duplicate embeddings"
            ),
            summarize=lambda _document, _chunks: pytest.fail(
                "rerun should reuse summarized checkpoint"
            ),
            propose_facts=lambda _document, _chunks, _summary: pytest.fail(
                "rerun should reuse fact proposal checkpoint"
            ),
        ),
    )

    assert rerun.status == "awaiting_input"
    assert runtime_db.scalar(select(func.count(RuntimeDocument.id))) == 1
    assert runtime_db.scalar(select(func.count(RuntimeDocumentChunk.id))) == 2
    assert runtime_db.scalar(select(func.count(RuntimeEmbedding.id))) == 2
    assert runtime_db.scalar(select(func.count(RuntimeWorkflowCheckpoint.id))) == 8


def test_partial_embedding_success_does_not_checkpoint_or_continue(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    _patch_pgvec_upsert(monkeypatch)
    request = _request()
    workflow_run_id, document = _seed_run_with_document(runtime_db, request)
    _seed_text_extracted(runtime_db, workflow_run_id=workflow_run_id, document=document)
    _seed_chunked(runtime_db, workflow_run_id=workflow_run_id, document=document)
    calls = _Calls()

    def partial_embedding(text: str) -> list[float] | None:
        if text == "seed alpha":
            return None
        return _embedding(float(len(text)))

    with pytest.raises(ValueError, match=r"PDF document .* was not fully indexed"):
        resume_pdf_ingestion_workflow(
            runtime_db,
            workflow_run_id=workflow_run_id,
            dependencies=_dependencies(
                calls,
                embedding_override=partial_embedding,
                fail_summarize=True,
                fail_propose=True,
            ),
        )

    refreshed = runtime_db.get(RuntimeDocument, document.id)
    assert refreshed is not None
    assert refreshed.status != "indexed"
    assert calls.embedded == ["seed alpha", "seed beta"]
    assert calls.summarized == 0
    assert calls.proposed == 0
    assert _checkpoint_names(runtime_db, workflow_run_id) == [
        "file_registered",
        "text_extracted",
        "chunked",
    ]
    assert runtime_db.scalar(select(func.count(RuntimeEmbedding.id))) == 1


def test_registered_pdf_storage_path_must_stay_under_data_dir(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    _patch_pgvec_upsert(monkeypatch)
    request = _request()
    workflow_run_id, document = _seed_run_with_document(runtime_db, request)
    document.storage_path = "../outside.pdf"
    runtime_db.flush()
    calls = _Calls()

    with pytest.raises(ValueError, match="Invalid document storage path"):
        resume_pdf_ingestion_workflow(
            runtime_db,
            workflow_run_id=workflow_run_id,
            dependencies=_dependencies(
                calls,
                fail_extract=True,
                fail_chunk=True,
                fail_embed=True,
                fail_summarize=True,
                fail_propose=True,
            ),
        )

    assert calls.extracted == 0
    assert _checkpoint_names(runtime_db, workflow_run_id) == ["file_registered"]


def test_existing_embedded_checkpoint_without_indexed_document_does_not_continue(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    _patch_pgvec_upsert(monkeypatch)
    request = _request()
    workflow_run_id, document = _seed_run_with_document(runtime_db, request)
    _seed_text_extracted(runtime_db, workflow_run_id=workflow_run_id, document=document)
    _seed_chunked(runtime_db, workflow_run_id=workflow_run_id, document=document)
    _checkpoint(
        runtime_db,
        workflow_run_id=workflow_run_id,
        state_name="embedded",
        output_json={"document_id": document.id, "embedded_count": 1},
        artifact_refs_json={"document_id": document.id},
    )
    calls = _Calls()

    with pytest.raises(ValueError, match=r"PDF document .* was not fully indexed"):
        resume_pdf_ingestion_workflow(
            runtime_db,
            workflow_run_id=workflow_run_id,
            dependencies=_dependencies(
                calls,
                fail_extract=True,
                fail_chunk=True,
                fail_embed=True,
                fail_summarize=True,
                fail_propose=True,
            ),
        )

    assert calls.embedded == []
    assert calls.summarized == 0
    assert calls.proposed == 0
    assert _checkpoint_names(runtime_db, workflow_run_id) == [
        "file_registered",
        "text_extracted",
        "chunked",
        "embedded",
    ]


@pytest.mark.parametrize("terminal_status", ["completed", "failed", "cancelled"])
def test_resume_terminal_pdf_workflow_returns_without_reopening(
    runtime_db: Session,
    monkeypatch: Any,
    terminal_status: str,
) -> None:
    _patch_pgvec_upsert(monkeypatch)
    request = _request()
    run = start_pdf_ingestion_workflow(runtime_db, request)
    run_pdf_ingestion_until_wait_or_done(
        runtime_db,
        workflow_run_id=run.id,
        dependencies=_dependencies(_Calls()),
    )
    if terminal_status == "completed":
        mark_workflow_completed(
            runtime_db,
            run,
            result_json={"terminal": "completed"},
        )
    elif terminal_status == "failed":
        mark_workflow_failed(
            runtime_db,
            run,
            error_json={"message": "failed intentionally"},
        )
    else:
        cancel_workflow(runtime_db, run)

    previous_status = run.status
    previous_current_state = run.current_state
    previous_result_json = run.result_json
    previous_error_json = run.error_json
    previous_checkpoint_count = runtime_db.scalar(
        select(func.count(RuntimeWorkflowCheckpoint.id))
    )

    resumed = run_pdf_ingestion_until_wait_or_done(
        runtime_db,
        workflow_run_id=run.id,
        dependencies=_dependencies(
            _Calls(),
            fail_extract=True,
            fail_chunk=True,
            fail_embed=True,
            fail_summarize=True,
            fail_propose=True,
        ),
    )

    assert resumed is run
    assert run.status == previous_status
    assert run.current_state == previous_current_state
    assert run.result_json == previous_result_json
    assert run.error_json == previous_error_json
    assert (
        runtime_db.scalar(select(func.count(RuntimeWorkflowCheckpoint.id)))
        == previous_checkpoint_count
    )


def test_unknown_completed_pdf_checkpoint_state_raises_controlled_error(
    runtime_db: Session,
) -> None:
    request = _request()
    workflow_run_id, _document = _seed_run_with_document(runtime_db, request)
    _checkpoint(
        runtime_db,
        workflow_run_id=workflow_run_id,
        state_name="unexpected_state",
    )

    with pytest.raises(ValueError, match="Unknown PDF workflow checkpoint state"):
        resume_pdf_ingestion_workflow(
            runtime_db,
            workflow_run_id=workflow_run_id,
            dependencies=_dependencies(_Calls()),
        )


def test_resume_from_file_registered_continues_after_existing_document(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    _patch_pgvec_upsert(monkeypatch)
    request = _request()
    workflow_run_id, document = _seed_run_with_document(runtime_db, request)
    calls = _Calls()

    result = resume_pdf_ingestion_workflow(
        runtime_db,
        workflow_run_id=workflow_run_id,
        dependencies=_dependencies(calls),
    )

    assert result.status == "awaiting_input"
    assert result.current_state == "awaiting_approval"
    assert calls.extracted == 1
    assert calls.chunked == 1
    assert calls.embedded == ["manual.pdf alpha", "beta"]
    assert calls.summarized == 1
    assert calls.proposed == 1
    assert runtime_db.scalar(select(func.count(RuntimeDocument.id))) == 1
    assert list_document_chunks(runtime_db, document_id=document.id)[0].content_text == (
        "manual.pdf alpha"
    )


def test_resume_from_text_extracted_reuses_staged_pages(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    _patch_pgvec_upsert(monkeypatch)
    request = _request()
    workflow_run_id, document = _seed_run_with_document(runtime_db, request)
    _seed_text_extracted(runtime_db, workflow_run_id=workflow_run_id, document=document)
    calls = _Calls()

    result = resume_pdf_ingestion_workflow(
        runtime_db,
        workflow_run_id=workflow_run_id,
        dependencies=_dependencies(calls, fail_extract=True),
    )

    assert result.status == "awaiting_input"
    assert calls.extracted == 0
    assert calls.chunked == 1
    assert calls.embedded == ["seed alpha", "seed beta"]
    chunk_texts = [
        chunk.content_text
        for chunk in list_document_chunks(runtime_db, document_id=document.id)
    ]
    assert chunk_texts == [
        "seed alpha",
        "seed beta",
    ]


def test_resume_from_chunked_reuses_stored_chunks(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    _patch_pgvec_upsert(monkeypatch)
    request = _request()
    workflow_run_id, document = _seed_run_with_document(runtime_db, request)
    _seed_text_extracted(runtime_db, workflow_run_id=workflow_run_id, document=document)
    _seed_chunked(runtime_db, workflow_run_id=workflow_run_id, document=document)
    calls = _Calls()

    result = resume_pdf_ingestion_workflow(
        runtime_db,
        workflow_run_id=workflow_run_id,
        dependencies=_dependencies(calls, fail_extract=True, fail_chunk=True),
    )

    assert result.status == "awaiting_input"
    assert calls.extracted == 0
    assert calls.chunked == 0
    assert calls.embedded == ["seed alpha", "seed beta"]
    assert calls.summarized == 1
    assert runtime_db.scalar(select(func.count(RuntimeDocumentChunk.id))) == 2


def test_resume_from_indexed_stages_summary_and_fact_proposals_only(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    request = _request()
    workflow_run_id, document = _seed_run_with_document(runtime_db, request)
    _seed_text_extracted(runtime_db, workflow_run_id=workflow_run_id, document=document)
    _seed_chunked(runtime_db, workflow_run_id=workflow_run_id, document=document)
    _seed_indexed(
        runtime_db,
        monkeypatch,
        workflow_run_id=workflow_run_id,
        document=document,
    )
    calls = _Calls()

    result = resume_pdf_ingestion_workflow(
        runtime_db,
        workflow_run_id=workflow_run_id,
        dependencies=_dependencies(
            calls,
            fail_extract=True,
            fail_chunk=True,
            fail_embed=True,
        ),
    )

    assert result.status == "awaiting_input"
    assert result.current_state == "awaiting_approval"
    assert calls.extracted == 0
    assert calls.chunked == 0
    assert calls.embedded == []
    assert calls.summarized == 1
    assert calls.proposed == 1
    assert result.result_json == {
        "document_id": document.id,
        "summary": {
            "title": "manual.pdf",
            "chunk_count": 2,
            "summary": "alpha and beta",
        },
        "proposed_facts": [
            {
                "content": "manual.pdf: alpha and beta",
                "source": "pdf",
                "chunk_count": 2,
            }
        ],
    }
    assert runtime_db.scalar(select(func.count(MemoryCandidate.id))) == 0


def test_resume_from_indexed_rechecks_document_status_before_summary(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    request = _request()
    workflow_run_id, document = _seed_run_with_document(runtime_db, request)
    _seed_text_extracted(runtime_db, workflow_run_id=workflow_run_id, document=document)
    _seed_chunked(runtime_db, workflow_run_id=workflow_run_id, document=document)
    _seed_indexed(
        runtime_db,
        monkeypatch,
        workflow_run_id=workflow_run_id,
        document=document,
    )
    replace_document_chunks(
        runtime_db,
        document_id=document.id,
        chunks=[
            ExtractedDocumentChunk(chunk_index=0, content_text="replacement alpha"),
            ExtractedDocumentChunk(chunk_index=1, content_text="replacement beta"),
        ],
    )
    calls = _Calls()

    with pytest.raises(ValueError, match=r"PDF document .* was not fully indexed"):
        resume_pdf_ingestion_workflow(
            runtime_db,
            workflow_run_id=workflow_run_id,
            dependencies=_dependencies(
                calls,
                fail_extract=True,
                fail_chunk=True,
                fail_embed=True,
                fail_summarize=True,
                fail_propose=True,
            ),
        )

    assert calls.embedded == []
    assert calls.summarized == 0
    assert calls.proposed == 0
    assert _checkpoint_names(runtime_db, workflow_run_id) == [
        "file_registered",
        "text_extracted",
        "chunked",
        "indexed",
    ]


def test_approve_pdf_memory_proposals_creates_candidates_once(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    _patch_pgvec_upsert(monkeypatch)
    request = _request()
    run = start_pdf_ingestion_workflow(runtime_db, request)
    result = run_pdf_ingestion_until_wait_or_done(
        runtime_db,
        workflow_run_id=run.id,
        dependencies=_dependencies(_Calls()),
    )
    assert result.status == "awaiting_input"
    assert runtime_db.scalar(select(func.count(MemoryCandidate.id))) == 0

    approved = approve_pdf_memory_proposals(
        runtime_db,
        workflow_run_id=run.id,
        approved_proposal_indices=[0],
    )
    approved_again = approve_pdf_memory_proposals(
        runtime_db,
        workflow_run_id=run.id,
        approved_proposal_indices=[0],
    )

    candidates = _candidate_rows(runtime_db)
    assert approved.status == "completed"
    assert approved.current_state == "memory_saved"
    assert approved.result_json == {
        "document_id": 1,
        "decision": "approved",
        "selected_count": 1,
        "created_count": 1,
        "candidate_ids": [1],
    }
    assert approved_again.status == "completed"
    assert len(candidates) == 1
    assert candidates[0].user_id == request.user_id
    assert candidates[0].content == "manual.pdf: alpha and beta"
    assert candidates[0].category == "fact"
    assert candidates[0].importance == 3
    assert candidates[0].importance_source == "user_explicit"
    assert candidates[0].source == "tool"
    assert candidates[0].tags_json == ["pdf", "document:1", "workflow:1"]
    assert _checkpoint_names(runtime_db, run.id)[-1] == "memory_saved"
    assert runtime_db.scalar(select(func.count(RuntimeWorkflowCheckpoint.id))) == 9


def test_approve_pdf_memory_proposals_rejects_unstaged_content(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    _patch_pgvec_upsert(monkeypatch)
    run = start_pdf_ingestion_workflow(runtime_db, _request())
    run_pdf_ingestion_until_wait_or_done(
        runtime_db,
        workflow_run_id=run.id,
        dependencies=_dependencies(_Calls()),
    )

    with pytest.raises(ValueError, match="not staged"):
        approve_pdf_memory_proposals(
            runtime_db,
            workflow_run_id=run.id,
            approved_proposals=[
                {
                    "content": "manual.pdf: unrelated caller-provided memory",
                    "category": "fact",
                    "importance": 3,
                }
            ],
        )

    assert runtime_db.scalar(select(func.count(MemoryCandidate.id))) == 0
    assert _checkpoint_names(runtime_db, run.id)[-1] == "awaiting_approval"


def test_approve_pdf_memory_proposals_matches_staged_payload_for_compatibility(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    _patch_pgvec_upsert(monkeypatch)
    run = start_pdf_ingestion_workflow(runtime_db, _request())
    run_pdf_ingestion_until_wait_or_done(
        runtime_db,
        workflow_run_id=run.id,
        dependencies=_dependencies(_Calls()),
    )

    approved = approve_pdf_memory_proposals(
        runtime_db,
        workflow_run_id=run.id,
        approved_proposals=[
            {
                "content": "manual.pdf: alpha and beta",
                "category": "fact",
                "importance": 3,
            }
        ],
    )

    candidates = _candidate_rows(runtime_db)
    assert approved.status == "completed"
    assert approved.result_json == {
        "document_id": 1,
        "decision": "approved",
        "selected_count": 1,
        "created_count": 1,
        "candidate_ids": [1],
    }
    assert len(candidates) == 1
    assert candidates[0].content == "manual.pdf: alpha and beta"


def test_approve_pdf_memory_proposal_indices_preserve_raw_positions(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    _patch_pgvec_upsert(monkeypatch)
    run = start_pdf_ingestion_workflow(runtime_db, _request())
    run_pdf_ingestion_until_wait_or_done(
        runtime_db,
        workflow_run_id=run.id,
        dependencies=_dependencies(_Calls()),
    )
    run.result_json = {
        "document_id": 1,
        "summary": {"summary": "malformed proposal fixture"},
        "proposed_facts": [
            {"bad": "entry"},
            {"content": "valid staged fact"},
        ],
    }
    runtime_db.flush()

    with pytest.raises(ValueError, match="not staged"):
        approve_pdf_memory_proposals(
            runtime_db,
            workflow_run_id=run.id,
            approved_proposal_indices=[0],
        )

    assert runtime_db.scalar(select(func.count(MemoryCandidate.id))) == 0
    assert run.status == "awaiting_input"
    assert run.current_state == "awaiting_approval"

    approved = approve_pdf_memory_proposals(
        runtime_db,
        workflow_run_id=run.id,
        approved_proposal_indices=[1],
    )

    candidates = _candidate_rows(runtime_db)
    assert approved.status == "completed"
    assert approved.result_json == {
        "document_id": 1,
        "decision": "approved",
        "selected_count": 1,
        "created_count": 1,
        "candidate_ids": [1],
    }
    assert len(candidates) == 1
    assert candidates[0].content == "valid staged fact"


def test_reject_pdf_memory_proposals_records_rejection_without_candidates(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    _patch_pgvec_upsert(monkeypatch)
    run = start_pdf_ingestion_workflow(runtime_db, _request())
    run_pdf_ingestion_until_wait_or_done(
        runtime_db,
        workflow_run_id=run.id,
        dependencies=_dependencies(_Calls()),
    )

    rejected = reject_pdf_memory_proposals(
        runtime_db,
        workflow_run_id=run.id,
        reason="not useful",
    )
    rejected_again = reject_pdf_memory_proposals(
        runtime_db,
        workflow_run_id=run.id,
        reason="not useful",
    )

    assert rejected.status == "completed"
    assert rejected.current_state == "memory_rejected"
    assert rejected.result_json == {
        "document_id": 1,
        "decision": "rejected",
        "reason": "not useful",
    }
    assert rejected_again.status == "completed"
    assert runtime_db.scalar(select(func.count(MemoryCandidate.id))) == 0
    assert _checkpoint_names(runtime_db, run.id)[-1] == "memory_rejected"
    assert runtime_db.scalar(select(func.count(RuntimeWorkflowCheckpoint.id))) == 9


def test_approval_requires_awaiting_pdf_workflow(runtime_db: Session) -> None:
    run = start_pdf_ingestion_workflow(runtime_db, _request())

    with pytest.raises(ValueError, match="awaiting PDF memory approval"):
        approve_pdf_memory_proposals(
            runtime_db,
            workflow_run_id=run.id,
            approved_proposals=[],
        )
