from __future__ import annotations

from collections import defaultdict
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from anima_server.models.runtime_memory import MemoryRetrievalFeedback
from anima_server.services.agent.state import AgentRetrievalTrace
from anima_server.services.agent.text_processing import prepare_embedding_text

_MIN_TOKEN_LENGTH = 3
_MIN_SUBSTRING_MATCH_LENGTH = 24
_POSITIVE_IMPORTANCE_EVIDENCE_THRESHOLD = 0.7
_CORRECTION_IMPORTANCE_EVIDENCE_THRESHOLD = 0.5
_USED_EVIDENCE_HEAT_SCALE = 0.2
_CORRECTED_EVIDENCE_HEAT_SCALE = 0.2
_ZERO_REFERENCE_HEAT_DECAY = 0.95
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "has",
    "have",
    "her",
    "his",
    "how",
    "i",
    "in",
    "into",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "our",
    "she",
    "that",
    "the",
    "their",
    "them",
    "there",
    "they",
    "this",
    "to",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "with",
    "you",
    "your",
}


@dataclass(frozen=True, slots=True)
class RetrievalFeedbackOutcome:
    memory_item_id: int
    was_used: bool
    was_corrected: bool
    evidence_score: float


def infer_retrieval_feedback_outcomes(
    *,
    retrieval: AgentRetrievalTrace | None,
    response_text: str,
) -> tuple[RetrievalFeedbackOutcome, ...]:
    if retrieval is None:
        return ()

    raw_response = (response_text or "").strip()
    if not raw_response:
        return ()

    normalized_response = prepare_embedding_text(raw_response, limit=4096).lower()
    if not normalized_response:
        return ()

    response_tokens = _tokenize(normalized_response)
    if not response_tokens:
        return ()
    correction_facts = _extract_response_correction_facts(raw_response)

    explicit_matches = {
        citation.memory_item_id
        for citation in retrieval.citations
        if f"memory://items/{citation.memory_item_id}" in raw_response.lower()
    }

    fragment_texts: dict[int, list[str]] = {}
    for fragment in retrieval.context_fragments:
        if fragment.text:
            fragment_texts.setdefault(fragment.memory_item_id, []).append(fragment.text)

    ordered_item_ids: list[int] = []
    for citation in retrieval.citations:
        if citation.memory_item_id not in ordered_item_ids:
            ordered_item_ids.append(citation.memory_item_id)
    for item_id in fragment_texts:
        if item_id not in ordered_item_ids:
            ordered_item_ids.append(item_id)

    outcomes: list[RetrievalFeedbackOutcome] = []
    for item_id in ordered_item_ids:
        best_used = item_id in explicit_matches
        best_corrected = False
        best_score = 1.0 if best_used else 0.0
        fragment_found = False

        for fragment_text in fragment_texts.get(item_id, []):
            fragment_found = True
            was_used, score = _match_fragment(
                fragment_text=fragment_text,
                normalized_response=normalized_response,
                response_tokens=response_tokens,
            )
            if was_used and (not best_used or score > best_score):
                best_used = True
                best_score = score
            elif not best_used and score > best_score:
                best_score = score

            was_corrected, correction_score = _match_correction_fragment(
                fragment_text=fragment_text,
                correction_facts=correction_facts,
            )
            if was_corrected and (not best_corrected or correction_score >= best_score):
                best_corrected = True
                best_used = False
                best_score = correction_score

        if not fragment_found and item_id not in explicit_matches:
            continue

        outcomes.append(
            RetrievalFeedbackOutcome(
                memory_item_id=item_id,
                was_used=best_used and not best_corrected,
                was_corrected=best_corrected,
                evidence_score=round(best_score, 4),
            )
        )

    return tuple(outcomes)


def record_retrieval_feedback(
    runtime_db: Session,
    *,
    user_id: int,
    run_id: int | None = None,
    retrieval: AgentRetrievalTrace | None,
    response_text: str,
) -> dict[str, int]:
    outcomes = infer_retrieval_feedback_outcomes(
        retrieval=retrieval,
        response_text=response_text,
    )
    if not outcomes:
        return {"logged": 0, "used": 0, "corrected": 0, "unused": 0}

    used = 0
    corrected = 0
    unused = 0
    for outcome in outcomes:
        runtime_db.add(
            MemoryRetrievalFeedback(
                user_id=user_id,
                run_id=run_id,
                memory_item_id=outcome.memory_item_id,
                was_used=outcome.was_used,
                was_corrected=outcome.was_corrected,
                evidence_score=outcome.evidence_score,
            )
        )
        if outcome.was_corrected:
            corrected += 1
        elif outcome.was_used:
            used += 1
        else:
            unused += 1

    runtime_db.flush()
    return {
        "logged": len(outcomes),
        "used": used,
        "corrected": corrected,
        "unused": unused,
    }


