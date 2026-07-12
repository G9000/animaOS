"""Agent tools for iterative document investigation.

The injected document context block is only a first-turn primer; these tools
let the agent search, orient (outline), and read documents on demand during
the loop. All access is scoped to the turn's user via the tool context, and
total returned document text is bounded per turn by
``document_tool_turn_char_budget``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from anima_server.services.agent.tools import tool

logger = logging.getLogger(__name__)

_SEARCH_EXCERPT_CHARS = 400
_MAX_SEARCH_LIMIT = 20
_OUTLINE_MAX_LINES = 60
_UNTITLED_SECTION = "(untitled preamble)"

_BUDGET_EXHAUSTED_NOTICE = (
    "Per-turn document text budget is used up. Answer from the evidence "
    "already gathered this turn."
)


@tool
def search_documents(
    query: str,
    document_ids: str = "",
    scope: str = "auto",
    limit: str = "8",
) -> str:
    """Search the user's indexed documents (hybrid keyword + semantic). Returns chunk excerpts with document ids, pages, and section paths. Defaults to the documents active in this conversation; pass scope="all" to search the whole library, or document_ids as comma-separated ids. Follow up with get_document_outline / read_document_section for depth."""
    from anima_server.services.agent.tool_context import get_tool_context
    from anima_server.services.documents import rag as documents_rag

    query_stripped = query.strip()
    if not query_stripped:
        return "Please provide a document search query."
    if scope not in {"auto", "all"}:
        return "Unknown scope. Valid scopes: auto (conversation documents), all (whole library)."

    ctx = get_tool_context()
    remaining = _budget_remaining(ctx)
    if remaining <= 0:
        return _BUDGET_EXHAUSTED_NOTICE

    allowed_ids: list[int] | None
    requested_ids = _parse_id_list(document_ids)
    if requested_ids:
        owned = _owned_documents(ctx, requested_ids)
        if not owned:
            return "None of the requested document ids exist in your library."
        allowed_ids = [document.id for document in owned]
    elif scope == "all":
        allowed_ids = None
    else:
        allowed_ids = _active_thread_document_ids(ctx)
        if not allowed_ids:
            return (
                "No documents are active in this conversation. "
                'Retry with scope="all" to search the whole library.'
            )

    max_results = _parse_bounded_int(limit, default=8, low=1, high=_MAX_SEARCH_LIMIT)
    results = documents_rag.search_document_chunks(
        ctx.runtime_db,
        ctx.user_id,
        query_stripped,
        document_ids=allowed_ids,
        limit=max_results,
    )
    if not results:
        return f"No document matches for: {query_stripped}"

    lines = [f"Found {len(results)} document chunk matches for: {query_stripped}"]
    for result in results:
        _record_citation(ctx, result.document_id, result.filename)
        location = _format_pages(result.page_start, result.page_end)
        section = f' section "{result.section_title}"' if result.section_title else ""
        excerpt = _clean_excerpt(result.content, _SEARCH_EXCERPT_CHARS)
        lines.append(
            f"- doc:{result.document_id} {result.filename} chunk:{result.chunk_id}"
            f"{location}{section} (score={result.similarity:.2f}): {excerpt}"
        )
    lines.append(
        "Use read_document_section(document_id, section_path=...) to read a full section."
    )
    return _emit_within_budget(ctx, "\n".join(lines))


@tool
def get_document_outline(document_id: str) -> str:
    """Return a document's section tree (section paths with page ranges and sizes) so you can decide what to read. Falls back to a per-chunk page outline for documents indexed without structure."""
    from anima_server.services.agent.tool_context import get_tool_context

    ctx = get_tool_context()
    document, chunks, error = _load_owned_document_chunks(ctx, document_id)
    if error is not None:
        return error
    assert document is not None

    _record_citation(ctx, document.id, document.filename)
    lines = [f"Outline of doc:{document.id} {document.filename}:"]
    sections = _section_summaries(chunks)
    if sections:
        for title, page_start, page_end, chunk_count, char_count in sections:
            pages = _format_pages(page_start, page_end)
            lines.append(
                f"- {title}{pages} ({chunk_count} chunk{'s' if chunk_count != 1 else ''},"
                f" ~{char_count} chars)"
            )
        lines.append(
            'Read a section with read_document_section(document_id, section_path="<section>").'
        )
    else:
        for chunk in chunks[:_OUTLINE_MAX_LINES]:
            pages = _format_pages(chunk.page_start, chunk.page_end)
            lines.append(
                f"- chunk {chunk.chunk_index}{pages} (~{len(chunk.content_text)} chars)"
            )
        if len(chunks) > _OUTLINE_MAX_LINES:
            lines.append(f"... {len(chunks) - _OUTLINE_MAX_LINES} more chunks.")
        lines.append(
            "This document has no section structure; read it with "
            "read_document_section(document_id, page_start=..., page_end=...)."
        )
    return _emit_within_budget(ctx, "\n".join(lines))


