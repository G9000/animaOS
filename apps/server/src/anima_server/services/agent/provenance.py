from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models import MemoryItem, MemoryItemEvidence
from anima_server.models.runtime import RuntimeMessage
from anima_server.models.runtime_memory import MemoryCandidate
from anima_server.services.data_crypto import df, ef

_SESSION_DATE_RE = re.compile(
    r"Session date:\s*(?P<date>[0-9]{4}[/-][0-9]{2}[/-][0-9]{2}"
    r"(?:\s*\([^)]+\))?(?:\s*[0-9]{2}:[0-9]{2}(?::[0-9]{2})?)?)",
    re.IGNORECASE,
)
_DATE_TIME_RE = re.compile(
    r"(?P<year>\d{4})[/-](?P<month>\d{1,2})[/-](?P<day>\d{1,2})"
    r"(?:\D+(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?)?"
)
_USER_LINE_RE = re.compile(r"^\s*User\s*:", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True, slots=True)
class EvidenceBackfillResult:
    scanned: int = 0
    created: int = 0
    skipped_existing: int = 0
    skipped_empty: int = 0


def add_memory_item_evidence(
    db: Session,
    *,
    user_id: int,
    memory_item_id: int,
    evidence_text: str,
    source_kind: str,
    runtime_thread_id: int | None = None,
    runtime_message_id: int | None = None,
    runtime_message_ids: list[int] | None = None,
    transcript_ref: str | None = None,
    sequence_id: int | None = None,
    speaker: str | None = None,
    observed_at: datetime | None = None,
    source_created_at: datetime | None = None,
    confidence: float = 1.0,
    extractor: str | None = None,
    metadata: dict[str, object] | None = None,
) -> MemoryItemEvidence | None:
    text = evidence_text.strip()
    if not text:
        return None

    evidence = MemoryItemEvidence(
        user_id=user_id,
        memory_item_id=memory_item_id,
        source_kind=source_kind,
        runtime_thread_id=runtime_thread_id,
        runtime_message_id=runtime_message_id,
        runtime_message_ids_json=runtime_message_ids or None,
        transcript_ref=transcript_ref,
        sequence_id=sequence_id,
        speaker=speaker,
        observed_at=observed_at,
        source_created_at=source_created_at,
        confidence=max(0.0, min(1.0, float(confidence))),
        extractor=extractor,
        evidence_text=ef(
            user_id,
            text,
            table="memory_item_evidence",
            field="evidence_text",
        ),
        metadata_json=metadata,
    )
    db.add(evidence)
    db.flush()
    return evidence


def add_candidate_memory_item_evidence(
    soul_db: Session,
    *,
    runtime_db: Session,
    candidate: MemoryCandidate,
    memory_item: MemoryItem,
) -> MemoryItemEvidence | None:
    message_ids = _normalize_source_message_ids(candidate.source_message_ids)
    messages = _load_source_messages(runtime_db, user_id=candidate.user_id, message_ids=message_ids)
    primary = _primary_source_message(messages)

    evidence_text = _candidate_evidence_text(candidate, primary)
    source_kind = _candidate_source_kind(candidate)
    observed_at = _candidate_observed_at(candidate, primary)

    return add_memory_item_evidence(
        soul_db,
        user_id=memory_item.user_id,
        memory_item_id=memory_item.id,
        evidence_text=evidence_text,
        source_kind=source_kind,
        runtime_thread_id=primary.thread_id if primary is not None else None,
        runtime_message_id=primary.id if primary is not None else None,
        runtime_message_ids=message_ids,
        sequence_id=primary.sequence_id if primary is not None else None,
        speaker=primary.role if primary is not None else _fallback_speaker(candidate),
        observed_at=observed_at,
        source_created_at=primary.created_at if primary is not None else candidate.created_at,
        confidence=_candidate_confidence(candidate),
        extractor=candidate.extraction_model or candidate.source,
        metadata={"candidate_id": int(candidate.id)} if candidate.id is not None else None,
    )


def backfill_memory_item_evidence(
    db: Session,
    *,
    user_id: int,
    limit: int = 500,
) -> EvidenceBackfillResult:
    """Create provenance evidence for existing memory items that do not have it."""

    existing_evidence = (
        select(MemoryItemEvidence.id)
        .where(
            MemoryItemEvidence.user_id == user_id,
            MemoryItemEvidence.memory_item_id == MemoryItem.id,
        )
        .exists()
    )
    items = list(
        db.scalars(
            select(MemoryItem)
            .where(
                MemoryItem.user_id == user_id,
                ~existing_evidence,
            )
            .order_by(MemoryItem.id)
            .limit(max(1, limit))
        ).all()
    )
    if not items:
        return EvidenceBackfillResult()

    result = EvidenceBackfillResult(scanned=len(items))
    created = 0
    skipped_empty = 0

    for item in items:
        item_id = int(item.id)
        plaintext = df(user_id, item.content, table="memory_items", field="content").strip()
        if not plaintext:
            skipped_empty += 1
            continue

        defaults = _backfill_defaults(item, plaintext)
        if add_memory_item_evidence(
            db,
            user_id=user_id,
            memory_item_id=item_id,
            evidence_text=plaintext,
            source_kind=defaults["source_kind"],
            speaker=defaults["speaker"],
            observed_at=defaults["observed_at"],
            source_created_at=item.created_at,
            confidence=defaults["confidence"],
            extractor="backfill",
            metadata=defaults["metadata"],
        ):
            created += 1

    return EvidenceBackfillResult(
        scanned=result.scanned,
        created=created,
        skipped_existing=0,
        skipped_empty=skipped_empty,
    )


