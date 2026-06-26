from __future__ import annotations

from typing import Any

from anima_server.services.agent import service as agent_service
from anima_server.services.documents.rag import DocumentRagResult


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
