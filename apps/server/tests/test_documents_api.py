from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from anima_server.config import settings
from anima_server.db.runtime import get_runtime_session_factory
from anima_server.models.runtime import RuntimeThread, RuntimeWorkflowRun
from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.services.agent import pgvec_store as pgvec_module
from anima_server.services.agent.embedding_integrity import compute_embedding_checksum
from anima_server.services.agent.vector_store import VectorSearchResult
from anima_server.services.documents import ExtractedDocumentChunk, pdf_workflow
from anima_server.services.documents.parsing import ExtractionOutcome
from anima_server.services.documents.parsing_pack import ParsingPackStatus
from anima_server.services.documents.pdf_text import PageText
from anima_server.services.documents.reparse import ReparseResult
from anima_server.services.sessions import unlock_session_store
from conftest import managed_test_client
from fastapi.testclient import TestClient
from sqlalchemy import select

_TEST_EMBEDDING_DIM = 768


def _register_user(
    client: TestClient,
    *,
    username: str = "document-api-user",
    name: str = "Document API User",
) -> dict[str, object]:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pw123456", "name": name},
    )
    assert response.status_code == 201
    return response.json()


def _pdf_payload(
    user_id: int,
    *,
    thread_id: int | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "userId": user_id,
        "filename": "manual.pdf",
        "mimeType": "application/pdf",
        "storagePath": f".anima/documents/{user_id}/manual.pdf",
        "sha256": "a" * 64,
        "sizeBytes": 2048,
        "metadata": {"source": "test"},
    }
    if thread_id is not None:
        payload["threadId"] = thread_id
    return payload


def _embedding(*values: float) -> list[float]:
    return [*values, *([0.0] * (_TEST_EMBEDDING_DIM - len(values)))]


def _patch_pdf_edges(
    monkeypatch: Any,
    *,
    proposed_facts: list[dict[str, object]] | None = None,
    summarize_failure: Exception | None = None,
) -> None:
    from anima_server.api.routes import documents as documents_route

    def fake_dependencies() -> pdf_workflow.PDFIngestionDependencies:
        def summarize(document: Any, chunks: list[Any]) -> dict[str, object]:
            if summarize_failure is not None:
                raise summarize_failure
            return {
                "title": document.filename,
                "chunk_count": len(chunks),
                "summary": f"Indexed {len(chunks)} chunks from {document.filename}.",
            }

        def propose_facts(
            document: Any,
            chunks: list[Any],
            summary: dict[str, object],
        ) -> list[dict[str, object]]:
            if proposed_facts is not None:
                return proposed_facts
            return [
                {
                    "content": f"{document.filename}: {summary['summary']}",
                    "chunk_count": len(chunks),
                }
            ]

        return pdf_workflow.PDFIngestionDependencies(
            extract_text=lambda _path: ExtractionOutcome(
                pages=[
                    PageText(page_number=1, text="alpha installation guide"),
                    PageText(page_number=2, text="beta usage notes"),
                ],
                parse_quality="docling",
            ),
            chunk_text=lambda _pages: [
                ExtractedDocumentChunk(
                    chunk_index=0,
                    content_text="alpha installation guide",
                    page_start=1,
                    page_end=1,
                ),
                ExtractedDocumentChunk(
                    chunk_index=1,
                    content_text="beta usage notes",
                    page_start=2,
                    page_end=2,
                ),
            ],
            embedding_fn=lambda _text: _embedding(0.0),
            summarize=summarize,
            propose_facts=propose_facts,
        )

    monkeypatch.setattr(documents_route, "_default_pdf_dependencies", fake_dependencies)
    monkeypatch.setattr(
        pdf_workflow,
        "extract_document_text",
        lambda _path: ExtractionOutcome(
            pages=[
                PageText(page_number=1, text="alpha installation guide"),
                PageText(page_number=2, text="beta usage notes"),
            ],
            parse_quality="docling",
        ),
    )

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


def test_start_pdf_workflow_and_get_status() -> None:
    with managed_test_client("anima-documents-api-") as client:
        reg = _register_user(client)
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": str(reg["unlockToken"])}
        thread_response = client.post("/api/threads", headers=headers)
        assert thread_response.status_code == 201
        thread_id = int(thread_response.json()["threadId"])

        start = client.post(
            "/api/documents/workflows/pdf",
            headers=headers,
            json=_pdf_payload(user_id, thread_id=thread_id),
        )

        assert start.status_code == 201
        created = start.json()
        assert created["workflowId"] == 1
        assert created["status"] == "created"
        assert created["currentState"] == "created"

        status_response = client.get(
            "/api/documents/workflows/1",
            headers=headers,
        )

        assert status_response.status_code == 200
        status = status_response.json()
        assert status["id"] == 1
        assert status["userId"] == user_id
        assert status["threadId"] == thread_id
        assert status["workflowType"] == "pdf_ingestion"
        assert status["input"]["filename"] == "manual.pdf"
        assert status["checkpoints"] == []