def _backfill_defaults(item: MemoryItem, plaintext: str) -> dict[str, object]:
    session_date = _extract_session_date(plaintext)
    observed_at = _parse_session_date(session_date) if session_date else None
    if observed_at is None:
        observed_at = item.created_at

    if item.source == "eval_import_raw":
        metadata: dict[str, object] = {"source": "legacy_eval_raw_chunk"}
        if session_date:
            metadata["session_date"] = session_date
        return {
            "source_kind": "eval_import",
            "speaker": "user" if _USER_LINE_RE.search(plaintext) else "unknown",
            "observed_at": observed_at,
            "confidence": 0.7,
            "metadata": metadata,
        }

    return {
        "source_kind": "legacy_backfill",
        "speaker": "unknown",
        "observed_at": observed_at,
        "confidence": 0.5,
        "metadata": {
            "source": "legacy_backfill",
            "memory_source": item.source,
        },
    }


def _extract_session_date(text: str) -> str | None:
    match = _SESSION_DATE_RE.search(text)
    if match is None:
        return None
    return match.group("date").strip()


def _parse_session_date(value: str | None) -> datetime | None:
    if not value:
        return None
    match = _DATE_TIME_RE.search(value)
    if match is None:
        return None
    groups = match.groupdict()
    try:
        return datetime(
            int(groups["year"]),
            int(groups["month"]),
            int(groups["day"]),
            int(groups["hour"] or 0),
            int(groups["minute"] or 0),
            int(groups["second"] or 0),
            tzinfo=UTC,
        )
    except ValueError:
        return None


def _normalize_source_message_ids(raw: object) -> list[int]:
    if raw is None:
        return []
    value = raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = [part.strip() for part in raw.split(",") if part.strip()]
    if not isinstance(value, Iterable) or isinstance(value, (bytes, str)):
        return []

    ids: list[int] = []
    for item in value:
        try:
            message_id = int(item)
        except (TypeError, ValueError):
            continue
        if message_id > 0:
            ids.append(message_id)
    return ids


def _load_source_messages(
    runtime_db: Session,
    *,
    user_id: int,
    message_ids: list[int],
) -> list[RuntimeMessage]:
    if not message_ids:
        return []
    rows = list(
        runtime_db.scalars(
            select(RuntimeMessage).where(
                RuntimeMessage.user_id == user_id,
                RuntimeMessage.id.in_(message_ids),
            )
        ).all()
    )
    position = {message_id: index for index, message_id in enumerate(message_ids)}
    rows.sort(key=lambda message: position.get(int(message.id), len(position)))
    return rows


def _primary_source_message(messages: list[RuntimeMessage]) -> RuntimeMessage | None:
    for message in messages:
        if message.role == "user" and (message.content_text or "").strip():
            return message
    for message in messages:
        if (message.content_text or "").strip():
            return message
    return messages[0] if messages else None


def _candidate_evidence_text(
    candidate: MemoryCandidate,
    primary: RuntimeMessage | None,
) -> str:
    if primary is not None and (primary.content_text or "").strip():
        return str(primary.content_text).strip()
    return candidate.content


def _candidate_source_kind(candidate: MemoryCandidate) -> str:
    if candidate.importance_source == "user_explicit":
        return "explicit_save"
    if candidate.source == "regex":
        return "regex_extraction"
    if candidate.source in {"llm", "predict_calibrate"}:
        return "llm_extraction"
    return candidate.source or "llm_extraction"


def _candidate_observed_at(
    candidate: MemoryCandidate,
    primary: RuntimeMessage | None,
) -> datetime:
    if primary is not None and primary.created_at is not None:
        return primary.created_at
    if candidate.created_at is not None:
        return candidate.created_at
    return datetime.now(UTC)


def _fallback_speaker(candidate: MemoryCandidate) -> str:
    if candidate.importance_source == "user_explicit":
        return "user"
    return "unknown"


def _candidate_confidence(candidate: MemoryCandidate) -> float:
    if candidate.importance_source == "user_explicit":
        return 1.0
    if candidate.importance_source == "correction":
        return 0.95
    if candidate.source == "regex":
        return 0.7
    return 0.8
