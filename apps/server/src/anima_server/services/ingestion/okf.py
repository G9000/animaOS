from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import yaml
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from anima_server.models.runtime import RuntimeKnowledgeConcept, RuntimeKnowledgeLink

_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
_OKF_IMPORT_SOURCE = "okf_import"


@dataclass(frozen=True, slots=True)
class OKFExportResult:
    bundle_dir: Path
    concept_count: int


@dataclass(frozen=True, slots=True)
class OKFImportResult:
    concept_count: int
    link_count: int


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
            .where(RuntimeKnowledgeConcept.user_id == user_id)
            .order_by(RuntimeKnowledgeConcept.slug)
        ).all()
    )

    for concept in concepts:
        frontmatter = _frontmatter_for_export(concept)
        body = _ensure_trailing_newline(concept.body_markdown)
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
    concept_paths = sorted((bundle_dir / "concepts").glob("*.md"))
    imported_by_slug: dict[str, RuntimeKnowledgeConcept] = {}

    for path in concept_paths:
        frontmatter, body = _parse_markdown(path)
        slug = path.stem
        concept_type = str(frontmatter.get("type") or "note")
        title = str(frontmatter.get("title") or _title_from_slug(slug))
        description_value = frontmatter.get("description")
        description = str(description_value) if description_value is not None else None
        concept = _upsert_concept(
            db,
            user_id=user_id,
            slug=slug,
            concept_type=concept_type,
            title=title,
            description=description,
            body_markdown=body,
            frontmatter_json=frontmatter,
        )
        imported_by_slug[slug] = concept

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
    concept = db.scalar(
        select(RuntimeKnowledgeConcept).where(
            RuntimeKnowledgeConcept.user_id == user_id,
            RuntimeKnowledgeConcept.slug == slug,
        )
    )
    if concept is None:
        concept = RuntimeKnowledgeConcept(
            user_id=user_id,
            concept_type=concept_type,
            slug=slug,
            title=title,
            description=description,
            body_markdown=body_markdown,
            frontmatter_json=dict(frontmatter_json),
            content_hash=_content_hash(body_markdown),
            status="active",
        )
    else:
        concept.concept_type = concept_type
        concept.title = title
        concept.description = description
        concept.body_markdown = body_markdown
        concept.frontmatter_json = dict(frontmatter_json)
        concept.content_hash = _content_hash(body_markdown)
        concept.status = "active"
        concept.updated_at = datetime.now(UTC)
    db.add(concept)
    db.flush()
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
                RuntimeKnowledgeLink.metadata_json["source"].as_string()
                == _OKF_IMPORT_SOURCE,
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
    for target in _MARKDOWN_LINK_RE.findall(body_markdown):
        if "://" in target or target.startswith("#"):
            continue
        path = Path(target.split("#", maxsplit=1)[0])
        if path.suffix.lower() != ".md":
            continue
        slugs.append(path.stem)
    return slugs


def _frontmatter_for_export(concept: RuntimeKnowledgeConcept) -> dict[str, object]:
    frontmatter = dict(concept.frontmatter_json or {})
    frontmatter["type"] = str(frontmatter.get("type") or concept.concept_type)
    frontmatter["title"] = str(frontmatter.get("title") or concept.title)
    if concept.description and "description" not in frontmatter:
        frontmatter["description"] = concept.description
    return frontmatter


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
    _, yaml_text, body = text.split("---\n", maxsplit=2)
    parsed = yaml.safe_load(yaml_text) or {}
    if not isinstance(parsed, dict):
        raise ValueError(f"Invalid OKF frontmatter in {path}")
    return _json_safe_frontmatter(parsed), body.lstrip("\n")


def _json_safe_frontmatter(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): _json_safe_value(nested_value)
        for key, nested_value in value.items()
    }


def _json_safe_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {
            str(key): _json_safe_value(nested_value)
            for key, nested_value in value.items()
        }
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe_value(item) for item in value]
    return value


def _render_index(concepts: list[RuntimeKnowledgeConcept]) -> str:
    lines = ["# Index", ""]
    lines.extend(
        f"- [{concept.title}](concepts/{concept.slug}.md)" for concept in concepts
    )
    return "\n".join(lines).rstrip() + "\n"


def _render_log(concepts: list[RuntimeKnowledgeConcept]) -> str:
    timestamp = datetime.now(UTC).isoformat()
    return (
        "# Log\n\n"
        f"- {timestamp} - Exported {len(concepts)} OKF concept page(s).\n"
    )


def _title_from_slug(slug: str) -> str:
    return slug.replace("-", " ").strip().title()


def _ensure_trailing_newline(value: str) -> str:
    return value if value.endswith("\n") else f"{value}\n"


def _content_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