def test_upload_pdf_creates_owned_workflow_and_saves_file() -> None:
    with managed_test_client("anima-documents-upload-") as client:
        reg = _register_user(client, username="document-upload-user")
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": str(reg["unlockToken"])}
        content = b"%PDF-1.4\n% test pdf\n"

        response = client.post(
            "/api/documents/pdf",
            headers=headers,
            data={"userId": str(user_id)},
            files={"file": ("Plan Manual.pdf", content, "application/pdf")},
        )

        assert response.status_code == 201
        payload = response.json()
        assert payload["workflowId"] == 1
        assert payload["status"] == "created"
        assert payload["currentState"] == "created"
        assert payload["document"]["filename"] == "Plan Manual.pdf"
        assert payload["document"]["mimeType"] == "application/pdf"
        assert payload["document"]["sizeBytes"] == len(content)

        storage_path = payload["document"]["storagePath"]
        assert storage_path.startswith(f".anima/documents/{user_id}/")
        saved_path = settings.data_dir / storage_path
        assert saved_path.read_bytes() == content

        status_response = client.get(
            "/api/documents/workflows/1",
            headers=headers,
        )
        assert status_response.status_code == 200
        workflow = status_response.json()
        assert workflow["input"]["filename"] == "Plan Manual.pdf"
        assert workflow["input"]["storage_path"] == storage_path


def test_start_pdf_workflow_rejects_missing_thread_id() -> None:
    with managed_test_client("anima-documents-api-") as client:
        reg = _register_user(client)
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": str(reg["unlockToken"])}

        start = client.post(
            "/api/documents/workflows/pdf",
            headers=headers,
            json=_pdf_payload(user_id, thread_id=999),
        )

        assert start.status_code == 404
        assert start.json()["error"] == "Thread not found"


def test_start_pdf_workflow_rejects_other_users_thread_id() -> None:
    with managed_test_client("anima-documents-api-") as client:
        reg = _register_user(client, username="document-thread-owner")
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": str(reg["unlockToken"])}
        runtime_factory = get_runtime_session_factory()
        with runtime_factory() as runtime_db:
            other_thread = RuntimeThread(user_id=user_id + 999, status="active")
            runtime_db.add(other_thread)
            runtime_db.commit()
            thread_id = other_thread.id

        start = client.post(
            "/api/documents/workflows/pdf",
            headers=headers,
            json=_pdf_payload(user_id, thread_id=thread_id),
        )

        assert start.status_code == 404
        assert start.json()["error"] == "Thread not found"


@pytest.mark.parametrize(
    "storage_path",
    [
        "../outside.pdf",
        str(Path.cwd() / "outside.pdf"),
    ],
)
def test_start_pdf_workflow_rejects_storage_path_outside_data_dir(
    storage_path: str,
) -> None:
    with managed_test_client("anima-documents-api-") as client:
        reg = _register_user(client)
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": str(reg["unlockToken"])}
        payload = _pdf_payload(user_id)
        payload["storagePath"] = storage_path

        start = client.post(
            "/api/documents/workflows/pdf",
            headers=headers,
            json=payload,
        )

        assert start.status_code == 400
        assert start.json()["error"] == "Invalid document storage path."
        runtime_factory = get_runtime_session_factory()
        with runtime_factory() as runtime_db:
            assert runtime_db.scalar(select(RuntimeWorkflowRun).limit(1)) is None


@pytest.mark.parametrize(
    "storage_path",
    [
        ".anima/documents/999/manual.pdf",
        "users/999/attachments/chat/manual.pdf",
    ],
)
def test_start_pdf_workflow_rejects_other_users_storage_path(
    storage_path: str,
) -> None:
    with managed_test_client("anima-documents-api-") as client:
        reg = _register_user(client)
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": str(reg["unlockToken"])}
        payload = _pdf_payload(user_id)
        payload["storagePath"] = storage_path

        start = client.post(
            "/api/documents/workflows/pdf",
            headers=headers,
            json=payload,
        )

        assert start.status_code == 400
        assert start.json()["error"] == "Invalid document storage path."
        runtime_factory = get_runtime_session_factory()
        with runtime_factory() as runtime_db:
            assert runtime_db.scalar(select(RuntimeWorkflowRun).limit(1)) is None


