from __future__ import annotations

from enum import StrEnum

from anima_server.services.memory.domain import (
    ACTIVE_TEMPORAL_STATUSES,
    HISTORICAL_TEMPORAL_STATUSES,
    TERMINAL_FORESIGHT_STATUSES,
    VISIBLE_FORESIGHT_STATUSES,
    VISIBLE_TEMPORAL_STATUSES,
    ForesightStatus,
    TemporalStatus,
)


def normalize_temporal_status(
    value: str | TemporalStatus | None,
    *,
    default: TemporalStatus = TemporalStatus.ACTIVE,
) -> str:
    return _normalize_status(value, choices=VISIBLE_TEMPORAL_STATUSES, default=default)


def normalize_foresight_status(
    value: str | ForesightStatus | None,
    *,
    default: ForesightStatus = ForesightStatus.ACTIVE,
) -> str:
    return _normalize_status(value, choices=VISIBLE_FORESIGHT_STATUSES, default=default)


def is_current_status(value: str | TemporalStatus | None) -> bool:
    return normalize_temporal_status(value) in ACTIVE_TEMPORAL_STATUSES


def is_historical_status(value: str | TemporalStatus | None) -> bool:
    return normalize_temporal_status(value) in HISTORICAL_TEMPORAL_STATUSES


def is_terminal_foresight_status(value: str | ForesightStatus | None) -> bool:
    return normalize_foresight_status(value) in TERMINAL_FORESIGHT_STATUSES


def _normalize_status(
    value: str | StrEnum | None,
    *,
    choices: frozenset[str],
    default: StrEnum,
) -> str:
    raw = value.value if isinstance(value, StrEnum) else value
    normalized = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in choices:
        return normalized
    return default.value


__all__ = [
    "is_current_status",
    "is_historical_status",
    "is_terminal_foresight_status",
    "normalize_foresight_status",
    "normalize_temporal_status",
]
