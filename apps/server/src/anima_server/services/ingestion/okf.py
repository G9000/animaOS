from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from anima_server.models.runtime import (
    RuntimeKnowledgeConcept,
    RuntimeKnowledgeConceptSource,
    RuntimeKnowledgeLink,
    RuntimeSource,
    RuntimeSourceSpan,
)
from anima_server.services.corefs.sealed_runtime import (
    runtime_private_lookup_value,
    seal_runtime_fields,
)

_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_OKF_IMPORT_SOURCE = "okf_import"
_SOURCE_REFERENCES_HEADING = "## Source References"


@dataclass(frozen=True, slots=True)
class OKFExportResult:
    bundle_dir: Path
    concept_count: int


@dataclass(frozen=True, slots=True)
class OKFImportResult:
    concept_count: int
    link_count: int


@dataclass(frozen=True, slots=True)
class PortableOKFConcept:
    slug: str
    concept_type: str
    title: str
    description: str | None
    body_markdown: str
    frontmatter_json: dict[str, object]
    original_markdown: str
    linked_slugs: tuple[str, ...]


def read_portable_okf_bundle(*, bundle_dir: Path) -> tuple[PortableOKFConcept, ...]:
    concepts: list[PortableOKFConcept] = []
    seen_slugs: set[str] = set()
    for path in sorted((bundle_dir / "concepts").glob("*.md")):
        slug = path.stem
        _concept_markdown_path(bundle_dir / "concepts", slug)
        if slug in seen_slugs:
            raise ValueError(f"Duplicate OKF concept slug: {slug!r}")
        seen_slugs.add(slug)
        original = path.read_text(encoding="utf-8")
        frontmatter, body = _parse_markdown(path)
        body = _strip_generated_source_references(body, frontmatter)
        frontmatter = _frontmatter_for_import(frontmatter)
        description_value = frontmatter.get("description")
        concepts.append(
            PortableOKFConcept(
                slug=slug,
                concept_type=str(frontmatter.get("type") or "note"),
                title=str(frontmatter.get("title") or _title_from_slug(slug)),
                description=(
                    str(description_value) if description_value is not None else None
                ),
                body_markdown=body,
                frontmatter_json=frontmatter,
                original_markdown=original,
                linked_slugs=tuple(_extract_relative_link_slugs(body)),
            )
        )
    return tuple(concepts)


def export_portable_okf_bundle(
    *,
    concepts: tuple[PortableOKFConcept, ...],
    bundle_dir: Path,
) -> OKFExportResult:
    concepts_dir = bundle_dir / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    ordered = sorted(concepts, key=lambda concept: concept.slug)
    exported_slugs = {concept.slug for concept in ordered}
    if len(exported_slugs) != len(ordered):
        raise ValueError("Canonical OKF export contains duplicate slugs.")
    for concept in ordered:
        frontmatter = dict(concept.frontmatter_json)
        frontmatter["type"] = concept.concept_type
        frontmatter["title"] = concept.title
        if concept.description:
            frontmatter["description"] = concept.description
        body = _unlink_absent_bundle_targets(concept.body_markdown, exported_slugs)
        _concept_markdown_path(concepts_dir, concept.slug).write_text(
            _render_markdown(frontmatter, body),
            encoding="utf-8",
        )
    (bundle_dir / "index.md").write_text(
        _render_index(ordered),
        encoding="utf-8",
    )
    (bundle_dir / "log.md").write_text(_render_log(ordered), encoding="utf-8")
    return OKFExportResult(bundle_dir=bundle_dir, concept_count=len(ordered))