def test_resume_pdf_workflow_search_chunks_and_approve_memory(monkeypatch: Any) -> None:
    _patch_pdf_edges(monkeypatch)

    with managed_test_client("anima-documents-api-") as client:
        reg = _register_user(client)
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": str(reg["unlockToken"])}

        start = client.post(
            "/api/documents/workflows/pdf",
            headers=headers,
            json=_pdf_payload(user_id),
        )
        assert start.status_code == 201

        resume = client.post(
            "/api/documents/workflows/1/resume",
            headers=headers,
        )

        assert resume.status_code == 200
        resumed = resume.json()
        assert resumed["workflowId"] == 1
        assert resumed["status"] == "awaiting_input"
        assert resumed["currentState"] == "awaiting_approval"
        assert resumed["workflow"]["result"]["summary"] == {
            "title": "manual.pdf",
            "chunk_count": 2,
            "summary": "Indexed 2 chunks from manual.pdf.",
        }
        assert resumed["workflow"]["result"]["proposed_facts"] == [
            {
                "content": "manual.pdf: Indexed 2 chunks from manual.pdf.",
                "chunk_count": 2,
            }
        ]

        status_response = client.get(
            "/api/documents/workflows/1",
            headers=headers,
        )
        assert status_response.status_code == 200
        checkpoints = status_response.json()["checkpoints"]
        chunked = next(item for item in checkpoints if item["state"] == "chunked")
        chunk_ids = chunked["artifacts"]["chunk_ids"]

        monkeypatch.setattr(
            "anima_server.services.documents.rag.generate_embedding",
            lambda text: _embedding(float(len(text)), 1.0),
        )
        monkeypatch.setattr(
            pgvec_module.PgVecStore,
            "search_by_vector",
            lambda *_args, **_kwargs: [
                VectorSearchResult(
                    item_id=chunk_ids[0],
                    content="alpha preview",
                    category="document",
                    importance=3,
                    similarity=0.91,
                    source_type="document_chunk",
                )
            ],
        )

        search = client.post(
            "/api/documents/search",
            headers=headers,
            json={"userId": user_id, "query": "installation", "limit": 3},
        )

        assert search.status_code == 200
        results = search.json()
        assert results["count"] == 1
        assert results["results"][0]["chunkId"] == chunk_ids[0]
        assert results["results"][0]["documentId"] == 1
        assert results["results"][0]["filename"] == "manual.pdf"
        assert results["results"][0]["similarity"] == 0.91

        approve = client.post(
            "/api/documents/workflows/1/approve-memory",
            headers=headers,
            json={"proposalIndices": [0]},
        )

        assert approve.status_code == 200
        approved = approve.json()
        assert approved["workflowId"] == 1
        assert approved["status"] == "completed"
        assert approved["currentState"] == "memory_saved"
        assert approved["workflow"]["result"] == {
            "document_id": 1,
            "decision": "approved",
            "selected_count": 1,
            "created_count": 1,
            "candidate_ids": [1],
        }


def test_approve_memory_allows_empty_selection_when_no_facts(
    monkeypatch: Any,
) -> None:
    _patch_pdf_edges(monkeypatch, proposed_facts=[])

    with managed_test_client("anima-documents-api-") as client:
        reg = _register_user(client)
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": str(reg["unlockToken"])}

        start = client.post(
            "/api/documents/workflows/pdf",
            headers=headers,
            json=_pdf_payload(user_id),
        )
        assert start.status_code == 201
        resume = client.post(
            "/api/documents/workflows/1/resume",
            headers=headers,
        )
        assert resume.status_code == 200
        assert resume.json()["workflow"]["result"]["proposed_facts"] == []

        approve = client.post(
            "/api/documents/workflows/1/approve-memory",
            headers=headers,
            json={"proposalIndices": []},
        )

        assert approve.status_code == 200
        approved = approve.json()
        assert approved["status"] == "completed"
        assert approved["currentState"] == "memory_saved"
        assert approved["workflow"]["result"] == {
            "document_id": 1,
            "decision": "approved",
            "selected_count": 0,
            "created_count": 0,
            "candidate_ids": [],
        }


