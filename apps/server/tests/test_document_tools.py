from __future__ import annotations

from typing import Any

import pytest
from anima_server.config import settings
from anima_server.services.agent.document_tools import (
    get_document_outline,
    read_document_section,
    search_documents,
)
from anima_server.services.agent.tool_context import (
    ToolContext,
    clear_tool_context,
    set_tool_context,
)
from anima_server.services.documents import rag as documents_rag
from anima_server.services.documents.models import (
    DocumentRegistration,
    ExtractedDocumentChunk,
)
from anima_server.services.documents.rag import DocumentRagResult
from anima_server.services.documents.store import (
    register_document,
    replace_document_chunks,
)

pytest_plugins = ("conftest_runtime",)

USER_ID = 7
OTHER_USER_ID = 8


@pytest.fixture()
def tool_ctx(runtime_db):
    ctx = ToolContext(
        db=runtime_db,
        runtime_db=runtime_db,
        user_id=USER_ID,
        thread_id=42,
    )
    set_tool_context(ctx)
    yield ctx
    clear_tool_context()


def _make_document(
    runtime_db,
    *,
    user_id: int = USER_ID,
    filename: str = "manual.pdf",
    sha: str = "a" * 64,
    chunks: list[ExtractedDocumentChunk] | None = None,
):
    document = register_document(
        runtime_db,
        DocumentRegistration(
            user_id=user_id,
            filename=filename,
            mime_type="application/pdf",
            storage_path=f".anima/documents/{user_id}/{filename}",
            sha256=sha,
            size_bytes=1024,
        ),
    )
    if chunks is not None:
        replace_document_chunks(
            runtime_db,
            document_id=document.id,
            chunks=chunks,
        )
    return document


def _structured_chunks() -> list[ExtractedDocumentChunk]:
    return [
        ExtractedDocumentChunk(
            chunk_index=0,
            content_text="Intro paragraph before any heading.",
            page_start=1,
            page_end=1,
            section_title=None,
        ),
        ExtractedDocumentChunk(
            chunk_index=1,
            content_text="Open the relay housing and inspect the pads.",
            page_start=2,
            page_end=3,
            section_title="Guide > Inspection",
        ),
        ExtractedDocumentChunk(
            chunk_index=2,
            content_text="Measure coil resistance against the nameplate.",
            page_start=3,
            page_end=4,
            section_title="Guide > Inspection",
        ),
        ExtractedDocumentChunk(
            chunk_index=3,
            content_text="Replace relays every 10,000 cycles.",
            page_start=5,
            page_end=5,
            section_title="Guide > Replacement",
        ),
    ]


def test_get_document_outline_returns_section_tree(runtime_db, tool_ctx) -> None:
    document = _make_document(runtime_db, chunks=_structured_chunks())

    output = get_document_outline(str(document.id))

    assert f"doc:{document.id} manual.pdf" in output
    assert "- (untitled preamble) pages 1" in output
    assert "- Guide > Inspection pages 2-4 (2 chunks" in output
    assert "- Guide > Replacement pages 5 (1 chunk," in output
    assert tool_ctx.document_tool_citations == {document.id: "manual.pdf"}


def test_get_document_outline_page_fallback_for_legacy_documents(
    runtime_db, tool_ctx
) -> None:
    legacy_chunks = [
        ExtractedDocumentChunk(
            chunk_index=index,
            content_text=f"Legacy chunk {index}.",
            page_start=index + 1,
            page_end=index + 1,
            section_title=None,
        )
        for index in range(3)
    ]
    document = _make_document(runtime_db, chunks=legacy_chunks)

    output = get_document_outline(str(document.id))

    assert "- chunk 0 pages 1" in output
    assert "- chunk 2 pages 3" in output
    assert "no section structure" in output


def test_read_document_section_by_section_path(runtime_db, tool_ctx) -> None:
    document = _make_document(runtime_db, chunks=_structured_chunks())

    output = read_document_section(str(document.id), section_path="Guide > Inspection")

    assert "Open the relay housing" in output
    assert "Measure coil resistance" in output
    assert "Replace relays" not in output
    assert "Intro paragraph" not in output
    assert tool_ctx.document_tool_citations == {document.id: "manual.pdf"}