@tool
def read_document_section(
    document_id: str,
    section_path: str = "",
    page_start: str = "",
    page_end: str = "",
    start_chunk: str = "0",
    start_offset: str = "0",
) -> str:
    """Read document text: a full section by section_path (from get_document_outline), a page range, or the document sequentially. Long reads are bounded per call — continue with the start_chunk (and, inside an oversized chunk, start_offset) values given in the truncation notice."""
    from anima_server.config import settings
    from anima_server.services.agent.tool_context import get_tool_context

    ctx = get_tool_context()
    remaining = _budget_remaining(ctx)
    if remaining <= 0:
        return _BUDGET_EXHAUSTED_NOTICE

    document, chunks, error = _load_owned_document_chunks(ctx, document_id)
    if error is not None:
        return error
    assert document is not None

    section = section_path.strip()
    first_page = _parse_optional_int(page_start)
    last_page = _parse_optional_int(page_end)
    selected = _select_chunks(
        chunks,
        section_path=section,
        page_start=first_page,
        page_end=last_page,
    )
    if not selected:
        if section:
            known = _section_summaries(chunks)
            hint = (
                " Known sections: " + "; ".join(title for title, *_rest in known[:12])
                if known
                else " This document has no section structure; use page_start/page_end."
            )
            return f'No section matching "{section}" in doc:{document.id}.{hint}'
        return f"No content in that page range of doc:{document.id}."

    minimum_chunk = _parse_bounded_int(start_chunk, default=0, low=0, high=10**9)
    selected = [chunk for chunk in selected if chunk.chunk_index >= minimum_chunk]
    if not selected:
        return (
            f"No further content past start_chunk={minimum_chunk} in that selection "
            f"of doc:{document.id}."
        )

    offset = _parse_bounded_int(start_offset, default=0, low=0, high=10**9)
    call_cap = min(settings.document_tool_read_char_limit, remaining)
    parts: list[str] = []
    used = 0
    next_chunk_index: int | None = None
    next_offset = 0
    budget_limited = False
    for position, chunk in enumerate(selected):
        chunk_offset = offset if position == 0 else 0
        text = chunk.content_text[chunk_offset:]
        if not text:
            continue
        if parts and used + len(text) > call_cap:
            next_chunk_index = chunk.chunk_index
            budget_limited = call_cap < settings.document_tool_read_char_limit
            break
        if not parts and len(text) > call_cap:
            # An oversized chunk (atomic table/code or a legacy chunk):
            # continue inside the same chunk so its tail stays reachable.
            parts.append(text[:call_cap].rstrip())
            used = call_cap
            next_chunk_index = chunk.chunk_index
            next_offset = chunk_offset + call_cap
            budget_limited = call_cap < settings.document_tool_read_char_limit
            break
        parts.append(text)
        used += len(text)

    _record_citation(ctx, document.id, document.filename)
    where = f' section "{section}"' if section else ""
    if first_page is not None or last_page is not None:
        where += _format_pages(first_page, last_page)
    lines = [f"doc:{document.id} {document.filename}{where}:", "", "\n\n".join(parts)]
    if next_chunk_index is not None:
        continuation = f"start_chunk={next_chunk_index}"
        if next_offset:
            continuation += f", start_offset={next_offset}"
        if budget_limited:
            lines.append("")
            lines.append(
                "[Truncated: per-turn document text budget is nearly used up. "
                f"Continue with {continuation} only if essential.]"
            )
        else:
            lines.append("")
            lines.append(
                f"[Truncated at {call_cap} chars. Continue with {continuation}.]"
            )
    return _emit_within_budget(ctx, "\n".join(lines))


def get_document_tools() -> list[Any]:
    return [search_documents, get_document_outline, read_document_section]


def _budget_remaining(ctx: Any) -> int:
    from anima_server.config import settings

    return max(
        0, settings.document_tool_turn_char_budget - ctx.document_tool_chars_used
    )


def _emit_within_budget(ctx: Any, output: str) -> str:
    """Charge *output* against the turn budget, truncating when it overflows.

    The truncation notice itself is not charged, so accounting never exceeds
    the configured budget.
    """
    remaining = _budget_remaining(ctx)
    if len(output) > remaining:
        ctx.document_tool_chars_used += remaining
        return output[:remaining].rstrip() + (
            "\n[Truncated: per-turn document text budget reached. Answer from "
            "the evidence already gathered.]"
        )
    ctx.document_tool_chars_used += len(output)
    return output


def _record_citation(ctx: Any, document_id: int, filename: str) -> None:
    ctx.document_tool_citations.setdefault(document_id, filename)