def test_resume_preserves_committed_checkpoints_when_later_stage_fails(
    monkeypatch: Any,
) -> None:
    _patch_pdf_edges(monkeypatch, summarize_failure=RuntimeError("summary unavailable"))

    with managed_test_client("anima-documents-api-") as client:
        reg = _register_user(client)
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": str(reg["unlockToken"])}

        start = client.post(
            "/api/documents/workflows/pdf",
            headers=headers,
            json=_pdf_payload(user_id),
        )
        assert start.status_code == 201

        resume = client.post(
            "/api/documents/workflows/1/resume",
            headers=headers,
        )

        assert resume.status_code == 503
        status_response = client.get(
            "/api/documents/workflows/1",
            headers=headers,
        )
        assert status_response.status_code == 200
        status_json = status_response.json()
        assert status_json["status"] == "running"
        assert status_json["currentState"] == "indexed"
        assert [checkpoint["state"] for checkpoint in status_json["checkpoints"]] == [
            "file_registered",
            "text_extracted",
            "chunked",
            "embedded",
            "indexed",
        ]


def test_default_resume_returns_controlled_error_when_pdf_parser_unavailable() -> None:
    with managed_test_client("anima-documents-api-") as client:
        reg = _register_user(client)
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": str(reg["unlockToken"])}

        start = client.post(
            "/api/documents/workflows/pdf",
            headers=headers,
            json=_pdf_payload(user_id),
        )
        assert start.status_code == 201

        resume = client.post(
            "/api/documents/workflows/1/resume",
            headers=headers,
        )

        assert resume.status_code == 503
        assert "PDF ingestion is unavailable" in resume.json()["error"]

        status_response = client.get(
            "/api/documents/workflows/1",
            headers=headers,
        )
        assert status_response.status_code == 200
        status_json = status_response.json()
        assert status_json["status"] == "running"
        assert status_json["currentState"] == "file_registered"
        assert [checkpoint["state"] for checkpoint in status_json["checkpoints"]] == [
            "file_registered"
        ]


@pytest.mark.parametrize("body", [{}, {"proposalIndices": [-1]}])
def test_approve_memory_rejects_invalid_proposal_indices(
    monkeypatch: Any,
    body: dict[str, object],
) -> None:
    _patch_pdf_edges(monkeypatch)

    with managed_test_client("anima-documents-api-") as client:
        reg = _register_user(client)
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": str(reg["unlockToken"])}

        start = client.post(
            "/api/documents/workflows/pdf",
            headers=headers,
            json=_pdf_payload(user_id),
        )
        assert start.status_code == 201
        resume = client.post(
            "/api/documents/workflows/1/resume",
            headers=headers,
        )
        assert resume.status_code == 200

        approve = client.post(
            "/api/documents/workflows/1/approve-memory",
            headers=headers,
            json=body,
        )

        assert approve.status_code == 422
        status_response = client.get(
            "/api/documents/workflows/1",
            headers=headers,
        )
        assert status_response.status_code == 200
        assert status_response.json()["status"] == "awaiting_input"
        assert status_response.json()["currentState"] == "awaiting_approval"


def test_document_workflows_are_hidden_from_other_unlocked_users() -> None:
    with managed_test_client("anima-documents-api-") as client:
        owner = _register_user(client, username="document-owner")
        owner_session = unlock_session_store.resolve(str(owner["unlockToken"]))
        assert owner_session is not None
        other_token = unlock_session_store.create(
            int(owner["id"]) + 999,
            owner_session.deks,
        )
        owner_headers = {"x-anima-unlock": str(owner["unlockToken"])}
        other_headers = {"x-anima-unlock": other_token}

        start = client.post(
            "/api/documents/workflows/pdf",
            headers=owner_headers,
            json=_pdf_payload(int(owner["id"])),
        )
        assert start.status_code == 201

        status_response = client.get(
            "/api/documents/workflows/1",
            headers=other_headers,
        )

        assert status_response.status_code == 404

        resume_response = client.post(
            "/api/documents/workflows/1/resume",
            headers=other_headers,
        )
        assert resume_response.status_code == 404

        approve_response = client.post(
            "/api/documents/workflows/1/approve-memory",
            headers=other_headers,
            json={"proposalIndices": [0]},
        )
        assert approve_response.status_code == 404

        search_response = client.post(
            "/api/documents/search",
            headers=owner_headers,
            json={"userId": int(owner["id"]) + 999, "query": "manual"},
        )
        assert search_response.status_code == 403


