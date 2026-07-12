from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models.runtime import (
    RuntimeKnowledgeBundleRun,
    RuntimeKnowledgeConceptSource,
    RuntimeSource,
    RuntimeSourceSpan,
)
from anima_server.services.ingestion.compiler import (
    CompileMode,
    CompileResult,
    compile_source_to_concepts,
)
from anima_server.services.ingestion.retrieval import EmbeddingFn

logger = logging.getLogger(__name__)

_MAX_CONCEPT_SLUG_LENGTH = 255
_MAX_CONCEPT_TITLE_LENGTH = 512

_PROMPT_SPAN_LIMIT = 80
_PROMPT_SPAN_CHARS = 700
_PROMPT_RELATED_CONCEPTS = 8

_COMPILER_SYSTEM_PROMPT = """You are the knowledge compiler for a personal wiki. You turn a raw ingested source into maintained, citable concept pages.

Return ONE JSON object, nothing else:
{
  "concepts": [
    {
      "type": "topic" | "source_summary" | "entity" | "process",
      "slug": "kebab-case-stable-identifier",
      "title": "...",
      "description": "one sentence",
      "body_markdown": "# Title\\n\\nsynthesized page body",
      "source_span_ids": [<int>, ...],
      "tags": ["..."],
      "merge_confidence": 0.0-1.0 (optional; only when updating an existing concept by title)
    }
  ],
  "links": [
    {"source_slug": "...", "target_slug": "...", "link_type": "supports" | "contradicts" | "updates" | "relates_to", "confidence": 0.0-1.0}
  ],
  "metadata": {"compiler": "llm_wiki"}
}

Rules:
- Every concept MUST cite the span ids its claims come from in source_span_ids. Uncited concepts are discarded.
- Synthesize: group related spans into coherent topic pages instead of one page per span. Include exactly one source_summary concept for the whole source.
- Merge, do not duplicate: when a RELATED EXISTING CONCEPT covers the same subject, reuse its slug exactly (this updates that page and adds this source's citations) and integrate the new evidence into the body.
- Cross-link: add supports/contradicts/updates links between your concepts and toward existing concept slugs when the evidence warrants it.
- Slugs must be stable, lowercase, hyphenated, without slashes.
- Write body_markdown as a readable wiki page, not a span dump."""


def compile_source_knowledge(
    db: Session,
    *,
    source: RuntimeSource,
    spans: Sequence[RuntimeSourceSpan],
    embedding_fn: EmbeddingFn | None = None,
    mode: CompileMode = "initial",
) -> CompileResult:
    """Deterministic compiler: one summary plus one topic per span (no-LLM fallback)."""
    # Section spans duplicate their child chunk/paragraph content; compiling
    # them too would double every topic. Evidence spans only.
    evidence_spans = [span for span in spans if span.span_kind != "section"]
    return compile_source_to_concepts(
        db,
        user_id=source.user_id,
        source_id=source.id,
        span_ids=[span.id for span in evidence_spans],
        model=lambda _request: json.dumps(_source_payload(source, evidence_spans)),
        mode=mode,
        embedding_fn=embedding_fn,
    )


async def compile_source_knowledge_auto(
    db: Session,
    *,
    source: RuntimeSource,
    spans: Sequence[RuntimeSourceSpan],
    embedding_fn: EmbeddingFn | None = None,
    mode: CompileMode = "initial",
    llm_client: Any | None = None,
) -> CompileResult:
    """Compile via the configured backend (ANIMA_KNOWLEDGE_COMPILER=llm|deterministic)."""
    if settings.knowledge_compiler == "deterministic":
        return compile_source_knowledge(
            db, source=source, spans=spans, embedding_fn=embedding_fn, mode=mode
        )
    return await compile_source_knowledge_llm(
        db,
        source=source,
        spans=spans,
        embedding_fn=embedding_fn,
        mode=mode,
        llm_client=llm_client,
    )