def _owned_documents(ctx: Any, document_ids: Sequence[int]) -> list[Any]:
    from anima_server.services.documents.store import get_document_for_user

    owned = []
    for document_id in document_ids:
        document = get_document_for_user(
            ctx.runtime_db, user_id=ctx.user_id, document_id=document_id
        )
        if document is not None:
            owned.append(document)
    return owned


def _active_thread_document_ids(ctx: Any) -> list[int]:
    from anima_server.services.agent.service import _recent_thread_document_ids

    try:
        return _recent_thread_document_ids(
            ctx.runtime_db,
            thread_id=ctx.thread_id,
            user_id=ctx.user_id,
        )
    except Exception:
        logger.debug(
            "Active thread document lookup failed for user %s thread %s",
            ctx.user_id,
            ctx.thread_id,
            exc_info=True,
        )
        return []


def _load_owned_document_chunks(
    ctx: Any, document_id: str
) -> tuple[Any | None, list[Any], str | None]:
    from anima_server.services.documents.store import (
        get_document_for_user,
        list_document_chunks,
    )

    parsed_id = _parse_optional_int(document_id)
    if parsed_id is None or parsed_id <= 0:
        return None, [], "Please provide a valid document_id."
    document = get_document_for_user(
        ctx.runtime_db, user_id=ctx.user_id, document_id=parsed_id
    )
    if document is None:
        return None, [], f"Document {parsed_id} does not exist in your library."
    chunks = list_document_chunks(ctx.runtime_db, document_id=document.id)
    if not chunks:
        return None, [], f"doc:{document.id} {document.filename} has no indexed content."
    return document, chunks, None


def _chunk_section_paths(chunk: Any) -> list[str]:
    """Every section path a chunk answers to (merged sections included)."""
    metadata = chunk.metadata_json or {}
    merged = metadata.get("section_paths")
    if isinstance(merged, list):
        paths = [path for path in merged if isinstance(path, str) and path]
        if paths:
            return paths
    return [chunk.section_title] if chunk.section_title else []


def _section_summaries(
    chunks: Sequence[Any],
) -> list[tuple[str, int | None, int | None, int, int]]:
    """(title, page_start, page_end, chunk_count, char_count) in first-appearance order."""
    if not any(_chunk_section_paths(chunk) for chunk in chunks):
        return []
    order: list[str] = []
    stats: dict[str, list[Any]] = {}
    for chunk in chunks:
        for title in _chunk_section_paths(chunk) or [_UNTITLED_SECTION]:
            if title not in stats:
                order.append(title)
                stats[title] = [chunk.page_start, chunk.page_end, 0, 0]
            entry = stats[title]
            if chunk.page_start is not None:
                entry[0] = (
                    chunk.page_start
                    if entry[0] is None
                    else min(entry[0], chunk.page_start)
                )
            if chunk.page_end is not None:
                entry[1] = (
                    chunk.page_end if entry[1] is None else max(entry[1], chunk.page_end)
                )
            entry[2] += 1
            entry[3] += len(chunk.content_text)
    return [
        (title, *stats[title])  # type: ignore[misc]
        for title in order
    ]


def _select_chunks(
    chunks: Sequence[Any],
    *,
    section_path: str,
    page_start: int | None,
    page_end: int | None,
) -> list[Any]:
    if section_path:
        if section_path == _UNTITLED_SECTION:
            return [chunk for chunk in chunks if not chunk.section_title]
        return [
            chunk
            for chunk in chunks
            if section_path in _chunk_section_paths(chunk)
        ]
    if page_start is not None or page_end is not None:
        low = page_start if page_start is not None else 1
        high = page_end if page_end is not None else 10**9
        return [
            chunk
            for chunk in chunks
            if chunk.page_start is not None
            and chunk.page_end is not None
            and chunk.page_end >= low
            and chunk.page_start <= high
        ]
    return list(chunks)


def _parse_id_list(raw: str) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for piece in raw.replace(";", ",").split(","):
        value = _parse_optional_int(piece)
        if value is not None and value > 0 and value not in seen:
            seen.add(value)
            ids.append(value)
    return ids


def _parse_optional_int(raw: str) -> int | None:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _parse_bounded_int(raw: str, *, default: int, low: int, high: int) -> int:
    value = _parse_optional_int(raw)
    if value is None:
        return default
    return max(low, min(high, value))


def _clean_excerpt(content: str, limit: int) -> str:
    cleaned = " ".join(content.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def _format_pages(page_start: int | None, page_end: int | None) -> str:
    if page_start is None and page_end is None:
        return ""
    if page_end is None or page_end == page_start:
        return f" pages {page_start}" if page_start is not None else f" pages {page_end}"
    if page_start is None:
        return f" pages -{page_end}"
    return f" pages {page_start}-{page_end}"


__all__ = [
    "get_document_outline",
    "get_document_tools",
    "read_document_section",
    "search_documents",
]