def test_get_parsing_pack_status_requires_auth() -> None:
    with managed_test_client("anima-documents-api-") as client:
        response = client.get("/api/documents/parsing-pack")
        assert response.status_code == 401


def test_get_parsing_pack_status_returns_pack_state(monkeypatch: Any) -> None:
    from anima_server.api.routes import documents as documents_route

    monkeypatch.setattr(
        documents_route,
        "pack_status",
        lambda: ParsingPackStatus(state="downloading", progress=0.42),
    )

    with managed_test_client("anima-documents-api-") as client:
        reg = _register_user(client, username="parsing-pack-status-user")
        headers = {"x-anima-unlock": str(reg["unlockToken"])}

        response = client.get("/api/documents/parsing-pack", headers=headers)

        assert response.status_code == 200
        assert response.json() == {
            "state": "downloading",
            "progress": 0.42,
            "error": None,
        }


def test_download_parsing_pack_triggers_ensure_and_returns_state(
    monkeypatch: Any,
) -> None:
    from anima_server.api.routes import documents as documents_route

    calls: list[bool] = []

    def fake_ensure() -> ParsingPackStatus:
        calls.append(True)
        return ParsingPackStatus(state="downloading", progress=0.0)

    monkeypatch.setattr(documents_route, "ensure_parsing_pack", fake_ensure)

    with managed_test_client("anima-documents-api-") as client:
        reg = _register_user(client, username="parsing-pack-download-user")
        headers = {"x-anima-unlock": str(reg["unlockToken"])}

        response = client.post("/api/documents/parsing-pack/download", headers=headers)

        assert response.status_code == 200
        assert response.json() == {"state": "downloading", "progress": 0.0, "error": None}
        assert calls == [True]


def test_download_parsing_pack_requires_auth() -> None:
    with managed_test_client("anima-documents-api-") as client:
        response = client.post("/api/documents/parsing-pack/download")
        assert response.status_code == 401


def test_reparse_document_returns_upgraded_payload(monkeypatch: Any) -> None:
    from anima_server.api.routes import documents as documents_route

    captured: dict[str, object] = {}

    def fake_reparse_document(
        runtime_db: Any,
        *,
        user_id: int,
        document_id: int,
    ) -> ReparseResult:
        captured["user_id"] = user_id
        captured["document_id"] = document_id
        return ReparseResult(status="upgraded", chunk_count=3)

    monkeypatch.setattr(documents_route, "reparse_document", fake_reparse_document)

    with managed_test_client("anima-documents-api-") as client:
        reg = _register_user(client, username="reparse-upgraded-user")
        user_id = int(reg["id"])
        headers = {"x-anima-unlock": str(reg["unlockToken"])}

        response = client.post("/api/documents/55/reparse", headers=headers)

        assert response.status_code == 200
        assert response.json() == {"status": "upgraded", "chunk_count": 3}
        assert captured == {"user_id": user_id, "document_id": 55}


def test_reparse_document_returns_404_when_not_found(monkeypatch: Any) -> None:
    from anima_server.api.routes import documents as documents_route

    monkeypatch.setattr(
        documents_route,
        "reparse_document",
        lambda runtime_db, *, user_id, document_id: ReparseResult(status="not_found"),
    )

    with managed_test_client("anima-documents-api-") as client:
        reg = _register_user(client, username="reparse-missing-user")
        headers = {"x-anima-unlock": str(reg["unlockToken"])}

        response = client.post("/api/documents/999/reparse", headers=headers)

        assert response.status_code == 404
        assert response.json()["error"] == "Document not found"


def test_reparse_document_returns_409_when_pack_not_ready(monkeypatch: Any) -> None:
    from anima_server.api.routes import documents as documents_route

    monkeypatch.setattr(
        documents_route,
        "reparse_document",
        lambda runtime_db, *, user_id, document_id: ReparseResult(status="pack_not_ready"),
    )

    with managed_test_client("anima-documents-api-") as client:
        reg = _register_user(client, username="reparse-pack-not-ready-user")
        headers = {"x-anima-unlock": str(reg["unlockToken"])}

        response = client.post("/api/documents/1/reparse", headers=headers)

        assert response.status_code == 409
        assert response.json()["error"] == "Parsing pack is not ready."