async def compile_source_knowledge_llm(
    db: Session,
    *,
    source: RuntimeSource,
    spans: Sequence[RuntimeSourceSpan],
    embedding_fn: EmbeddingFn | None = None,
    mode: CompileMode = "initial",
    llm_client: Any | None = None,
) -> CompileResult:
    """Compile with the runtime's configured model.

    The LLM is invoked first; its output is funneled through the sync
    compiler contract so all persistence, merge, and failure bookkeeping
    stay in ``compile_source_to_concepts``. Malformed output records a
    failed bundle run (existing concepts untouched); an unavailable LLM
    (network/configuration) falls back to the deterministic compiler so
    ingestion never blocks on the model being up.
    """
    from anima_server.services.agent.json_utils import parse_json_object
    from anima_server.services.agent.llm_json import call_llm_for_text

    evidence_spans = [span for span in spans if span.span_kind != "section"]
    # Every span must be visible to the model: long sources compile in
    # batches instead of silently truncating at the prompt span cap (the
    # autocompile cooldown would otherwise never revisit the tail).
    batches = [
        evidence_spans[start : start + _PROMPT_SPAN_LIMIT]
        for start in range(0, len(evidence_spans), _PROMPT_SPAN_LIMIT)
    ] or [[]]

    combined: dict[str, Any] = {
        "concepts": [],
        "links": [],
        "metadata": {"compiler": "llm_wiki", "batches": len(batches)},
    }
    for batch in batches:
        system, prompt = _build_compile_prompt(db, source=source, spans=batch)
        try:
            raw = await call_llm_for_text(system, prompt, client=llm_client)
        except Exception:
            logger.warning(
                "LLM compile unavailable for source %s; using deterministic compiler",
                source.id,
                exc_info=True,
            )
            return compile_source_knowledge(
                db, source=source, spans=spans, embedding_fn=embedding_fn, mode=mode
            )
        payload = parse_json_object(raw)
        if payload is None or not isinstance(payload.get("concepts"), list):
            def _malformed(_request: Any, _raw: str = raw) -> str:
                # Hand the raw output to the compiler so it records its own
                # malformed-output failure (existing concepts untouched).
                return _raw

            return compile_source_to_concepts(
                db,
                user_id=source.user_id,
                source_id=source.id,
                span_ids=[span.id for span in evidence_spans],
                model=_malformed,
                mode=mode,
                embedding_fn=embedding_fn,
            )
        combined["concepts"].extend(payload["concepts"])
        links = payload.get("links")
        if isinstance(links, list):
            combined["links"].extend(links)

    def _model(_request: Any) -> str:
        # Citation enforcement raises inside the compiler so a
        # parseable-but-uncited payload records a failed bundle run.
        return _prepare_llm_payload(combined, evidence_spans)

    return compile_source_to_concepts(
        db,
        user_id=source.user_id,
        source_id=source.id,
        span_ids=[span.id for span in evidence_spans],
        model=_model,
        mode=mode,
        embedding_fn=embedding_fn,
    )