def test_read_document_section_by_page_range(runtime_db, tool_ctx) -> None:
    document = _make_document(runtime_db, chunks=_structured_chunks())

    output = read_document_section(str(document.id), page_start="5", page_end="5")

    assert "Replace relays" in output
    assert "Open the relay housing" not in output


def test_read_document_section_unknown_section_lists_known_sections(
    runtime_db, tool_ctx
) -> None:
    document = _make_document(runtime_db, chunks=_structured_chunks())

    output = read_document_section(str(document.id), section_path="Nope")

    assert 'No section matching "Nope"' in output
    assert "Guide > Inspection" in output


def test_read_document_section_truncates_and_continues(
    runtime_db, tool_ctx, monkeypatch: Any
) -> None:
    monkeypatch.setattr(settings, "document_tool_read_char_limit", 60)
    document = _make_document(runtime_db, chunks=_structured_chunks())

    first = read_document_section(str(document.id))
    assert "[Truncated at 60 chars. Continue with start_chunk=" in first

    continuation_index = int(first.rsplit("start_chunk=", 1)[1].split(".", 1)[0])
    second = read_document_section(
        str(document.id), start_chunk=str(continuation_index)
    )
    assert second != first


def test_document_tools_enforce_per_turn_budget(
    runtime_db, tool_ctx, monkeypatch: Any
) -> None:
    monkeypatch.setattr(settings, "document_tool_turn_char_budget", 250)
    document = _make_document(runtime_db, chunks=_structured_chunks())

    first = read_document_section(str(document.id))
    assert "Intro paragraph" in first  # some content emitted
    assert tool_ctx.document_tool_chars_used <= 250

    exhausted = read_document_section(str(document.id))
    assert "budget" in exhausted
    assert "error" not in exhausted.lower()


def test_read_continuation_notice_survives_budget_truncation(
    runtime_db, tool_ctx, monkeypatch: Any
) -> None:
    monkeypatch.setattr(settings, "document_tool_turn_char_budget", 300)
    oversized_text = "".join(f"word{index:03d} " for index in range(80)).strip()
    document = _make_document(
        runtime_db,
        chunks=[
            ExtractedDocumentChunk(
                chunk_index=0,
                content_text=oversized_text,
                page_start=1,
                page_end=1,
            )
        ],
    )

    output = read_document_section(str(document.id))

    # The whole message fits the remaining budget, and the continuation
    # hint is intact at the end instead of being chopped by the budget cap.
    assert len(output) <= 300
    assert "start_offset=" in output
    assert output.rstrip().endswith("]")


def test_document_tools_refuse_other_users_documents(runtime_db, tool_ctx) -> None:
    other_document = _make_document(
        runtime_db,
        user_id=OTHER_USER_ID,
        filename="secret.pdf",
        sha="b" * 64,
        chunks=[
            ExtractedDocumentChunk(
                chunk_index=0,
                content_text="Confidential content.",
                page_start=1,
                page_end=1,
            )
        ],
    )

    outline = get_document_outline(str(other_document.id))
    read = read_document_section(str(other_document.id))

    assert "does not exist in your library" in outline
    assert "does not exist in your library" in read
    assert "Confidential" not in outline
    assert "Confidential" not in read
    assert tool_ctx.document_tool_citations == {}


def test_search_documents_uses_hybrid_search_and_cites(
    runtime_db, tool_ctx, monkeypatch: Any
) -> None:
    document = _make_document(runtime_db, chunks=_structured_chunks())
    calls: list[dict[str, object]] = []

    def fake_search(
        db: object,
        user_id: int,
        query: str,
        *,
        document_ids,
        limit: int,
        embedding_fn=None,
    ) -> list[DocumentRagResult]:
        calls.append(
            {"user_id": user_id, "query": query, "document_ids": document_ids, "limit": limit}
        )
        return [
            DocumentRagResult(
                chunk_id=11,
                document_id=document.id,
                filename=document.filename,
                content="Open the relay housing and inspect the pads.",
                similarity=0.88,
                page_start=2,
                page_end=3,
                section_title="Guide > Inspection",
            )
        ]

    monkeypatch.setattr(documents_rag, "search_document_chunks", fake_search)

    output = search_documents("relay housing", document_ids=str(document.id))

    assert calls == [
        {
            "user_id": USER_ID,
            "query": "relay housing",
            "document_ids": [document.id],
            "limit": 8,
        }
    ]
    assert f"doc:{document.id} manual.pdf chunk:11 pages 2-3" in output
    assert 'section "Guide > Inspection"' in output
    assert tool_ctx.document_tool_citations == {document.id: "manual.pdf"}


