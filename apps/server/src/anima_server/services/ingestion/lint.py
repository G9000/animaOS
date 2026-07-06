from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from anima_server.models.runtime import (
    RuntimeKnowledgeConcept,
    RuntimeKnowledgeConceptSource,
    RuntimeKnowledgeLink,
    RuntimeSource,
)


@dataclass(frozen=True, slots=True)
class KnowledgeLintFinding:
    code: str
    severity: str
    message: str
    concept_id: int | None = None
    source_id: int | None = None
    link_id: int | None = None


def lint_knowledge_bundle(
    db: Session,
    *,
    user_id: int,
    source_id: int | None = None,
    concept_id: int | None = None,
) -> list[KnowledgeLintFinding]:
    findings: list[KnowledgeLintFinding] = []
    concepts = _scoped_concepts(db, user_id=user_id, concept_id=concept_id)
    concept_ids = [concept.id for concept in concepts]

    findings.extend(_uncited_claims(db, concepts=concepts))
    findings.extend(_stale_concepts(concepts))
    findings.extend(_duplicate_titles(db, user_id=user_id, concept_ids=concept_ids))
    findings.extend(_broken_links(db, user_id=user_id, concept_ids=concept_ids))
    if concept_id is None or source_id is not None:
        findings.extend(
            _orphan_sources(
                db,
                user_id=user_id,
                source_id=source_id,
                concept_scope=set(concept_ids) if concept_id is not None else None,
            )
        )
    return findings


def _scoped_concepts(
    db: Session,
    *,
    user_id: int,
    concept_id: int | None,
) -> list[RuntimeKnowledgeConcept]:
    stmt = select(RuntimeKnowledgeConcept).where(
        RuntimeKnowledgeConcept.user_id == user_id,
        RuntimeKnowledgeConcept.status == "active",
    )
    if concept_id is not None:
        stmt = stmt.where(RuntimeKnowledgeConcept.id == concept_id)
    return list(db.scalars(stmt.order_by(RuntimeKnowledgeConcept.id)).all())


def _uncited_claims(
    db: Session,
    *,
    concepts: list[RuntimeKnowledgeConcept],
) -> list[KnowledgeLintFinding]:
    findings: list[KnowledgeLintFinding] = []
    for concept in concepts:
        if concept.concept_type != "claim":
            continue
        has_source = db.scalar(
            select(RuntimeKnowledgeConceptSource.id).where(
                RuntimeKnowledgeConceptSource.user_id == concept.user_id,
                RuntimeKnowledgeConceptSource.concept_id == concept.id,
            )
        )
        if has_source is None:
            findings.append(
                KnowledgeLintFinding(
                    code="uncited_claim",
                    severity="warning",
                    concept_id=concept.id,
                    message=f"Claim concept {concept.slug} has no source citation.",
                )
            )
    return findings


def _stale_concepts(
    concepts: list[RuntimeKnowledgeConcept],
) -> list[KnowledgeLintFinding]:
    findings: list[KnowledgeLintFinding] = []
    for concept in concepts:
        if concept.content_hash != _content_hash(concept.body_markdown):
            findings.append(
                KnowledgeLintFinding(
                    code="stale_concept_hash",
                    severity="warning",
                    concept_id=concept.id,
                    message=f"Concept {concept.slug} content hash is stale.",
                )
            )
    return findings


def _content_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _duplicate_titles(
    db: Session,
    *,
    user_id: int,
    concept_ids: list[int],
) -> list[KnowledgeLintFinding]:
    if not concept_ids:
        return []
    duplicate_titles = {
        title
        for title, count in db.execute(
            select(RuntimeKnowledgeConcept.title, func.count(RuntimeKnowledgeConcept.id))
            .where(
                RuntimeKnowledgeConcept.user_id == user_id,
                RuntimeKnowledgeConcept.status == "active",
            )
            .group_by(RuntimeKnowledgeConcept.title)
            .having(func.count(RuntimeKnowledgeConcept.id) > 1)
        ).all()
    }
    if not duplicate_titles:
        return []
    concepts = db.scalars(
        select(RuntimeKnowledgeConcept).where(
            RuntimeKnowledgeConcept.id.in_(concept_ids),
            RuntimeKnowledgeConcept.title.in_(duplicate_titles),
        )
    ).all()
    return [
        KnowledgeLintFinding(
            code="duplicate_concept_title",
            severity="warning",
            concept_id=concept.id,
            message=f"Concept title {concept.title!r} is duplicated.",
        )
        for concept in concepts
    ]


def _broken_links(
    db: Session,
    *,
    user_id: int,
    concept_ids: list[int],
) -> list[KnowledgeLintFinding]:
    if not concept_ids:
        return []
    valid_ids = set(
        db.scalars(
            select(RuntimeKnowledgeConcept.id).where(
                RuntimeKnowledgeConcept.user_id == user_id,
                RuntimeKnowledgeConcept.status == "active",
            )
        ).all()
    )
    links = db.scalars(
        select(RuntimeKnowledgeLink).where(
            RuntimeKnowledgeLink.user_id == user_id,
            RuntimeKnowledgeLink.source_concept_id.in_(concept_ids),
        )
    ).all()
    return [
        KnowledgeLintFinding(
            code="broken_concept_link",
            severity="error",
            concept_id=link.source_concept_id,
            link_id=link.id,
            message=f"Knowledge link {link.id} points to missing concept.",
        )
        for link in links
        if link.target_concept_id not in valid_ids
    ]


def _orphan_sources(
    db: Session,
    *,
    user_id: int,
    source_id: int | None,
    concept_scope: set[int] | None,
) -> list[KnowledgeLintFinding]:
    stmt = select(RuntimeSource).where(RuntimeSource.user_id == user_id)
    if source_id is not None:
        stmt = stmt.where(RuntimeSource.id == source_id)
    sources = list(db.scalars(stmt.order_by(RuntimeSource.id)).all())
    findings: list[KnowledgeLintFinding] = []
    for source in sources:
        linked_concept_ids = set(
            db.scalars(
                select(RuntimeKnowledgeConceptSource.concept_id).where(
                    RuntimeKnowledgeConceptSource.user_id == user_id,
                    RuntimeKnowledgeConceptSource.source_id == source.id,
                )
            ).all()
        )
        if concept_scope is not None:
            linked_concept_ids &= concept_scope
        if not linked_concept_ids:
            findings.append(
                KnowledgeLintFinding(
                    code="orphan_source",
                    severity="info",
                    source_id=source.id,
                    message=f"Source {source.source_uri} has no compiled concept.",
                )
            )
    return findings