def _prepare_llm_payload(
    payload: dict[str, Any], spans: Sequence[RuntimeSourceSpan]
) -> str:
    """Drop uncited concepts (and their links); coalesce duplicate slugs.

    Batched compiles can emit the same slug from more than one batch. The
    downstream compiler replaces a concept's citations per payload entry, so
    duplicates must merge here — first occurrence keeps the content fields,
    citations and tags are unioned — or later batches would silently
    overwrite earlier-batch evidence.
    """
    valid_span_ids = {span.id for span in spans}
    kept_by_slug: dict[str, dict[str, Any]] = {}
    kept_order: list[str] = []
    unsluggable: list[dict[str, Any]] = []
    uncited_slugs: set[str] = set()
    for concept in payload["concepts"]:
        if not isinstance(concept, dict):
            continue
        cited = [
            span_id
            for span_id in (concept.get("source_span_ids") or [])
            if isinstance(span_id, int) and span_id in valid_span_ids
        ]
        slug = concept.get("slug")
        if isinstance(slug, str):
            # Model output derived from long headings must fit the concept
            # columns; the same bound applies to link slug references below.
            slug = _limit_slug(slug)
        if not cited:
            if isinstance(slug, str):
                uncited_slugs.add(slug)
            continue
        if not isinstance(slug, str) or not slug:
            # Preserved so the compiler records its own missing-slug failure.
            unsluggable.append({**concept, "source_span_ids": cited})
            continue
        concept = {**concept, "slug": slug, "source_span_ids": cited}
        title = concept.get("title")
        if isinstance(title, str):
            concept["title"] = _limit_title(title)
        existing = kept_by_slug.get(slug)
        if existing is None:
            kept_by_slug[slug] = concept
            kept_order.append(slug)
            continue
        existing["source_span_ids"] = existing["source_span_ids"] + [
            span_id
            for span_id in cited
            if span_id not in existing["source_span_ids"]
        ]
        existing_tags = (
            existing.get("tags") if isinstance(existing.get("tags"), list) else []
        )
        new_tags = (
            concept.get("tags") if isinstance(concept.get("tags"), list) else []
        )
        existing["tags"] = existing_tags + [
            tag for tag in new_tags if tag not in existing_tags
        ]
    kept_concepts = [kept_by_slug[slug] for slug in kept_order] + unsluggable
    dropped_slugs = uncited_slugs - set(kept_order)
    if not kept_concepts:
        raise ValueError(
            "Compiler model output contained no concepts with valid span citations."
        )

    links = payload.get("links")
    kept_links = []
    for link in links if isinstance(links, list) else []:
        if isinstance(link, dict):
            link = {
                **link,
                **{
                    key: _limit_slug(value)
                    for key in ("source_slug", "target_slug")
                    if isinstance(value := link.get(key), str)
                },
            }
            if (
                link.get("source_slug") in dropped_slugs
                or link.get("target_slug") in dropped_slugs
            ):
                continue
        kept_links.append(link)
    return json.dumps(
        {**payload, "concepts": kept_concepts, "links": kept_links}
    )


def _build_compile_prompt(
    db: Session,
    *,
    source: RuntimeSource,
    spans: Sequence[RuntimeSourceSpan],
) -> tuple[str, str]:
    lines = [
        "SOURCE",
        f"- id: {source.id}",
        f"- kind: {source.kind}",
        f"- title: {_source_title(source)}",
        f"- uri: {source.source_uri}",
        "",
        f"SPANS ({len(spans)})",
    ]
    for span in spans:
        metadata = span.metadata_json or {}
        section_path = metadata.get("section_path") or metadata.get("heading") or ""
        section = f" [{section_path}]" if section_path else ""
        location = _span_location(span)
        lines.append(
            f"- span {span.id}{section}{location}: "
            f"{_compact_text(span.content_text, limit=_PROMPT_SPAN_CHARS)}"
        )

    related = _related_existing_concepts(db, source=source, spans=spans)
    if related:
        lines.append("")
        lines.append("RELATED EXISTING CONCEPTS (reuse these slugs to update/merge)")
        for concept in related:
            lines.append(
                f"- slug: {concept.slug} [{concept.concept_type}] {concept.title}: "
                f"{_compact_text(concept.summary, limit=240)}"
            )

    lines.append("")
    lines.append(
        "Compile this source into concept pages now. Respond with the JSON object only."
    )
    return _COMPILER_SYSTEM_PROMPT, "\n".join(lines)


