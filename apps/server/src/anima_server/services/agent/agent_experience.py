"""Procedural experience and learned skill memory."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from anima_server.models import AgentExperience, AgentSkill, ExperienceClusterState
from anima_server.services.agent.embeddings import cosine_similarity
from anima_server.services.data_crypto import df, ef

EXPERIENCE_CLUSTER_SIMILARITY_THRESHOLD = 0.75
EXPERIENCE_CLUSTER_MAX_GAP_DAYS = 90
MIN_EXPERIENCES_FOR_SKILL = 3
SKILL_CONFIDENCE_PROMPT_THRESHOLD = 0.7
PROCEDURAL_SIMILARITY_THRESHOLD = 0.6


@dataclass(frozen=True, slots=True)
class AgentExperienceCandidate:
    task_intent: str
    approach: str
    quality_score: float
    source_thread_id: int | None = None
    source_run_id: int | None = None
    tool_names: Sequence[str] = ()
    turn_count: int = 1
    embedding: list[float] | None = None
    cluster_id: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AgentSkillCandidate:
    cluster_id: str
    name: str
    description: str
    content: str
    confidence: float
    experience_count: int
    embedding: list[float] | None = None


@dataclass(frozen=True, slots=True)
class RetrievedExperience:
    experience: AgentExperience
    similarity: float


@dataclass(frozen=True, slots=True)
class RetrievedSkill:
    skill: AgentSkill
    similarity: float


def store_agent_experience(
    db: Session,
    *,
    user_id: int,
    candidate: AgentExperienceCandidate,
) -> AgentExperience:
    task_intent = _clean_text(candidate.task_intent)
    approach = _clean_text(candidate.approach)
    if not task_intent or not approach:
        raise ValueError("agent experience requires task_intent and approach")

    created_at = candidate.created_at or datetime.now(UTC)
    row = AgentExperience(
        user_id=user_id,
        task_intent=ef(
            user_id,
            task_intent,
            table="agent_experiences",
            field="task_intent",
        ),
        approach=ef(user_id, approach, table="agent_experiences", field="approach"),
        quality_score=_clamp(candidate.quality_score),
        source_thread_id=candidate.source_thread_id,
        source_run_id=candidate.source_run_id,
        tool_names_json=_clean_tool_names(candidate.tool_names),
        turn_count=max(1, int(candidate.turn_count or 1)),
        embedding_json=list(candidate.embedding) if candidate.embedding is not None else None,
        cluster_id=candidate.cluster_id,
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(row)
    db.flush()
    _supersede_lower_quality_duplicate(db, user_id=user_id, experience=row)
    _append_experience_growth_log(db, user_id=user_id, task_intent=task_intent, quality=row.quality_score)
    return row


def assign_experience_to_cluster(
    db: Session,
    *,
    user_id: int,
    experience: AgentExperience,
    similarity_threshold: float = EXPERIENCE_CLUSTER_SIMILARITY_THRESHOLD,
    max_time_gap_days: int = EXPERIENCE_CLUSTER_MAX_GAP_DAYS,
) -> str | None:
    embedding = _valid_embedding(experience.embedding_json)
    if embedding is None:
        experience.cluster_id = None
        db.flush()
        return None

    state_row = _get_or_create_cluster_state(db, user_id=user_id)
    state = _normalize_state(state_row.state_json)
    timestamp = experience.created_at or datetime.now(UTC)
    cutoff = timestamp - timedelta(days=max_time_gap_days)

    best_id: str | None = None
    best_similarity = -1.0
    clusters = state["clusters"]
    for cluster_id, cluster in clusters.items():
        last_activity = _parse_datetime(cluster.get("last_activity"))
        if last_activity is not None and last_activity < cutoff:
            continue
        centroid = _valid_embedding(cluster.get("centroid"))
        if centroid is None:
            continue
        similarity = cosine_similarity(embedding, centroid)
        if similarity > best_similarity:
            best_similarity = similarity
            best_id = cluster_id

    if best_id is None or best_similarity < similarity_threshold:
        next_index = int(state.get("next_index", 0))
        best_id = _new_cluster_id(user_id, next_index)
        state["next_index"] = next_index + 1
        clusters[best_id] = {
            "centroid": list(embedding),
            "count": 0,
            "last_activity": timestamp.isoformat(),
            "experience_ids": [],
        }

    cluster = clusters[best_id]
    count = int(cluster.get("count", 0))
    centroid = _valid_embedding(cluster.get("centroid")) or list(embedding)
    cluster["centroid"] = _running_centroid(centroid, count, embedding)
    cluster["count"] = count + 1
    cluster["last_activity"] = timestamp.isoformat()
    experience_ids = [int(item) for item in cluster.get("experience_ids", [])]
    if experience.id not in experience_ids:
        experience_ids.append(int(experience.id))
    cluster["experience_ids"] = experience_ids
    state_row.state_json = state
    flag_modified(state_row, "state_json")
    state_row.updated_at = datetime.now(UTC)
    experience.cluster_id = best_id
    db.flush()
    return best_id


def maybe_distill_skill_for_cluster(
    db: Session,
    *,
    user_id: int,
    cluster_id: str,
    min_experiences: int = MIN_EXPERIENCES_FOR_SKILL,
    distiller: Callable[[Sequence[AgentExperience]], AgentSkillCandidate] | None = None,
) -> AgentSkill | None:
    experiences = list(_cluster_experiences(db, user_id=user_id, cluster_id=cluster_id))
    if len(experiences) < min_experiences:
        return None

    candidate = (
        distiller(experiences)
        if distiller is not None
        else _default_skill_candidate(user_id=user_id, cluster_id=cluster_id, experiences=experiences)
    )
    return upsert_agent_skill(db, user_id=user_id, skill=candidate)


def upsert_agent_skill(
    db: Session,
    *,
    user_id: int,
    skill: AgentSkillCandidate,
) -> AgentSkill:
    now = datetime.now(UTC)
    existing = list(
        db.scalars(
            select(AgentSkill)
            .where(
                AgentSkill.user_id == user_id,
                AgentSkill.cluster_id == skill.cluster_id,
                AgentSkill.superseded_by.is_(None),
            )
            .order_by(AgentSkill.confidence.desc(), AgentSkill.id.asc())
        ).all()
    )
    row = AgentSkill(
        user_id=user_id,
        cluster_id=skill.cluster_id,
        name=ef(user_id, _clean_text(skill.name), table="agent_skills", field="name"),
        description=ef(
            user_id,
            _clean_text(skill.description),
            table="agent_skills",
            field="description",
        ),
        content=ef(user_id, _clean_text(skill.content), table="agent_skills", field="content"),
        confidence=_clamp(skill.confidence),
        experience_count=max(0, int(skill.experience_count)),
        last_refined_at=now,
        embedding_json=list(skill.embedding) if skill.embedding is not None else None,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    for old in existing:
        old.superseded_by = row.id
        old.updated_at = now
    _append_skill_growth_log(db, user_id=user_id, skill=row)
    db.flush()
    return row


def retrieve_agent_skills(
    db: Session,
    *,
    user_id: int,
    query_embedding: list[float] | None,
    limit: int = 2,
    min_confidence: float = SKILL_CONFIDENCE_PROMPT_THRESHOLD,
    similarity_threshold: float = PROCEDURAL_SIMILARITY_THRESHOLD,
) -> tuple[RetrievedSkill, ...]:
    if query_embedding is None:
        return ()
    rows = list(
        db.scalars(
            select(AgentSkill)
            .where(
                AgentSkill.user_id == user_id,
                AgentSkill.superseded_by.is_(None),
                AgentSkill.confidence >= min_confidence,
            )
            .order_by(AgentSkill.confidence.desc(), AgentSkill.updated_at.desc())
        ).all()
    )
    ranked = [
        RetrievedSkill(skill=row, similarity=cosine_similarity(query_embedding, embedding))
        for row in rows
        if (embedding := _valid_embedding(row.embedding_json)) is not None
    ]
    ranked = [item for item in ranked if item.similarity >= similarity_threshold]
    ranked.sort(key=lambda item: (item.skill.confidence, item.similarity), reverse=True)
    return tuple(ranked[:limit])


def retrieve_agent_experiences(
    db: Session,
    *,
    user_id: int,
    query_embedding: list[float] | None,
    limit: int = 3,
    similarity_threshold: float = PROCEDURAL_SIMILARITY_THRESHOLD,
) -> tuple[RetrievedExperience, ...]:
    if query_embedding is None:
        return ()
    rows = list(
        db.scalars(
            select(AgentExperience)
            .where(
                AgentExperience.user_id == user_id,
                AgentExperience.superseded_by.is_(None),
            )
            .order_by(AgentExperience.quality_score.desc(), AgentExperience.updated_at.desc())
        ).all()
    )
    ranked = [
        RetrievedExperience(
            experience=row,
            similarity=cosine_similarity(query_embedding, embedding),
        )
        for row in rows
        if (embedding := _valid_embedding(row.embedding_json)) is not None
    ]
    ranked = [item for item in ranked if item.similarity >= similarity_threshold]
    ranked.sort(key=lambda item: (item.similarity, item.experience.quality_score), reverse=True)
    return tuple(ranked[:limit])


def has_matching_high_confidence_skill(
    db: Session,
    *,
    user_id: int,
    query_embedding: list[float] | None,
) -> bool:
    return bool(
        retrieve_agent_skills(
            db,
            user_id=user_id,
            query_embedding=query_embedding,
            limit=1,
        )
    )


def _cluster_experiences(
    db: Session,
    *,
    user_id: int,
    cluster_id: str,
) -> tuple[AgentExperience, ...]:
    return tuple(
        db.scalars(
            select(AgentExperience)
            .where(
                AgentExperience.user_id == user_id,
                AgentExperience.cluster_id == cluster_id,
                AgentExperience.superseded_by.is_(None),
            )
            .order_by(AgentExperience.created_at.asc(), AgentExperience.id.asc())
        ).all()
    )


def _get_or_create_cluster_state(
    db: Session,
    *,
    user_id: int,
) -> ExperienceClusterState:
    row = db.scalar(
        select(ExperienceClusterState).where(ExperienceClusterState.user_id == user_id)
    )
    if row is not None:
        return row
    row = ExperienceClusterState(
        user_id=user_id,
        state_json={"next_index": 0, "clusters": {}},
    )
    db.add(row)
    db.flush()
    return row


def _normalize_state(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {"next_index": 0, "clusters": {}}
    clusters = value.get("clusters")
    if not isinstance(clusters, dict):
        clusters = {}
    next_index = int(value.get("next_index", len(clusters)))
    return {"next_index": next_index, "clusters": clusters}


def _new_cluster_id(user_id: int, index: int) -> str:
    return f"cluster_{user_id}_{index:03d}"


def _running_centroid(
    centroid: list[float],
    count: int,
    embedding: list[float],
) -> list[float]:
    if count <= 0 or len(centroid) != len(embedding):
        return list(embedding)
    return [
        ((centroid[idx] * count) + embedding[idx]) / (count + 1)
        for idx in range(len(embedding))
    ]


def _default_skill_candidate(
    *,
    user_id: int,
    cluster_id: str,
    experiences: Sequence[AgentExperience],
) -> AgentSkillCandidate:
    best = max(experiences, key=lambda item: item.quality_score)
    best_intent = df(
        user_id,
        best.task_intent,
        table="agent_experiences",
        field="task_intent",
    )
    high_quality = [item for item in experiences if item.quality_score >= 0.7]
    low_quality = [item for item in experiences if item.quality_score < 0.5]
    name = _skill_name_from_intent(best_intent)
    steps = _distill_steps(user_id=user_id, experiences=high_quality or experiences)
    pitfalls = _distill_pitfalls(user_id=user_id, experiences=low_quality)
    content = "\n".join(steps)
    if pitfalls:
        content = f"{content}\n\nPitfalls:\n" + "\n".join(f"- {pitfall}" for pitfall in pitfalls)
    confidence = min(0.95, 0.45 + (len(experiences) * 0.08) + _avg_quality(high_quality) * 0.2)
    embedding = best.embedding_json
    return AgentSkillCandidate(
        cluster_id=cluster_id,
        name=name,
        description=f"Use when the user needs help with: {best_intent}",
        content=content,
        confidence=confidence,
        experience_count=len(experiences),
        embedding=list(embedding) if embedding else None,
    )


def _distill_steps(
    *,
    user_id: int,
    experiences: Sequence[AgentExperience],
) -> list[str]:
    fragments: list[str] = []
    for experience in experiences:
        approach = df(
            user_id,
            experience.approach,
            table="agent_experiences",
            field="approach",
        )
        for line in approach.splitlines():
            cleaned = _clean_step(line)
            if cleaned and cleaned not in fragments:
                fragments.append(cleaned)
            if len(fragments) >= 4:
                break
        if len(fragments) >= 4:
            break
    if not fragments:
        fragments = ["Start by clarifying the user's constraints before acting."]
    return [f"{idx}. {fragment}" for idx, fragment in enumerate(fragments, start=1)]


def _distill_pitfalls(
    *,
    user_id: int,
    experiences: Sequence[AgentExperience],
) -> list[str]:
    pitfalls: list[str] = []
    for experience in experiences:
        approach = df(
            user_id,
            experience.approach,
            table="agent_experiences",
            field="approach",
        )
        if re.search(r"\b(over[- ]?budget|timeout|failed|avoid|error)\b", approach, re.IGNORECASE):
            cleaned = _clean_text(approach)
            if "over-budget" in cleaned or "over budget" in cleaned:
                pitfalls.append("Avoid over-budget suggestions when the user has named a budget.")
            elif "timeout" in cleaned:
                pitfalls.append("Narrow or simplify tool calls after a timeout instead of retrying unchanged.")
            elif "failed" in cleaned or "error" in cleaned:
                pitfalls.append("Record the failure mode and recover with a narrower next step.")
            elif "avoid" in cleaned:
                pitfalls.append(cleaned[:180])
    return _dedupe(pitfalls)[:3]


def _skill_name_from_intent(intent: str) -> str:
    lowered = intent.lower()
    if "trip" in lowered or "travel" in lowered:
        return "Trip Planning"
    if "debug" in lowered or "migration" in lowered:
        return "Debugging Recovery"
    words = re.findall(r"[A-Za-z0-9]+", intent)[:4]
    return " ".join(word.capitalize() for word in words) or "Learned Procedure"


def _supersede_lower_quality_duplicate(
    db: Session,
    *,
    user_id: int,
    experience: AgentExperience,
) -> None:
    embedding = _valid_embedding(experience.embedding_json)
    if embedding is None:
        return
    existing = list(
        db.scalars(
            select(AgentExperience)
            .where(
                AgentExperience.user_id == user_id,
                AgentExperience.id != experience.id,
                AgentExperience.superseded_by.is_(None),
            )
            .order_by(AgentExperience.created_at.desc())
            .limit(20)
        ).all()
    )
    for row in existing:
        row_embedding = _valid_embedding(row.embedding_json)
        if row_embedding is None:
            continue
        if cosine_similarity(embedding, row_embedding) < 0.9:
            continue
        if experience.quality_score > row.quality_score:
            row.superseded_by = experience.id
            row.updated_at = datetime.now(UTC)


def _append_experience_growth_log(
    db: Session,
    *,
    user_id: int,
    task_intent: str,
    quality: float,
) -> None:
    try:
        from anima_server.services.agent.self_model import append_growth_log_entry_row

        append_growth_log_entry_row(
            db,
            user_id=user_id,
            entry=f"Learned from experience: {task_intent} (quality: {quality:.2f})",
            source="agent_experience",
        )
    except Exception:
        return


def _append_skill_growth_log(
    db: Session,
    *,
    user_id: int,
    skill: AgentSkill,
) -> None:
    try:
        from anima_server.services.agent.self_model import append_growth_log_entry_row

        name = df(user_id, skill.name, table="agent_skills", field="name")
        append_growth_log_entry_row(
            db,
            user_id=user_id,
            entry=f"Distilled skill: {name} (confidence: {skill.confidence:.2f})",
            source="agent_skill",
        )
    except Exception:
        return


def _valid_embedding(value: object) -> list[float] | None:
    if not isinstance(value, list) or not value:
        return None
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _clean_tool_names(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        cleaned = _clean_text(value)
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def _clean_step(value: str) -> str:
    cleaned = _clean_text(value)
    cleaned = re.sub(r"^\d+[\).]\s*", "", cleaned)
    cleaned = re.sub(r"^[-*]\s*", "", cleaned)
    return cleaned.strip()


def _avg_quality(experiences: Sequence[AgentExperience]) -> float:
    if not experiences:
        return 0.0
    return sum(item.quality_score for item in experiences) / len(experiences)


def _dedupe(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