def test_search_documents_defaults_to_thread_documents(
    runtime_db, tool_ctx, monkeypatch: Any
) -> None:
    seen_ids: list[object] = []

    def fake_search(db, user_id, query, *, document_ids, limit, embedding_fn=None):
        seen_ids.append(document_ids)
        return []

    monkeypatch.setattr(documents_rag, "search_document_chunks", fake_search)
    monkeypatch.setattr(
        "anima_server.services.agent.service._recent_thread_document_ids",
        lambda db, *, thread_id, user_id: [4, 5],
    )

    output = search_documents("relay")

    assert seen_ids == [[4, 5]]
    assert "No document matches" in output


def test_search_documents_without_thread_documents_suggests_scope_all(
    runtime_db, tool_ctx, monkeypatch: Any
) -> None:
    monkeypatch.setattr(
        "anima_server.services.agent.service._recent_thread_document_ids",
        lambda db, *, thread_id, user_id: [],
    )

    output = search_documents("relay")

    assert 'scope="all"' in output


def test_search_documents_scope_all_searches_whole_library(
    runtime_db, tool_ctx, monkeypatch: Any
) -> None:
    seen_ids: list[object] = ["sentinel"]

    def fake_search(db, user_id, query, *, document_ids, limit, embedding_fn=None):
        seen_ids[0] = document_ids
        return []

    monkeypatch.setattr(documents_rag, "search_document_chunks", fake_search)

    search_documents("relay", scope="all")

    assert seen_ids[0] is None


def test_search_documents_refuses_unowned_ids(runtime_db, tool_ctx) -> None:
    other_document = _make_document(
        runtime_db,
        user_id=OTHER_USER_ID,
        filename="secret.pdf",
        sha="c" * 64,
    )

    output = search_documents("anything", document_ids=str(other_document.id))

    assert "None of the requested document ids" in output


def test_tool_citations_become_document_source_pills(runtime_db, tool_ctx) -> None:
    from anima_server.services.agent import service as agent_service

    document = _make_document(runtime_db, chunks=_structured_chunks())
    get_document_outline(str(document.id))

    turn_ctx = agent_service._TurnContext(
        history=[],
        conversation_turn_count=0,
        memory_blocks=(),
    )
    agent_service._capture_document_tool_citations(turn_ctx)
    pills = agent_service._build_assistant_source_pills(turn_ctx)

    assert {
        "kind": "document_source",
        "label": "manual.pdf",
        "ref": document.id,
    } in pills

    # Re-capturing must not duplicate the pill.
    agent_service._capture_document_tool_citations(turn_ctx)
    refs = [pill["ref"] for pill in turn_ctx.document_source_pills]
    assert refs.count(document.id) == 1


def test_merged_section_paths_stay_addressable(runtime_db, tool_ctx) -> None:
    merged_chunk = ExtractedDocumentChunk(
        chunk_index=0,
        content_text="Alpha section body.\n\nBeta section body.",
        page_start=1,
        page_end=1,
        section_title="Guide > Alpha",
        metadata_json={"section_paths": ["Guide > Alpha", "Guide > Beta"]},
    )
    document = _make_document(runtime_db, chunks=[merged_chunk])

    outline = get_document_outline(str(document.id))
    assert "- Guide > Alpha" in outline
    assert "- Guide > Beta" in outline

    by_merged_path = read_document_section(
        str(document.id), section_path="Guide > Beta"
    )
    assert "Beta section body" in by_merged_path