def export_okf_bundle(
    db: Session,
    *,
    user_id: int,
    bundle_dir: Path,
) -> OKFExportResult:
    concepts_dir = bundle_dir / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)

    concepts = list(
        db.scalars(
            select(RuntimeKnowledgeConcept)
            .where(
                RuntimeKnowledgeConcept.user_id == user_id,
                # A refresh compile retires superseded pages as "inactive";
                # retrieval and lint scope to "active", so the exported bundle
                # must too or retired pages reappear on every export.
                RuntimeKnowledgeConcept.status == "active",
            )
            .order_by(RuntimeKnowledgeConcept.slug)
        ).all()
    )

    exported_slugs = {concept.slug for concept in concepts}
    for concept in concepts:
        frontmatter = _frontmatter_for_export(concept)
        citations = _citation_records_for_export(
            db,
            user_id=user_id,
            concept_id=concept.id,
        )
        if citations:
            frontmatter["x_anima_citations"] = citations
        body = _ensure_trailing_newline(
            _unlink_absent_bundle_targets(concept.body_markdown, exported_slugs)
        )
        if citations:
            body = _append_source_references(body, citations)
        _concept_markdown_path(concepts_dir, concept.slug).write_text(
            _render_markdown(frontmatter, body),
            encoding="utf-8",
        )

    (bundle_dir / "index.md").write_text(_render_index(concepts), encoding="utf-8")
    (bundle_dir / "log.md").write_text(_render_log(concepts), encoding="utf-8")
    return OKFExportResult(bundle_dir=bundle_dir, concept_count=len(concepts))


def import_okf_bundle(
    db: Session,
    *,
    user_id: int,
    bundle_dir: Path,
) -> OKFImportResult:
    imported_by_slug: dict[str, RuntimeKnowledgeConcept] = {}

    for portable in read_portable_okf_bundle(bundle_dir=bundle_dir):
        concept = _upsert_concept(
            db,
            user_id=user_id,
            slug=portable.slug,
            concept_type=portable.concept_type,
            title=portable.title,
            description=portable.description,
            body_markdown=portable.body_markdown,
            frontmatter_json=portable.frontmatter_json,
        )
        imported_by_slug[portable.slug] = concept

    db.flush()
    link_count = _replace_imported_links(db, user_id=user_id, concepts=imported_by_slug)
    return OKFImportResult(concept_count=len(imported_by_slug), link_count=link_count)


def _upsert_concept(
    db: Session,
    *,
    user_id: int,
    slug: str,
    concept_type: str,
    title: str,
    description: str | None,
    body_markdown: str,
    frontmatter_json: dict[str, object],
) -> RuntimeKnowledgeConcept:
    slug_lookup = runtime_private_lookup_value(
        db,
        owner_id=user_id,
        value=slug,
    )
    concept_type_lookup = runtime_private_lookup_value(
        db,
        owner_id=user_id,
        value=concept_type,
        max_length=48,
    )
    concept = db.scalar(
        select(RuntimeKnowledgeConcept).where(
            RuntimeKnowledgeConcept.user_id == user_id,
            RuntimeKnowledgeConcept.slug == slug_lookup,
        )
    )
    if concept is None:
        concept = RuntimeKnowledgeConcept(
            user_id=user_id,
            concept_type=concept_type_lookup,
            slug=slug_lookup,
            title="",
            description=None,
            body_markdown="",
            frontmatter_json={},
            content_hash=_content_hash(body_markdown),
            status="active",
        )
    else:
        concept.concept_type = concept_type_lookup
        concept.content_hash = _content_hash(body_markdown)
        concept.status = "active"
        concept.updated_at = datetime.now(UTC)
    seal_runtime_fields(
        db,
        row=concept,
        row_type="runtime_knowledge_concept",
        owner_id=user_id,
        payload={
            "concept_type": concept_type,
            "slug": slug,
            "title": title,
            "description": description,
            "body_markdown": body_markdown,
            "frontmatter_json": dict(frontmatter_json),
        },
        placeholders={
            "concept_type": concept_type_lookup,
            "slug": slug_lookup,
            "title": "",
            "description": None,
            "body_markdown": "",
            "frontmatter_json": {},
        },
    )
    return concept