def sync_retrieval_feedback(
    *,
    user_id: int,
    runtime_db: Session,
    soul_db: Session | None,
    dry_run: bool = False,
) -> dict:
    rows = runtime_db.execute(
        select(
            MemoryRetrievalFeedback.id,
            MemoryRetrievalFeedback.run_id,
            MemoryRetrievalFeedback.memory_item_id,
            MemoryRetrievalFeedback.was_used,
            MemoryRetrievalFeedback.was_corrected,
            MemoryRetrievalFeedback.evidence_score,
        )
        .where(
            MemoryRetrievalFeedback.user_id == user_id,
            MemoryRetrievalFeedback.synced.is_(False),
        )
    ).all()

    if not rows:
        return {
            "items_synced": 0,
            "run_count": 0,
            "zero_reference_runs": 0,
            "used_counts": {},
            "used_evidence_totals": {},
            "corrected_counts": {},
            "corrected_evidence_totals": {},
            "unused_counts": {},
            "zero_reference_counts": {},
            "importance_deltas": {},
            "evidence_heat_factors": {},
            "heat_decay_factors": {},
        }

    run_feedback: dict[int | tuple[str, int], list] = defaultdict(list)
    unused_counts: dict[int, int] = defaultdict(int)

    for row in rows:
        if not row.was_used and not row.was_corrected:
            unused_counts[int(row.memory_item_id)] += 1
        group_key: int | tuple[str, int]
        if row.run_id is None:
            group_key = ("legacy", int(row.id))
        else:
            group_key = int(row.run_id)
        run_feedback[group_key].append(row)

    used_counts: dict[int, int] = defaultdict(int)
    used_evidence_totals: dict[int, float] = defaultdict(float)
    corrected_counts: dict[int, int] = defaultdict(int)
    corrected_evidence_totals: dict[int, float] = defaultdict(float)
    zero_reference_counts: dict[int, int] = defaultdict(int)
    zero_reference_runs = 0

    for grouped_rows in run_feedback.values():
        used_rows = [row for row in grouped_rows if row.was_used and not row.was_corrected]
        corrected_rows = [row for row in grouped_rows if row.was_corrected]
        if used_rows or corrected_rows:
            for row in used_rows:
                item_id = int(row.memory_item_id)
                used_counts[item_id] += 1
                used_evidence_totals[item_id] += float(row.evidence_score or 0.0)
            for row in corrected_rows:
                item_id = int(row.memory_item_id)
                corrected_counts[item_id] += 1
                corrected_evidence_totals[item_id] += float(row.evidence_score or 0.0)
            continue

        zero_reference_runs += 1
        for row in grouped_rows:
            zero_reference_counts[int(row.memory_item_id)] += 1

    evidence_heat_factors = _collect_evidence_heat_factors(
        used_evidence_totals=used_evidence_totals,
        corrected_evidence_totals=corrected_evidence_totals,
    )

    runtime_db.execute(
        update(MemoryRetrievalFeedback)
        .where(
            MemoryRetrievalFeedback.user_id == user_id,
            MemoryRetrievalFeedback.synced.is_(False),
        )
        .values(synced=True)
    )
    runtime_db.flush()

    if dry_run or soul_db is None:
        return {
            "items_synced": len(rows),
            "run_count": len(run_feedback),
            "zero_reference_runs": zero_reference_runs,
            "used_counts": dict(used_counts),
            "used_evidence_totals": _round_scores(used_evidence_totals),
            "corrected_counts": dict(corrected_counts),
            "corrected_evidence_totals": _round_scores(corrected_evidence_totals),
            "unused_counts": dict(unused_counts),
            "zero_reference_counts": dict(zero_reference_counts),
            "importance_deltas": {},
            "evidence_heat_factors": evidence_heat_factors,
            "heat_decay_factors": {},
        }

    runtime_db.commit()

    from anima_server.models import MemoryItem
    from anima_server.services.agent.heat_scoring import compute_heat

    ref_now = datetime.now(UTC)
    importance_deltas: dict[int, int] = {}
    applied_evidence_heat_factors: dict[int, float] = {}
    heat_decay_factors: dict[int, float] = {}

    for item_id in sorted(set(used_counts) | set(corrected_counts) | set(zero_reference_counts)):
        item = soul_db.get(MemoryItem, item_id)
        if item is None:
            continue

        delta = _importance_delta(
            used_count=used_counts.get(item_id, 0),
            used_evidence_total=used_evidence_totals.get(item_id, 0.0),
            corrected_count=corrected_counts.get(item_id, 0),
            corrected_evidence_total=corrected_evidence_totals.get(item_id, 0.0),
        )
        heat_value = item.heat
        if delta != 0:
            prior_importance = item.importance or 3
            item.importance = max(1, min(5, prior_importance + delta))
            importance_deltas[item_id] = item.importance - prior_importance
            ref_count = item.reference_count or 0
            heat_value = compute_heat(
                access_count=ref_count,
                interaction_depth=ref_count,
                last_accessed_at=item.last_referenced_at,
                importance=float(item.importance),
                now=ref_now,
                created_at=item.created_at,
            )

        evidence_heat_factor = evidence_heat_factors.get(item_id)
        if evidence_heat_factor is not None:
            if heat_value in (None, 0.0):
                ref_count = item.reference_count or 0
                heat_value = compute_heat(
                    access_count=ref_count,
                    interaction_depth=ref_count,
                    last_accessed_at=item.last_referenced_at,
                    importance=float(item.importance or 3),
                    now=ref_now,
                    created_at=item.created_at,
                )
            applied_evidence_heat_factors[item_id] = evidence_heat_factor
            heat_value = float(heat_value or 0.0) * evidence_heat_factor

        decay_count = zero_reference_counts.get(item_id, 0)
        if decay_count > 0:
            if heat_value in (None, 0.0):
                ref_count = item.reference_count or 0
                heat_value = compute_heat(
                    access_count=ref_count,
                    interaction_depth=ref_count,
                    last_accessed_at=item.last_referenced_at,
                    importance=float(item.importance or 3),
                    now=ref_now,
                    created_at=item.created_at,
                )
            decay_factor = _zero_reference_decay(decay_count)
            heat_decay_factors[item_id] = decay_factor
            heat_value = float(heat_value or 0.0) * decay_factor

        if heat_value is not None and (delta != 0 or evidence_heat_factor is not None or decay_count > 0):
            item.heat = heat_value

    soul_db.flush()
    soul_db.commit()

    runtime_db.execute(
        delete(MemoryRetrievalFeedback).where(
            MemoryRetrievalFeedback.user_id == user_id,
            MemoryRetrievalFeedback.synced.is_(True),
        )
    )
    runtime_db.commit()

    return {
        "items_synced": len(rows),
        "run_count": len(run_feedback),
        "zero_reference_runs": zero_reference_runs,
        "used_counts": dict(used_counts),
        "used_evidence_totals": _round_scores(used_evidence_totals),
        "corrected_counts": dict(corrected_counts),
        "corrected_evidence_totals": _round_scores(corrected_evidence_totals),
        "unused_counts": dict(unused_counts),
        "zero_reference_counts": dict(zero_reference_counts),
        "importance_deltas": importance_deltas,
        "evidence_heat_factors": applied_evidence_heat_factors,
        "heat_decay_factors": heat_decay_factors,
    }