def test_read_document_section_continues_inside_oversized_chunk(
    runtime_db, tool_ctx, monkeypatch: Any
) -> None:
    monkeypatch.setattr(settings, "document_tool_read_char_limit", 60)
    oversized_text = "".join(f"word{index:03d} " for index in range(40)).strip()
    document = _make_document(
        runtime_db,
        chunks=[
            ExtractedDocumentChunk(
                chunk_index=0,
                content_text=oversized_text,
                page_start=1,
                page_end=1,
            )
        ],
    )

    collected = ""
    start_chunk = "0"
    start_offset = "0"
    for _round in range(10):
        output = read_document_section(
            str(document.id), start_chunk=start_chunk, start_offset=start_offset
        )
        body = output.split("\n\n", 1)[1]
        body = body.split("[Truncated", 1)[0].strip()
        collected += body
        if "start_offset=" in output:
            tail = output.rsplit("start_chunk=", 1)[1]
            start_chunk = tail.split(",", 1)[0].strip()
            start_offset = tail.rsplit("start_offset=", 1)[1].split(".", 1)[0].strip("] ")
        else:
            break

    # The whole oversized chunk is reachable through continuations.
    assert collected.replace(" ", "") == oversized_text.replace(" ", "")


def test_untitled_preamble_merged_with_titled_section_stays_addressable(
    runtime_db, tool_ctx
) -> None:
    merged_chunk = ExtractedDocumentChunk(
        chunk_index=0,
        content_text="Intro preamble text.\n\nAlpha section body.",
        page_start=1,
        page_end=1,
        section_title=None,
        metadata_json={"section_paths": ["Guide > Alpha"]},
    )
    document = _make_document(runtime_db, chunks=[merged_chunk])

    outline = get_document_outline(str(document.id))
    assert "- Guide > Alpha" in outline
    assert "no section structure" not in outline

    by_path = read_document_section(str(document.id), section_path="Guide > Alpha")
    assert "Alpha section body" in by_path


def test_read_accounts_for_separators_so_hint_is_never_lost(
    runtime_db, tool_ctx, monkeypatch: Any
) -> None:
    # Budget close to the read cap with many small chunks: separator
    # overhead must be charged up front, or the body would overrun the cap
    # and the budget truncation would eat the continuation hint.
    monkeypatch.setattr(settings, "document_tool_turn_char_budget", 2280)
    document = _make_document(
        runtime_db,
        chunks=[
            ExtractedDocumentChunk(
                chunk_index=index,
                content_text=f"w{index:09d}",
                page_start=1,
                page_end=1,
            )
            for index in range(200)
        ],
    )

    output = read_document_section(str(document.id))

    assert len(output) <= 2280
    assert "Continue with start_chunk=" in output
    assert output.rstrip().endswith("]")


def test_read_parent_section_includes_descendant_chunks(runtime_db, tool_ctx) -> None:
    document = _make_document(
        runtime_db,
        chunks=[
            ExtractedDocumentChunk(
                chunk_index=0,
                content_text="Parent intro body.",
                section_title="Parent",
            ),
            ExtractedDocumentChunk(
                chunk_index=1,
                content_text="Child A body.",
                section_title="Parent > Child A",
            ),
            ExtractedDocumentChunk(
                chunk_index=2,
                content_text="Child B body.",
                section_title="Parent > Child B",
            ),
            ExtractedDocumentChunk(
                chunk_index=3,
                content_text="Parentheses trap body.",
                section_title="Parentheses",
            ),
        ],
    )

    parent = read_document_section(str(document.id), section_path="Parent")
    assert "Parent intro body" in parent
    assert "Child A body" in parent
    assert "Child B body" in parent
    # Prefix matching requires the path separator; sibling headings that
    # merely share a name prefix stay excluded.
    assert "Parentheses trap" not in parent

    child = read_document_section(str(document.id), section_path="Parent > Child A")
    assert "Child A body" in child
    assert "Child B body" not in child


def test_overlong_section_path_stays_addressable_after_title_truncation(
    runtime_db, tool_ctx
) -> None:
    long_path = "Deep Heading " + "x" * 300
    document = _make_document(
        runtime_db,
        chunks=[
            ExtractedDocumentChunk(
                chunk_index=0,
                content_text="Body under a very deep heading.",
                section_title=long_path,
                metadata_json={"section_paths": [long_path]},
            )
        ],
    )

    # The stored column value is truncated, but the metadata copy is intact
    # and the tools resolve the full path.
    output = read_document_section(str(document.id), section_path=long_path)
    assert "Body under a very deep heading" in output
