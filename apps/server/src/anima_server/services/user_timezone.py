from __future__ import annotations

import re
from datetime import timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

_TIMEZONE_LINE_RE = re.compile(r"^timezone\s*:\s*(.+)$", re.IGNORECASE)
_UTC_OFFSET_RE = re.compile(
    r"^(?:UTC)?\s*([+-])(\d{1,2})(?::?(\d{2}))?$", re.IGNORECASE
)


def extract_timezone_value(content: str) -> str | None:
    for line in content.splitlines():
        match = _TIMEZONE_LINE_RE.match(line.strip())
        if match is not None:
            value = match.group(1).strip()
            if value:
                return value
    return None


def normalize_timezone_spec(value: str) -> tuple[str, tzinfo]:
    candidate = value.strip()
    if not candidate:
        raise ValueError("Timezone cannot be empty.")

    try:
        return candidate, ZoneInfo(candidate)
    except ZoneInfoNotFoundError:
        pass

    offset_match = _UTC_OFFSET_RE.match(candidate)
    if offset_match is None:
        raise ValueError(
            "Invalid timezone. Use an IANA timezone like 'Asia/Kuala_Lumpur' or an offset like 'UTC+08:00'."
        )

    sign, hours_text, minutes_text = offset_match.groups()
    hours = int(hours_text)
    minutes = int(minutes_text or "0")
    if hours > 23 or minutes > 59:
        raise ValueError(
            "Invalid UTC offset. Use a value between UTC-23:59 and UTC+23:59."
        )

    delta = timedelta(hours=hours, minutes=minutes)
    if sign == "-":
        delta = -delta

    normalized = f"UTC{sign}{hours:02d}:{minutes:02d}"
    return normalized, timezone(delta)


def strip_timezone_lines(content: str) -> str:
    kept_lines = [
        line for line in content.splitlines() if _TIMEZONE_LINE_RE.match(line.strip()) is None
    ]
    return "\n".join(kept_lines).strip()


def upsert_timezone_line(content: str, timezone_value: str) -> str:
    stripped = strip_timezone_lines(content)
    if not stripped:
        return f"Timezone: {timezone_value}"
    return f"{stripped}\nTimezone: {timezone_value}"


def store_timezone_in_world_context(
    db: Session,
    *,
    user_id: int,
    existing_content: str,
    timezone_value: str,
    updated_by: str = "tool",
) -> None:
    from anima_server.services.agent.self_model import set_self_model_block

    set_self_model_block(
        db,
        user_id=user_id,
        section="world",
        content=upsert_timezone_line(existing_content, timezone_value),
        updated_by=updated_by,
    )