def test_reparse_document_returns_upgraded_unembedded_payload(monkeypatch: Any) -> None:
    from anima_server.api.routes import documents as documents_route

    monkeypatch.setattr(
        documents_route,
        "reparse_document",
        lambda runtime_db, *, user_id, document_id: ReparseResult(
            status="upgraded_unembedded", chunk_count=2
        ),
    )

    with managed_test_client("anima-documents-api-") as client:
        reg = _register_user(client, username="reparse-unembedded-user")
        headers = {"x-anima-unlock": str(reg["unlockToken"])}

        response = client.post("/api/documents/1/reparse", headers=headers)

        assert response.status_code == 200
        assert response.json() == {"status": "upgraded_unembedded", "chunk_count": 2}


def test_reparse_document_returns_502_when_parse_degraded(monkeypatch: Any) -> None:
    from anima_server.api.routes import documents as documents_route

    monkeypatch.setattr(
        documents_route,
        "reparse_document",
        lambda runtime_db, *, user_id, document_id: ReparseResult(status="parse_degraded"),
    )

    with managed_test_client("anima-documents-api-") as client:
        reg = _register_user(client, username="reparse-degraded-user")
        headers = {"x-anima-unlock": str(reg["unlockToken"])}

        response = client.post("/api/documents/1/reparse", headers=headers)

        assert response.status_code == 502
        assert (
            response.json()["error"]
            == "Quality parsing failed for this document; try again."
        )


def test_reparse_document_returns_503_when_parser_unavailable(monkeypatch: Any) -> None:
    from anima_server.api.routes import documents as documents_route

    monkeypatch.setattr(
        documents_route,
        "reparse_document",
        lambda runtime_db, *, user_id, document_id: ReparseResult(
            status="parser_unavailable",
            detail="the quality parser is not installed; install the docling extra",
        ),
    )

    with managed_test_client("anima-documents-api-") as client:
        reg = _register_user(client, username="reparse-parser-unavailable-user")
        headers = {"x-anima-unlock": str(reg["unlockToken"])}

        response = client.post("/api/documents/1/reparse", headers=headers)

        assert response.status_code == 503
        assert (
            "the quality parser is not installed; install the docling extra"
            in response.json()["error"]
        )


def test_reparse_document_returns_503_for_runtime_error(monkeypatch: Any) -> None:
    from anima_server.api.routes import documents as documents_route

    def raise_runtime_error(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Failed to read PDF file /tmp/x.pdf: corrupt")

    monkeypatch.setattr(documents_route, "reparse_document", raise_runtime_error)

    with managed_test_client("anima-documents-api-") as client:
        reg = _register_user(client, username="reparse-runtime-error-user")
        headers = {"x-anima-unlock": str(reg["unlockToken"])}

        response = client.post("/api/documents/1/reparse", headers=headers)

        assert response.status_code == 503
        assert "PDF parsing is unavailable" in response.json()["error"]


def test_reparse_document_returns_503_for_document_parsing_error(monkeypatch: Any) -> None:
    from anima_server.api.routes import documents as documents_route
    from anima_server.services.documents.parsing import DocumentParsingError

    def raise_parsing_error(*args: Any, **kwargs: Any) -> Any:
        raise DocumentParsingError("Docling could not extract any text from x.pdf.")

    monkeypatch.setattr(documents_route, "reparse_document", raise_parsing_error)

    with managed_test_client("anima-documents-api-") as client:
        reg = _register_user(client, username="reparse-parsing-error-user")
        headers = {"x-anima-unlock": str(reg["unlockToken"])}

        response = client.post("/api/documents/1/reparse", headers=headers)

        assert response.status_code == 503
        assert "PDF parsing is unavailable" in response.json()["error"]


def test_reparse_document_requires_auth() -> None:
    with managed_test_client("anima-documents-api-") as client:
        response = client.post("/api/documents/1/reparse")
        assert response.status_code == 401


def test_reparse_document_returns_400_for_storage_path_error(monkeypatch: Any) -> None:
    from anima_server.api.routes import documents as documents_route
    from anima_server.services.documents import DocumentStoragePathError

    def raise_storage_error(*args: Any, **kwargs: Any) -> Any:
        raise DocumentStoragePathError("Invalid document storage path.")

    monkeypatch.setattr(documents_route, "reparse_document", raise_storage_error)

    with managed_test_client("anima-documents-api-") as client:
        reg = _register_user(client, username="reparse-storage-error-user")
        headers = {"x-anima-unlock": str(reg["unlockToken"])}

        response = client.post("/api/documents/1/reparse", headers=headers)

        assert response.status_code == 400
        assert response.json()["error"] == "Invalid document storage path."
