"""Future-oriented memory signals.

Foresight signals are evidence-backed, time-bounded memories about future
events or expected outcomes. They are not tasks or calendar events; they are a
temporal awareness layer for prompts, reflection, and proactive follow-up.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import case, or_, select
from sqlalchemy.orm import Session

from anima_server.models import ForesightSignal
from anima_server.services.data_crypto import df, ef
from anima_server.services.user_timezone import normalize_timezone_spec

FORESIGHT_ACTIVE_STATUSES = frozenset({"active", "due", "occurred"})
FORESIGHT_PROMPT_STATUSES = frozenset({"active", "due"})
_WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
_RELATIVE_DATE_PATTERN = (
    r"tomorrow|today|next\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"|in\s+\d+\s+(?:day|days|week|weeks)|by\s+end\s+of\s+month|end\s+of\s+month"
)
_EVENT_TEXT_PATTERN = r"[^.!?;\n\r]+?"
_EVENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        rf"\bI\s+have\s+(?P<event>{_EVENT_TEXT_PATTERN})\s+"
        rf"(?P<relative>{_RELATIVE_DATE_PATTERN})\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\bI\s+need\s+to\s+(?P<event>{_EVENT_TEXT_PATTERN})\s+"
        rf"(?P<relative>{_RELATIVE_DATE_PATTERN})\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\bmy\s+(?P<event>{_EVENT_TEXT_PATTERN})\s+is\s+"
        rf"(?P<relative>{_RELATIVE_DATE_PATTERN})\b",
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True, slots=True)
class ForesightCandidate:
    content: str
    evidence: str
    relative_text: str | None
    start_date: date | None
    end_date: date | None
    duration_days: int | None
    confidence: float = 0.8


def extract_regex_foresight_signals(
    text: str,
    *,
    observed_at: datetime | None = None,
    timezone_name: str | None = None,
) -> tuple[ForesightCandidate, ...]:
    """Extract simple future events with deterministic relative-date handling."""
    prepared = _clean_evidence(text)
    if not prepared:
        return ()

    anchor = observed_at or datetime.now(UTC)
    candidates: list[ForesightCandidate] = []
    seen: set[tuple[str, date | None, date | None]] = set()
    for pattern in _EVENT_PATTERNS:
        for match in pattern.finditer(prepared):
            raw_event = _clean_event(match.group("event"))
            relative = _clean_text(match.group("relative"))
            if not raw_event or not relative:
                continue
            resolved = resolve_relative_date(
                relative,
                anchor=anchor,
                timezone_name=timezone_name,
            )
            if resolved is None:
                continue
            start, end = resolved
            content = _content_for_event(raw_event)
            key = (_normalize_for_match(content), start, end)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                ForesightCandidate(
                    content=content,
                    evidence=prepared,
                    relative_text=relative,
                    start_date=start,
                    end_date=end,
                    duration_days=((end - start).days + 1) if start and end else None,
                )
            )
    return tuple(candidates)


def parse_llm_foresight_payload(
    payload: object,
    *,
    observed_at: datetime | None = None,
    timezone_name: str | None = None,
) -> tuple[ForesightCandidate, ...]:
    if not isinstance(payload, list):
        return ()

    candidates: list[ForesightCandidate] = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        content = _clean_text(str(raw.get("content") or ""))
        evidence = _clean_text(str(raw.get("evidence") or ""))
        if not content or not evidence:
            continue
        start = _parse_iso_date(raw.get("start_date"))
        end = _parse_iso_date(raw.get("end_date")) or start
        relative = _clean_text(str(raw.get("relative_text") or "")) or None
        if start is None and relative and observed_at is not None:
            resolved = resolve_relative_date(
                relative,
                anchor=observed_at,
                timezone_name=timezone_name,
            )
            if resolved is not None:
                start, end = resolved
        duration = _coerce_positive_int(raw.get("duration_days"))
        if duration is None and start is not None and end is not None:
            duration = (end - start).days + 1
        candidates.append(
            ForesightCandidate(
                content=content,
                evidence=evidence,
                relative_text=relative,
                start_date=start,
                end_date=end,
                duration_days=duration,
                confidence=_clamp_confidence(raw.get("confidence", 0.8)),
            )
        )
    return tuple(candidates)


def _local_anchor_date(anchor: datetime, *, timezone_name: str | None) -> date:
    if not timezone_name:
        return anchor.date()
    try:
        _normalized, tzinfo = normalize_timezone_spec(timezone_name)
    except ValueError:
        return anchor.date()

    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=UTC)
    return anchor.astimezone(tzinfo).date()


def resolve_relative_date(
    relative_text: str,
    *,
    anchor: datetime,
    timezone_name: str | None = None,
) -> tuple[date, date] | None:
    relative = _clean_text(relative_text).lower()
    anchor_date = _local_anchor_date(anchor, timezone_name=timezone_name)
    if relative == "today":
        return anchor_date, anchor_date
    if relative == "tomorrow":
        target = anchor_date + timedelta(days=1)
        return target, target
    if relative in {"end of month", "by end of month"}:
        last_day = calendar.monthrange(anchor_date.year, anchor_date.month)[1]
        target = date(anchor_date.year, anchor_date.month, last_day)
        return target, target

    weekday_match = re.fullmatch(r"next\s+(\w+)", relative)
    if weekday_match:
        weekday = _WEEKDAY_INDEX.get(weekday_match.group(1))
        if weekday is None:
            return None
        delta = (weekday - anchor_date.weekday()) % 7
        if delta == 0:
            delta = 7
        target = anchor_date + timedelta(days=delta)
        return target, target

    offset_match = re.fullmatch(r"in\s+(\d+)\s+(day|days|week|weeks)", relative)
    if offset_match:
        amount = int(offset_match.group(1))
        unit = offset_match.group(2)
        days = amount * 7 if unit.startswith("week") else amount
        target = anchor_date + timedelta(days=days)
        return target, target

    return None


def upsert_foresight_signal(
    db: Session,
    *,
    user_id: int,
    signal: ForesightCandidate,
    source_thread_id: int | None = None,
    source_message_ids: list[int] | None = None,
    observed_at: datetime | None = None,
) -> ForesightSignal:
    """Create or reinforce an overlapping foresight signal."""
    now = observed_at or datetime.now(UTC)
    existing = _find_duplicate_signal(db, user_id=user_id, signal=signal)
    if existing is not None:
        _merge_signal(existing, user_id=user_id, signal=signal, message_ids=source_message_ids)
        existing.last_seen_at = now
        existing.updated_at = now
        if source_thread_id is not None:
            existing.source_thread_id = source_thread_id
        return existing

    row = ForesightSignal(
        user_id=user_id,
        content=ef(user_id, signal.content, table="foresight_signals", field="content"),
        evidence=ef(user_id, signal.evidence, table="foresight_signals", field="evidence"),
        relative_text=ef(
            user_id,
            signal.relative_text,
            table="foresight_signals",
            field="relative_text",
        )
        if signal.relative_text
        else None,
        start_date=signal.start_date,
        end_date=signal.end_date,
        duration_days=signal.duration_days,
        status="active",
        confidence=signal.confidence,
        source_thread_id=source_thread_id,
        source_message_ids_json=_unique_ints(source_message_ids),
        observed_at=now,
        last_seen_at=now,
    )
    db.add(row)
    db.flush()
    return row


def store_foresight_from_text(
    db: Session,
    *,
    user_id: int,
    text: str,
    observed_at: datetime | None = None,
    source_thread_id: int | None = None,
    source_message_ids: list[int] | None = None,
    timezone_name: str | None = None,
) -> int:
    candidates = extract_regex_foresight_signals(
        text,
        observed_at=observed_at,
        timezone_name=timezone_name,
    )
    created_or_updated = 0
    for candidate in candidates:
        upsert_foresight_signal(
            db,
            user_id=user_id,
            signal=candidate,
            source_thread_id=source_thread_id,
            source_message_ids=source_message_ids,
            observed_at=observed_at,
        )
        created_or_updated += 1
    return created_or_updated


def sweep_foresight_lifecycle(
    db: Session,
    *,
    user_id: int,
    today: date | None = None,
    stale_after_days: int = 7,
) -> dict[str, int]:
    current = today or datetime.now(UTC).date()
    due = 0
    occurred = 0
    stale = 0
    rows = list(
        db.scalars(
            select(ForesightSignal)
            .where(
                ForesightSignal.user_id == user_id,
                ForesightSignal.status.in_(list(FORESIGHT_ACTIVE_STATUSES)),
            )
            .order_by(ForesightSignal.start_date.asc(), ForesightSignal.id.asc())
        ).all()
    )
    now = datetime.now(UTC)
    for row in rows:
        end = row.end_date or row.start_date
        if end is None:
            continue
        if row.status in {"active"} and row.start_date and row.start_date <= current <= end:
            row.status = "due"
            row.updated_at = now
            due += 1
            continue
        if row.status in {"active", "due"} and end < current:
            row.status = "occurred"
            row.updated_at = now
            occurred += 1
        if row.status == "occurred" and end + timedelta(days=stale_after_days) < current:
            row.status = "stale"
            row.updated_at = now
            stale += 1
    return {"due": due, "occurred": occurred, "stale": stale}


def mark_cancelled_from_text(
    db: Session,
    *,
    user_id: int,
    text: str,
    observed_at: datetime | None = None,
) -> int:
    if not re.search(r"\b(cancelled|canceled|called off|not happening)\b", text, re.IGNORECASE):
        return 0
    prepared = _normalize_for_match(text)
    now = observed_at or datetime.now(UTC)
    count = 0
    rows = list(
        db.scalars(
            select(ForesightSignal)
            .where(
                ForesightSignal.user_id == user_id,
                ForesightSignal.status.in_(["active", "due"]),
            )
            .order_by(ForesightSignal.start_date.asc(), ForesightSignal.id.asc())
        ).all()
    )
    for row in rows:
        content = df(user_id, row.content, table="foresight_signals", field="content")
        if _text_overlap(content, prepared) < 0.4:
            continue
        row.status = "cancelled"
        row.updated_at = now
        count += 1
    return count


def get_prompt_foresight_signals(
    db: Session,
    *,
    user_id: int,
    today: date | None = None,
    limit: int = 8,
) -> tuple[ForesightSignal, ...]:
    current = today or datetime.now(UTC).date()
    rows = list(
        db.scalars(
            select(ForesightSignal)
            .where(
                ForesightSignal.user_id == user_id,
                ForesightSignal.status.in_(list(FORESIGHT_PROMPT_STATUSES)),
                or_(
                    ForesightSignal.status == "due",
                    ForesightSignal.end_date.is_(None),
                    ForesightSignal.end_date >= current,
                ),
            )
            .order_by(
                case(
                    (ForesightSignal.status == "due", 0),
                    (ForesightSignal.start_date.is_not(None), 1),
                    else_=2,
                ).asc(),
                ForesightSignal.start_date.asc(),
                ForesightSignal.id.asc(),
            )
            .limit(limit)
        ).all()
    )
    return tuple(rows)


def _find_duplicate_signal(
    db: Session,
    *,
    user_id: int,
    signal: ForesightCandidate,
) -> ForesightSignal | None:
    rows = list(
        db.scalars(
            select(ForesightSignal)
            .where(
                ForesightSignal.user_id == user_id,
                ForesightSignal.status.in_(list(FORESIGHT_ACTIVE_STATUSES)),
            )
            .order_by(ForesightSignal.updated_at.desc(), ForesightSignal.id.desc())
            .limit(50)
        ).all()
    )
    for row in rows:
        if not _dates_overlap(row.start_date, row.end_date, signal.start_date, signal.end_date):
            continue
        content = df(user_id, row.content, table="foresight_signals", field="content")
        if _text_overlap(content, signal.content) >= 0.6:
            return row
    return None


def _merge_signal(
    row: ForesightSignal,
    *,
    user_id: int,
    signal: ForesightCandidate,
    message_ids: list[int] | None,
) -> None:
    existing_evidence = df(user_id, row.evidence, table="foresight_signals", field="evidence")
    evidence_parts = [part for part in existing_evidence.split("\n") if part.strip()]
    if signal.evidence not in evidence_parts:
        evidence_parts.append(signal.evidence)
    row.evidence = ef(
        user_id,
        "\n".join(evidence_parts),
        table="foresight_signals",
        field="evidence",
    )
    row.source_message_ids_json = _unique_ints(
        [*(row.source_message_ids_json or []), *(message_ids or [])]
    )
    row.confidence = max(float(row.confidence or 0.0), signal.confidence)


def _dates_overlap(
    a_start: date | None,
    a_end: date | None,
    b_start: date | None,
    b_end: date | None,
) -> bool:
    if a_start is None or b_start is None:
        return False
    a_stop = a_end or a_start
    b_stop = b_end or b_start
    return a_start <= b_stop and b_start <= a_stop


def _text_overlap(left: str, right: str) -> float:
    left_tokens = set(_match_tokens(left))
    right_tokens = set(_match_tokens(right))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _match_tokens(value: str) -> tuple[str, ...]:
    ignored = {"a", "an", "the", "user", "has", "have", "my", "i"}
    return tuple(
        token
        for token in re.findall(r"[a-z0-9]+", _normalize_for_match(value))
        if token not in ignored
    )


def _normalize_for_match(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def _clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip(" \t\r\n\"'`.,;:!?")


def _clean_evidence(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def _clean_event(value: str) -> str:
    cleaned = _clean_text(value)
    cleaned = re.sub(r"^(?:a|an|the)\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^(?:submit|finish|complete)\s+", "", cleaned, flags=re.IGNORECASE)
    return cleaned


def _content_for_event(event: str) -> str:
    first = event[:1].lower()
    article = "an" if first in {"a", "e", "i", "o", "u"} else "a"
    return f"User has {article} {event}".strip()


def _parse_iso_date(value: object) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _coerce_positive_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _clamp_confidence(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.8
    return max(0.0, min(1.0, parsed))


def _unique_ints(values: list[int] | None) -> list[int]:
    seen: set[int] = set()
    result: list[int] = []
    for value in values or []:
        parsed = int(value)
        if parsed in seen:
            continue
        seen.add(parsed)
        result.append(parsed)
    return result