def _extract_response_correction_facts(response_text: str):
    from anima_server.services.agent.feedback_signals import extract_correction_facts

    normalized = re.sub(
        r"\b(?:just\s+)?to clarify\b",
        "actually",
        response_text,
        flags=re.IGNORECASE,
    )
    return extract_correction_facts(normalized)


def _match_correction_fragment(*, fragment_text: str, correction_facts: list) -> tuple[bool, float]:
    if not correction_facts:
        return False, 0.0

    normalized_fragment = prepare_embedding_text(fragment_text, limit=512).lower()
    if not normalized_fragment:
        return False, 0.0

    fragment_tokens = _tokenize(normalized_fragment)
    if not fragment_tokens:
        return False, 0.0

    best_score = 0.0
    for fact in correction_facts:
        wrong_tokens = _tokenize(fact.wrong or "")
        right_tokens = _tokenize(fact.right)
        topic_tokens = _tokenize(fact.topic or "")
        score = 0.0

        if wrong_tokens:
            wrong_overlap = len(wrong_tokens & fragment_tokens)
            wrong_coverage = wrong_overlap / len(wrong_tokens)
            if wrong_overlap > 0 and wrong_coverage >= 0.5:
                score = wrong_coverage
        elif topic_tokens:
            topic_overlap = len(topic_tokens & fragment_tokens)
            right_coverage = 0.0
            if right_tokens:
                right_coverage = len(right_tokens & fragment_tokens) / len(right_tokens)
            if topic_overlap > 0 and right_coverage < 1.0:
                score = max(score, topic_overlap / len(topic_tokens))

        if score > best_score:
            best_score = score

    return best_score >= 0.5, best_score


