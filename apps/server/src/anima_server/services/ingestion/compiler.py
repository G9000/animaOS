from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from anima_server.models.runtime import (
    RuntimeKnowledgeConcept,
    RuntimeKnowledgeConceptSource,
    RuntimeKnowledgeLink,
    RuntimeSource,
    RuntimeSourceSpan,
)
from anima_server.services.agent.json_utils import parse_json_object
from anima_server.services.ingestion.sources import (
    complete_bundle_run,
    fail_bundle_run,
    start_bundle_run,
)

CompileMode = Literal["initial", "refresh", "repair"]


@dataclass(frozen=True, slots=True)
class CompilerRequest:
    user_id: int
    source: RuntimeSource
    spans: Sequence[RuntimeSourceSpan]
    mode: CompileMode
    selected_concept_ids: Sequence[int] | None = None


@dataclass(frozen=True, slots=True)
class CompileResult:
    status: str
    run_id: int
    concept_count: int = 0
    link_count: int = 0


ModelCompiler = Callable[[CompilerRequest], str]


def compile_source_to_concepts(
    db: Session,
    *,
    user_id: int,
    source_id: int,
    span_ids: Sequence[int],
    model: ModelCompiler,
    mode: CompileMode = "initial",
    selected_concept_ids: Sequence[int] | None = None,
) -> CompileResult:
    source = _get_source(db, user_id=user_id, source_id=source_id)
    spans = _get_spans(db, user_id=user_id, source_id=source.id, span_ids=span_ids)
    run = start_bundle_run(
        db,
        user_id=user_id,
        run_type=f"compile:{mode}",
        source_id=source.id,
        input_json={"span_ids": list(span_ids), "mode": mode},
    )

    try:
        request = CompilerRequest(
            user_id=user_id,
            source=source,
            spans=spans,
            mode=mode,
            selected_concept_ids=selected_concept_ids,
        )
        payload = _parse_model_payload(model(request))
        with db.begin_nested():
            concepts = _merge_concepts(
                db,
                user_id=user_id,
                source=source,
                spans_by_id={span.id: span for span in spans},
                concept_payloads=_require_list(payload, "concepts"),
            )
            link_count = _merge_links(
                db,
                user_id=user_id,
                concepts_by_slug={concept.slug: concept for concept in concepts},
                link_payloads=_optional_list(payload, "links"),
            )
    except Exception as exc:
        fail_bundle_run(db, run=run, exc=exc)
        return CompileResult(status="failed", run_id=run.id)

    complete_bundle_run(
        db,
        run=run,
        result_json={"concepts": len(concepts), "links": link_count},
    )
    return CompileResult(
        status="completed",
        run_id=run.id,
        concept_count=len(concepts),
        link_count=link_count,
    )


def _merge_concepts(
    db: Session,
    *,
    user_id: int,
    source: RuntimeSource,
    spans_by_id: dict[int, RuntimeSourceSpan],
    concept_payloads: list[Any],
) -> list[RuntimeKnowledgeConcept]:
    concepts: list[RuntimeKnowledgeConcept] = []
    now = datetime.now(UTC)
    for payload in concept_payloads:
        if not isinstance(payload, dict):
            raise ValueError("Compiler concept entries must be objects.")
        concept_type = _required_str(payload, "type")
        slug = _required_str(payload, "slug")
        title = _required_str(payload, "title")
        body_markdown = _required_str(payload, "body_markdown")
        description = _optional_str(payload, "description")
        tags = payload.get("tags") if isinstance(payload.get("tags"), list) else []
        concept = _find_merge_target(
            db,
            user_id=user_id,
            concept_type=concept_type,
            slug=slug,
            title=title,
            merge_confidence=_optional_float(payload, "merge_confidence"),
        )
        if concept is None:
            concept = RuntimeKnowledgeConcept(
                user_id=user_id,
                concept_type=concept_type,
                slug=slug,
                title=title,
                description=description,
                body_markdown=body_markdown,
                frontmatter_json={},
                content_hash=_content_hash(body_markdown),
                status="active",
            )
        concept.concept_type = concept_type
        concept.title = title
        concept.description = description
        concept.body_markdown = body_markdown
        concept.frontmatter_json = {
            "type": concept_type,
            "title": title,
            "description": description,
            "tags": tags,
            "anima": {
                "status": "active",
                "source_id": source.id,
                "source_count": len(payload.get("source_span_ids", []) or []),
            },
        }
        concept.metadata_json = {"compiled_from_source_id": source.id}
        concept.content_hash = _content_hash(body_markdown)
        concept.status = "active"
        concept.compiled_at = now
        concept.updated_at = now
        db.add(concept)
        db.flush()
        _replace_concept_sources(
            db,
            user_id=user_id,
            concept=concept,
            source=source,
            span_ids=_span_ids_from_payload(payload),
            spans_by_id=spans_by_id,
        )
        concepts.append(concept)
    return concepts