def _replace_imported_links(
    db: Session,
    *,
    user_id: int,
    concepts: dict[str, RuntimeKnowledgeConcept],
) -> int:
    concept_ids = [concept.id for concept in concepts.values()]
    if concept_ids:
        db.execute(
            delete(RuntimeKnowledgeLink).where(
                RuntimeKnowledgeLink.user_id == user_id,
                RuntimeKnowledgeLink.source_concept_id.in_(concept_ids),
                RuntimeKnowledgeLink.metadata_json["source"].as_string() == _OKF_IMPORT_SOURCE,
            )
        )
        db.flush()

    link_count = 0
    links: list[RuntimeKnowledgeLink] = []
    seen_links: set[tuple[int, int, str]] = set()
    for source in concepts.values():
        for target_slug in _extract_relative_link_slugs(source.body_markdown):
            target = concepts.get(target_slug)
            if target is None or target.id == source.id:
                continue
            link_key = (source.id, target.id, "related")
            if link_key in seen_links:
                continue
            seen_links.add(link_key)
            if _find_existing_link(db, user_id=user_id, link_key=link_key) is not None:
                link_count += 1
                continue
            links.append(
                RuntimeKnowledgeLink(
                    user_id=user_id,
                    source_concept_id=source.id,
                    target_concept_id=target.id,
                    link_type="related",
                    confidence=1.0,
                    metadata_json={"source": _OKF_IMPORT_SOURCE},
                )
            )
            link_count += 1
    db.add_all(links)
    db.flush()
    return link_count


def _find_existing_link(
    db: Session,
    *,
    user_id: int,
    link_key: tuple[int, int, str],
) -> RuntimeKnowledgeLink | None:
    source_concept_id, target_concept_id, link_type = link_key
    return db.scalar(
        select(RuntimeKnowledgeLink).where(
            RuntimeKnowledgeLink.user_id == user_id,
            RuntimeKnowledgeLink.source_concept_id == source_concept_id,
            RuntimeKnowledgeLink.target_concept_id == target_concept_id,
            RuntimeKnowledgeLink.link_type == link_type,
        )
    )


def _extract_relative_link_slugs(body_markdown: str) -> list[str]:
    slugs: list[str] = []
    for _text, target in _MARKDOWN_LINK_RE.findall(body_markdown):
        slug = _relative_link_slug(target)
        if slug is not None:
            slugs.append(slug)
    return slugs


def _relative_link_slug(target: str) -> str | None:
    """The concept slug a bundle-relative markdown link points at, if any."""
    if "://" in target or target.startswith("#"):
        return None
    path = Path(target.split("#", maxsplit=1)[0])
    if path.suffix.lower() != ".md":
        return None
    return path.stem


def _unlink_absent_bundle_targets(body_markdown: str, exported_slugs: set[str]) -> str:
    """Drop bundle-relative links whose target is not part of this bundle.

    Retired concepts are not exported, so a link to one would dangle in the
    bundle and be silently discarded on re-import (`_replace_imported_links`
    skips unknown targets). Keep the visible text and remove only the broken
    hyperlink, so an exported bundle never carries a dangling concept link.
    """

    def _replace(match: re.Match[str]) -> str:
        text, target = match.group(1), match.group(2)
        slug = _relative_link_slug(target)
        if slug is None or slug in exported_slugs:
            return match.group(0)
        return text

    return _MARKDOWN_LINK_RE.sub(_replace, body_markdown)


def _frontmatter_for_export(concept: RuntimeKnowledgeConcept) -> dict[str, object]:
    frontmatter = dict(concept.frontmatter_json or {})
    frontmatter["type"] = str(frontmatter.get("type") or concept.concept_type)
    frontmatter["title"] = str(frontmatter.get("title") or concept.title)
    if concept.description and "description" not in frontmatter:
        frontmatter["description"] = concept.description
    return frontmatter


def _citation_records_for_export(
    db: Session,
    *,
    user_id: int,
    concept_id: int,
) -> list[dict[str, object]]:
    rows = list(
        db.execute(
            select(RuntimeKnowledgeConceptSource, RuntimeSource, RuntimeSourceSpan)
            .join(
                RuntimeSource,
                RuntimeKnowledgeConceptSource.source_id == RuntimeSource.id,
            )
            .join(
                RuntimeSourceSpan,
                RuntimeKnowledgeConceptSource.span_id == RuntimeSourceSpan.id,
            )
            .where(
                RuntimeKnowledgeConceptSource.user_id == user_id,
                RuntimeKnowledgeConceptSource.concept_id == concept_id,
                RuntimeSource.user_id == user_id,
                RuntimeSourceSpan.user_id == user_id,
            )
            .order_by(
                RuntimeKnowledgeConceptSource.created_at,
                RuntimeKnowledgeConceptSource.id,
            )
        ).all()
    )
    return [
        {
            "citation_label": citation.citation_label or f"S{index}",
            "source_uri": source.source_uri,
            "source_title": source.title,
            "source_kind": source.kind,
            "span_kind": span.span_kind,
            "locator": _json_safe_value(span.locator_json),
            "quote_text": citation.quote_text or span.content_text,
            "metadata": _json_safe_value(citation.metadata_json or {}),
        }
        for index, (citation, source, span) in enumerate(rows, start=1)
    ]