def _related_existing_concepts(
    db: Session,
    *,
    source: RuntimeSource,
    spans: Sequence[RuntimeSourceSpan],
) -> list[Any]:
    from anima_server.services.ingestion.retrieval import retrieve_knowledge

    query_parts = [_source_title(source)]
    for span in list(spans)[:3]:
        query_parts.append(_compact_text(span.content_text, limit=200))
    try:
        result = retrieve_knowledge(
            db,
            user_id=source.user_id,
            query="\n".join(query_parts),
            limit_concepts=_PROMPT_RELATED_CONCEPTS,
            limit_spans=0,
        )
    except Exception:
        logger.debug(
            "Related-concept retrieval failed for source %s",
            source.id,
            exc_info=True,
        )
        return []
    # Concepts previously compiled from this same source are not merge
    # candidates — slug reuse already covers refresh.
    return [
        concept
        for concept in result.concepts
        if concept.slug and not concept.slug.startswith(f"source-{source.id}-")
    ]


def find_autocompile_candidates(
    db: Session,
    *,
    user_id: int,
    policy: str,
    budget: int,
    cooldown_hours: float,
) -> list[RuntimeSource]:
    """Sources with spans but no compiled concepts, honoring policy and cooldown.

    Mirrors the ``orphan_source`` lint rule as a query. A source with any
    compile bundle run inside the cooldown window (success or failure) is
    skipped so the sleep agent doesn't hammer a failing source every cycle.
    """
    if policy == "off" or budget <= 0:
        return []

    has_spans = (
        select(RuntimeSourceSpan.id)
        .where(
            RuntimeSourceSpan.source_id == RuntimeSource.id,
            RuntimeSourceSpan.user_id == user_id,
        )
        .exists()
    )
    has_concept_citations = (
        select(RuntimeKnowledgeConceptSource.id)
        .where(
            RuntimeKnowledgeConceptSource.source_id == RuntimeSource.id,
            RuntimeKnowledgeConceptSource.user_id == user_id,
        )
        .exists()
    )
    cooldown_cutoff = datetime.now(UTC) - timedelta(hours=max(0.0, cooldown_hours))
    recently_attempted = (
        select(RuntimeKnowledgeBundleRun.id)
        .where(
            RuntimeKnowledgeBundleRun.source_id == RuntimeSource.id,
            RuntimeKnowledgeBundleRun.user_id == user_id,
            RuntimeKnowledgeBundleRun.run_type.like("compile:%"),
            RuntimeKnowledgeBundleRun.created_at >= cooldown_cutoff,
        )
        .exists()
    )
    stmt = select(RuntimeSource).where(
        RuntimeSource.user_id == user_id,
        has_spans,
        ~has_concept_citations,
        ~recently_attempted,
    )
    if policy == "markdown_only":
        stmt = stmt.where(RuntimeSource.kind == "markdown")
    return list(db.scalars(stmt.order_by(RuntimeSource.id).limit(budget)).all())


def _source_payload(
    source: RuntimeSource,
    spans: Sequence[RuntimeSourceSpan],
) -> dict[str, object]:
    raw_title = _source_title(source)
    title = _limit_title(raw_title)
    kind_tag = _slugify(source.kind)
    summary_slug = _source_summary_slug(source, raw_title)
    concepts: list[dict[str, object]] = [
        {
            "type": "source_summary",
            "slug": summary_slug,
            "title": title,
            "description": f"Compiled source summary for {title}.",
            "body_markdown": _summary_body(source, spans),
            "source_span_ids": [span.id for span in spans],
            "tags": ["compiled", "source", kind_tag, "source_summary"],
        }
    ]
    links: list[dict[str, object]] = []
    for span in spans:
        span_title = _span_title(source, span)
        slug = _span_slug(source, span)
        concepts.append(
            {
                "type": "topic",
                "slug": slug,
                "title": span_title,
                "description": f"Source-backed section from {title}.",
                "body_markdown": _span_body(source, span, span_title),
                "source_span_ids": [span.id],
                "tags": ["compiled", "source", kind_tag, "source_span"],
            }
        )
        links.append(
            {
                "source_slug": slug,
                "target_slug": summary_slug,
                "link_type": "supports",
                "confidence": 1.0,
            }
        )
    return {
        "concepts": concepts,
        "links": links,
        "metadata": {
            "compiler": "runtime_source",
            "runtime_source_id": source.id,
            "source_kind": source.kind,
        },
    }