def _replace_concept_sources(
    db: Session,
    *,
    user_id: int,
    concept: RuntimeKnowledgeConcept,
    source: RuntimeSource,
    span_ids: list[int],
    spans_by_id: dict[int, RuntimeSourceSpan],
) -> None:
    db.execute(
        delete(RuntimeKnowledgeConceptSource).where(
            RuntimeKnowledgeConceptSource.user_id == user_id,
            RuntimeKnowledgeConceptSource.concept_id == concept.id,
            RuntimeKnowledgeConceptSource.source_id == source.id,
        )
    )
    citations: list[RuntimeKnowledgeConceptSource] = []
    for index, span_id in enumerate(span_ids, start=1):
        span = spans_by_id.get(span_id)
        if span is None:
            raise ValueError(f"Compiler referenced unknown span id {span_id}.")
        citations.append(
            RuntimeKnowledgeConceptSource(
                user_id=user_id,
                concept_id=concept.id,
                source_id=source.id,
                span_id=span.id,
                citation_label=f"S{index}",
                quote_text=span.content_text,
                metadata_json={"compiler": "llm_wiki"},
            )
        )
    db.add_all(citations)
    db.flush()


def _merge_links(
    db: Session,
    *,
    user_id: int,
    concepts_by_slug: dict[str, RuntimeKnowledgeConcept],
    link_payloads: list[Any],
) -> int:
    count = 0
    seen_links: set[tuple[int, int, str]] = set()
    for payload in link_payloads:
        if not isinstance(payload, dict):
            raise ValueError("Compiler link entries must be objects.")
        source = concepts_by_slug.get(_required_str(payload, "source_slug"))
        target = concepts_by_slug.get(_required_str(payload, "target_slug"))
        if source is None or target is None or source.id == target.id:
            continue
        link_type = _required_str(payload, "link_type")
        link_key = (source.id, target.id, link_type)
        if link_key in seen_links:
            continue
        seen_links.add(link_key)
        link = db.scalar(
            select(RuntimeKnowledgeLink).where(
                RuntimeKnowledgeLink.user_id == user_id,
                RuntimeKnowledgeLink.source_concept_id == source.id,
                RuntimeKnowledgeLink.target_concept_id == target.id,
                RuntimeKnowledgeLink.link_type == link_type,
            )
        )
        if link is None:
            link = RuntimeKnowledgeLink(
                user_id=user_id,
                source_concept_id=source.id,
                target_concept_id=target.id,
                link_type=link_type,
            )
        link.confidence = _optional_float(payload, "confidence")
        link.metadata_json = {"compiler": "llm_wiki"}
        link.updated_at = datetime.now(UTC)
        db.add(link)
        count += 1
    db.flush()
    return count


def _find_merge_target(
    db: Session,
    *,
    user_id: int,
    concept_type: str,
    slug: str,
    title: str,
    merge_confidence: float | None,
) -> RuntimeKnowledgeConcept | None:
    exact = db.scalar(
        select(RuntimeKnowledgeConcept).where(
            RuntimeKnowledgeConcept.user_id == user_id,
            RuntimeKnowledgeConcept.slug == slug,
        )
    )
    if exact is not None:
        return exact
    if merge_confidence is None or merge_confidence < 0.8:
        return None
    candidates = list(
        db.scalars(
            select(RuntimeKnowledgeConcept).where(
                RuntimeKnowledgeConcept.user_id == user_id,
                RuntimeKnowledgeConcept.concept_type == concept_type,
            )
        ).all()
    )
    normalized_title = _normalize_title(title)
    for candidate in candidates:
        if _normalize_title(candidate.title) == normalized_title:
            return candidate
    return None


def _parse_model_payload(raw: str) -> dict[str, Any]:
    payload = parse_json_object(raw)
    if payload is None:
        raise ValueError("Compiler model output was not a JSON object.")
    return payload


def _get_source(db: Session, *, user_id: int, source_id: int) -> RuntimeSource:
    source = db.scalar(
        select(RuntimeSource).where(
            RuntimeSource.id == source_id,
            RuntimeSource.user_id == user_id,
        )
    )
    if source is None:
        raise ValueError(f"Source {source_id} does not exist for user {user_id}.")
    return source


def _get_spans(
    db: Session,
    *,
    user_id: int,
    source_id: int,
    span_ids: Sequence[int],
) -> list[RuntimeSourceSpan]:
    spans = list(
        db.scalars(
            select(RuntimeSourceSpan).where(
                RuntimeSourceSpan.user_id == user_id,
                RuntimeSourceSpan.source_id == source_id,
                RuntimeSourceSpan.id.in_(list(span_ids)),
            )
        ).all()
    )
    if len(spans) != len(set(span_ids)):
        raise ValueError("One or more source spans do not exist for this user.")
    return spans


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Compiler output missing required string field {key!r}.")
    return value.strip()


def _optional_str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Compiler field {key!r} must be a string.")
    return value


def _optional_float(payload: dict[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, int | float):
        raise ValueError(f"Compiler field {key!r} must be numeric.")
    return float(value)


def _require_list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ValueError(f"Compiler output missing required list field {key!r}.")
    return value


def _optional_list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"Compiler field {key!r} must be a list.")
    return value


def _span_ids_from_payload(payload: dict[str, Any]) -> list[int]:
    value = payload.get("source_span_ids") or []
    if not isinstance(value, list) or not all(isinstance(item, int) for item in value):
        raise ValueError("Compiler field 'source_span_ids' must be a list of integers.")
    return _deduplicate_ints(value)


def _deduplicate_ints(values: Sequence[int]) -> list[int]:
    seen: set[int] = set()
    result: list[int] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", title.strip().casefold())


def _content_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