def _append_source_references(
    body_markdown: str,
    citations: list[dict[str, object]],
) -> str:
    lines = [
        _ensure_trailing_newline(body_markdown).rstrip(),
        "",
        _SOURCE_REFERENCES_HEADING,
        "",
    ]
    for citation in citations:
        label = str(citation.get("citation_label") or "S?")
        source_uri = str(citation.get("source_uri") or "")
        source_title = str(citation.get("source_title") or source_uri or "Untitled source")
        lines.append(f"- [{label}] {source_title} ({source_uri})")
        quote = str(citation.get("quote_text") or "").strip()
        if quote:
            for quote_line in quote.splitlines():
                lines.append(f"  > {quote_line}")
    return "\n".join(lines).rstrip() + "\n"


def _strip_generated_source_references(
    body_markdown: str,
    frontmatter: dict[str, object],
) -> str:
    if not frontmatter.get("x_anima_citations"):
        return body_markdown
    marker = f"\n{_SOURCE_REFERENCES_HEADING}\n"
    stripped = body_markdown.rstrip()
    if stripped == _SOURCE_REFERENCES_HEADING or stripped.startswith(
        f"{_SOURCE_REFERENCES_HEADING}\n"
    ):
        return ""
    marker_index = stripped.rfind(marker)
    if marker_index == -1:
        return body_markdown
    return stripped[:marker_index].rstrip() + "\n"


def _frontmatter_for_import(frontmatter: dict[str, object]) -> dict[str, object]:
    imported = dict(frontmatter)
    imported.pop("x_anima_citations", None)
    return imported


def _concept_markdown_path(concepts_dir: Path, slug: str) -> Path:
    candidate = (concepts_dir / f"{slug}.md").resolve()
    if not slug or slug != slug.strip() or candidate.parent != concepts_dir.resolve():
        raise ValueError(f"Unsafe OKF concept slug: {slug!r}")
    return candidate


def _render_markdown(frontmatter: dict[str, object], body_markdown: str) -> str:
    yaml_text = yaml.safe_dump(
        frontmatter,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    return f"---\n{yaml_text}---\n\n{_ensure_trailing_newline(body_markdown)}"


def _parse_markdown(path: Path) -> tuple[dict[str, object], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {"type": "note", "title": _title_from_slug(path.stem)}, text
    parts = text.split("---\n", maxsplit=2)
    if len(parts) < 3:
        # The import route reports ValueError as the 422 detail; without this
        # the caller would get a tuple-unpacking message instead.
        raise ValueError(f"Unterminated OKF frontmatter in {path}")
    _, yaml_text, body = parts
    parsed = yaml.safe_load(yaml_text) or {}
    if not isinstance(parsed, dict):
        raise ValueError(f"Invalid OKF frontmatter in {path}")
    return _json_safe_frontmatter(parsed), body.lstrip("\n")


def _json_safe_frontmatter(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): _json_safe_value(nested_value) for key, nested_value in value.items()}


def _json_safe_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe_value(nested_value) for key, nested_value in value.items()}
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe_value(item) for item in value]
    return value


def _render_index(concepts: list[Any]) -> str:
    lines = ["# Index", ""]
    lines.extend(f"- [{concept.title}](concepts/{concept.slug}.md)" for concept in concepts)
    return "\n".join(lines).rstrip() + "\n"


def _render_log(concepts: list[Any]) -> str:
    timestamp = datetime.now(UTC).isoformat()
    return f"# Log\n\n- {timestamp} - Exported {len(concepts)} OKF concept page(s).\n"


def _title_from_slug(slug: str) -> str:
    return slug.replace("-", " ").strip().title()


def _ensure_trailing_newline(value: str) -> str:
    return value if value.endswith("\n") else f"{value}\n"


def _content_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