def _summary_body(
    source: RuntimeSource,
    spans: Sequence[RuntimeSourceSpan],
) -> str:
    title = _source_title(source)
    lines = [
        f"# {title}",
        "",
        f"Compiled {source.kind} knowledge source. Each section keeps citations to original source spans.",
        "",
    ]
    for span in spans:
        span_title = _span_title(source, span)
        location = _span_location(span)
        preview = _compact_text(span.content_text, limit=280)
        lines.append(f"## {span_title}{location}")
        lines.append(preview)
        lines.append("")
    return "\n".join(lines).strip()


def _span_body(
    source: RuntimeSource,
    span: RuntimeSourceSpan,
    title: str,
) -> str:
    lines = [
        f"# {title}",
        "",
        f"Source: {_source_title(source)}{_span_location(span)}.",
    ]
    lines.extend(["", span.content_text.strip()])
    return "\n".join(lines).strip()


def _span_title(source: RuntimeSource, span: RuntimeSourceSpan) -> str:
    metadata = span.metadata_json or {}
    for key in ("section_title", "heading", "annotation_kind"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return _limit_title(value.strip())
    span_index = _span_index(span)
    span_kind = span.span_kind.replace("_", " ").title()
    return _limit_title(f"{_source_title(source)} {span_kind} {span_index + 1}")


def _source_title(source: RuntimeSource) -> str:
    return source.title or source.source_uri or f"Source {source.id}"


def _source_summary_slug(source: RuntimeSource, title: str) -> str:
    return _limit_slug(f"source-{source.id}-{_slugify(title)}")


def _span_slug(source: RuntimeSource, span: RuntimeSourceSpan) -> str:
    return _limit_slug(
        f"source-{source.id}-span-{_slugify(span.span_kind)}-{span.id}"
    )


def _span_index(span: RuntimeSourceSpan) -> int:
    locator = span.locator_json or {}
    for key in ("chunk_index", "paragraph_index", "line_start", "row_start"):
        raw_index = locator.get(key)
        if isinstance(raw_index, int):
            return raw_index
    return span.id


def _span_location(span: RuntimeSourceSpan) -> str:
    locator = span.locator_json or {}
    page_start = locator.get("page_start")
    page_end = locator.get("page_end")
    if isinstance(page_start, int):
        if not isinstance(page_end, int) or page_end == page_start:
            return f" (page {page_start})"
        return f" (pages {page_start}-{page_end})"
    line_start = locator.get("line_start")
    line_end = locator.get("line_end")
    if isinstance(line_start, int):
        if not isinstance(line_end, int) or line_end == line_start:
            return f" (line {line_start})"
        return f" (lines {line_start}-{line_end})"
    row_start = locator.get("row_start")
    row_end = locator.get("row_end")
    if isinstance(row_start, int):
        if not isinstance(row_end, int) or row_end == row_start:
            return f" (row {row_start})"
        return f" (rows {row_start}-{row_end})"
    time_start = locator.get("time_start_ms")
    time_end = locator.get("time_end_ms")
    if isinstance(time_start, int):
        if not isinstance(time_end, int) or time_end == time_start:
            return f" ({time_start} ms)"
        return f" ({time_start}-{time_end} ms)"
    cell = locator.get("cell")
    if isinstance(cell, str) and cell.strip():
        return f" (cell {cell.strip()})"
    return ""


def _compact_text(value: str, *, limit: int) -> str:
    cleaned = " ".join(value.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "..."


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "document"


def _limit_slug(value: str) -> str:
    if len(value) <= _MAX_CONCEPT_SLUG_LENGTH:
        return value
    return value[:_MAX_CONCEPT_SLUG_LENGTH].rstrip("-") or "source"


def _limit_title(value: str) -> str:
    if len(value) <= _MAX_CONCEPT_TITLE_LENGTH:
        return value
    return value[:_MAX_CONCEPT_TITLE_LENGTH].rstrip()
