from __future__ import annotations

from typing import Any

from anima_server.config import settings
from anima_server.db.runtime import get_runtime_session_factory
from anima_server.models.runtime import RuntimeDocument
from anima_server.services.agent import pgvec_store as pgvec_module
from anima_server.services.agent.vector_store import VectorSearchResult
from anima_server.services.corefs.diary_migration import (
    read_prepared_writing_body,
    read_prepared_writing_snapshot,
)
from anima_server.services.documents import ExtractedDocumentChunk, pdf_workflow
from anima_server.services.documents.parsing import ExtractionOutcome
from anima_server.services.documents.parsing_pack import ParsingPackStatus
from anima_server.services.documents.pdf_text import PageText
from anima_server.services.sessions import unlock_session_store
from conftest import managed_test_client
from fastapi.testclient import TestClient
from sqlalchemy import select


def _register_user(
    client: TestClient,
    *,
    username: str = "document-api-user",
) -> dict[str, object]:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pw123456", "name": "Document User"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _patch_pdf_edges(
    monkeypatch: Any,
    *,
    proposed_facts: list[dict[str, object]] | None = None,
) -> None:
    from anima_server.api.routes import documents as documents_route

    def fake_dependencies() -> pdf_workflow.PDFIngestionDependencies:
        return pdf_workflow.PDFIngestionDependencies(
            extract_text=lambda _source: ExtractionOutcome(
                pages=[PageText(page_number=1, text="alpha installation guide")],
                parse_quality="docling",
            ),
            chunk_text=lambda _pages: [
                ExtractedDocumentChunk(
                    chunk_index=0,
                    content_text="alpha installation guide",
                    page_start=1,
                    page_end=1,
                )
            ],
            embedding_fn=lambda _text: [0.0] * 384,
            summarize=lambda document, chunks: {
                "title": document.filename,
                "chunk_count": len(chunks),
                "summary": f"Indexed {len(chunks)} chunks from {document.filename}.",
            },
            propose_facts=lambda _document, _chunks, _summary: (
                [] if proposed_facts is None else proposed_facts
            ),
        )

    monkeypatch.setattr(documents_route, "_default_pdf_dependencies", fake_dependencies)


def test_greenfield_pdf_upload_is_corefs_only_and_completes_workflow(
    monkeypatch: Any,
) -> None:
    _patch_pdf_edges(monkeypatch, proposed_facts=[])
    with managed_test_client("anima-documents-greenfield-") as client:
        registered = _register_user(client)
        user_id = int(registered["id"])
        token = str(registered["unlockToken"])
        headers = {"x-anima-unlock": token}
        content = b"%PDF-1.4\ncanonical document body\n%%EOF"

        uploaded = client.post(
            "/api/documents/pdf",
            headers=headers,
            data={"userId": str(user_id)},
            files={"file": ("Canonical Manual.pdf", content, "application/pdf")},
        )
        assert uploaded.status_code == 201, uploaded.text
        payload = uploaded.json()
        storage_path = payload["document"]["storagePath"]
        assert storage_path.startswith("corefs://object/")
        assert not (settings.data_dir / ".anima" / "documents" / str(user_id)).exists()

        session = unlock_session_store.resolve(token)
        assert session is not None
        stable_id = storage_path.rsplit("/", 1)[-1]
        source = next(
            item
            for item in read_prepared_writing_snapshot(session=session).objects
            if item.stable_id == stable_id
        )
        assert source.kind == "attachment"
        assert read_prepared_writing_body(session=session, item=source) == content

        status = client.get(f"/api/documents/workflows/{payload['workflowId']}", headers=headers)
        assert status.status_code == 200, status.text
        assert status.json()["input"]["storage_path"] == storage_path

        resumed = client.post(
            f"/api/documents/workflows/{payload['workflowId']}/resume", headers=headers
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["currentState"] == "awaiting_approval"

        approved = client.post(
            f"/api/documents/workflows/{payload['workflowId']}/approve-memory",
            headers=headers,
            json={"proposalIndices": []},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "completed"

        with get_runtime_session_factory()() as runtime_db:
            document = runtime_db.scalar(
                select(RuntimeDocument).where(RuntimeDocument.user_id == user_id)
            )
            assert document is not None
            assert document.storage_path == storage_path


def test_greenfield_document_workflow_rejects_host_storage_paths() -> None:
    with managed_test_client("anima-documents-no-legacy-") as client:
        registered = _register_user(client, username="document-no-legacy")
        user_id = int(registered["id"])
        headers = {"x-anima-unlock": str(registered["unlockToken"])}
        response = client.post(
            "/api/documents/workflows/pdf",
            headers=headers,
            json={
                "userId": user_id,
                "filename": "legacy.pdf",
                "mimeType": "application/pdf",
                "storagePath": f".anima/documents/{user_id}/legacy.pdf",
                "sha256": "a" * 64,
                "sizeBytes": 10,
                "metadata": {},
            },
        )
        assert response.status_code == 409


def test_document_search_uses_canonical_workflow_chunks(monkeypatch: Any) -> None:
    _patch_pdf_edges(monkeypatch, proposed_facts=[])
    monkeypatch.setattr(settings, "retrieval_reranker", "off")
    monkeypatch.setattr(
        "anima_server.services.documents.rag.generate_embedding",
        lambda _text: [0.0] * 384,
    )
    monkeypatch.setattr(
        pgvec_module.PgVecStore,
        "search_by_vector",
        lambda *_args, **_kwargs: [
            VectorSearchResult(
                item_id=1,
                content="alpha preview",
                category="document",
                importance=3,
                similarity=0.91,
                source_type="document_chunk",
            )
        ],
    )

    with managed_test_client("anima-documents-search-") as client:
        registered = _register_user(client, username="document-search")
        user_id = int(registered["id"])
        headers = {"x-anima-unlock": str(registered["unlockToken"])}
        uploaded = client.post(
            "/api/documents/pdf",
            headers=headers,
            data={"userId": str(user_id)},
            files={
                "file": (
                    "Search.pdf",
                    b"%PDF-1.4\ncanonical search document\n%%EOF",
                    "application/pdf",
                )
            },
        )
        assert uploaded.status_code == 201, uploaded.text
        workflow_id = int(uploaded.json()["workflowId"])
        assert (
            client.post(
                f"/api/documents/workflows/{workflow_id}/resume", headers=headers
            ).status_code
            == 200
        )

        result = client.post(
            "/api/documents/search",
            headers=headers,
            json={"userId": user_id, "query": "installation", "limit": 3},
        )
        assert result.status_code == 200, result.text
        assert result.json()["count"] == 1


def test_parsing_pack_status_and_download(monkeypatch: Any) -> None:
    from anima_server.api.routes import documents as documents_route

    monkeypatch.setattr(
        documents_route,
        "pack_status",
        lambda: ParsingPackStatus(state="downloading", progress=0.42),
    )
    monkeypatch.setattr(
        documents_route,
        "ensure_parsing_pack",
        lambda: ParsingPackStatus(state="downloading", progress=0.0),
    )
    with managed_test_client("anima-documents-pack-") as client:
        assert client.get("/api/documents/parsing-pack").status_code == 401
        registered = _register_user(client, username="document-pack")
        headers = {"x-anima-unlock": str(registered["unlockToken"])}
        status = client.get("/api/documents/parsing-pack", headers=headers)
        assert status.status_code == 200
        assert status.json()["progress"] == 0.42
        download = client.post("/api/documents/parsing-pack/download", headers=headers)
        assert download.status_code == 200
        assert download.json()["progress"] == 0.0