def _match_fragment(
    *,
    fragment_text: str,
    normalized_response: str,
    response_tokens: set[str],
) -> tuple[bool, float]:
    normalized_fragment = prepare_embedding_text(fragment_text, limit=512).lower()
    if not normalized_fragment:
        return False, 0.0

    if (
        len(normalized_fragment) >= _MIN_SUBSTRING_MATCH_LENGTH
        and normalized_fragment in normalized_response
    ):
        return True, 1.0

    fragment_tokens = _tokenize(normalized_fragment)
    if not fragment_tokens:
        return False, 0.0

    overlap_count = len(fragment_tokens & response_tokens)
    if overlap_count == 0:
        return False, 0.0

    coverage = overlap_count / len(fragment_tokens)
    if len(fragment_tokens) <= 2:
        return overlap_count >= 1, coverage
    if len(fragment_tokens) <= 4:
        return overlap_count >= 2 or coverage >= 0.6, coverage
    return overlap_count >= 2 and coverage >= 0.4, coverage


def _tokenize(text: str) -> set[str]:
    tokens: set[str] = set()
    for raw_token in re.findall(r"[a-z0-9']+", text.lower()):
        token = _normalize_token(raw_token)
        if len(token) < _MIN_TOKEN_LENGTH or token in _STOPWORDS:
            continue
        tokens.add(token)
    return tokens


def _normalize_token(token: str) -> str:
    normalized = token.strip("'")
    if len(normalized) > 4 and normalized.endswith("ies"):
        normalized = normalized[:-3] + "y"
    elif len(normalized) > 5 and normalized.endswith("ing"):
        normalized = normalized[:-3]
    elif len(normalized) > 4 and normalized.endswith("ed"):
        normalized = normalized[:-2]
    elif len(normalized) > 4 and normalized.endswith("es"):
        normalized = normalized[:-2]
    elif len(normalized) > 3 and normalized.endswith("s") and not normalized.endswith("ss"):
        normalized = normalized[:-1]
    return normalized


def _round_scores(values: dict[int, float]) -> dict[int, float]:
    return {item_id: round(score, 4) for item_id, score in values.items()}


def _collect_evidence_heat_factors(
    *,
    used_evidence_totals: dict[int, float],
    corrected_evidence_totals: dict[int, float],
) -> dict[int, float]:
    factors: dict[int, float] = {}
    for item_id in sorted(set(used_evidence_totals) | set(corrected_evidence_totals)):
        factor = _evidence_heat_factor(
            used_evidence_total=used_evidence_totals.get(item_id, 0.0),
            corrected_evidence_total=corrected_evidence_totals.get(item_id, 0.0),
        )
        if factor is not None:
            factors[item_id] = factor
    return factors


def _evidence_heat_factor(
    *,
    used_evidence_total: float,
    corrected_evidence_total: float,
) -> float | None:
    bounded_used = min(max(used_evidence_total, 0.0), 1.0)
    bounded_corrected = min(max(corrected_evidence_total, 0.0), 1.0)
    factor = 1.0 + bounded_used * _USED_EVIDENCE_HEAT_SCALE
    factor *= 1.0 - bounded_corrected * _CORRECTED_EVIDENCE_HEAT_SCALE
    if abs(factor - 1.0) < 1e-9:
        return None
    return round(factor, 4)


def _importance_delta(
    *,
    used_count: int,
    used_evidence_total: float,
    corrected_count: int,
    corrected_evidence_total: float,
) -> int:
    if (
        used_count > 0
        and used_evidence_total >= _POSITIVE_IMPORTANCE_EVIDENCE_THRESHOLD
        and used_evidence_total > corrected_evidence_total
    ):
        return 1
    if (
        corrected_count > 0
        and corrected_evidence_total >= _CORRECTION_IMPORTANCE_EVIDENCE_THRESHOLD
        and corrected_evidence_total >= used_evidence_total
    ):
        return -1
    return 0


def _zero_reference_decay(count: int) -> float:
    return _ZERO_REFERENCE_HEAT_DECAY ** max(count, 0)