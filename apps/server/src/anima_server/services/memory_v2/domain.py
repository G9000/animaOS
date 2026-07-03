from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TemporalRecordStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"
    INACTIVE = "inactive"


class ForesightStatus(StrEnum):
    ACTIVE = "active"
    DUE = "due"
    OCCURRED = "occurred"
    STALE = "stale"
    CANCELLED = "cancelled"


class MemoryEndpointKind(StrEnum):
    USER = "user"
    AGENT = "agent"
    PERSON = "person"
    PROJECT = "project"
    ORGANIZATION = "organization"
    CONCEPT = "concept"
    EXTERNAL = "external"


class MemoryClass(StrEnum):
    IDENTITY = "identity"
    LIFE_EVENT = "life_event"
    RELATIONSHIP = "relationship"
    ACTIVE_PROJECT = "active_project"
    CASUAL = "casual"
    TRANSIENT = "transient"
    EMOTIONAL_PATTERN = "emotional_pattern"


class StabilityClass(StrEnum):
    STABLE = "stable"
    EVOLVING = "evolving"
    TEMPORARY = "temporary"


class DecayClass(StrEnum):
    ANCHORED = "anchored"
    SLOW = "slow"
    STANDARD = "standard"
    FAST = "fast"
    EPHEMERAL = "ephemeral"


@dataclass(frozen=True, slots=True)
class RecallScoreBreakdown:
    lexical: float = 0.0
    vector: float = 0.0
    graph: float = 0.0
    temporal: float = 0.0
    profile: float = 0.0
    salience: float = 0.0
    recency: float = 0.0
    importance: float = 0.0
    access: float = 0.0


TemporalStatus = TemporalRecordStatus
EndpointKind = MemoryEndpointKind

ACTIVE_TEMPORAL_STATUSES = frozenset({TemporalRecordStatus.ACTIVE.value})
HISTORICAL_TEMPORAL_STATUSES = frozenset(
    {
        TemporalRecordStatus.SUPERSEDED.value,
        TemporalRecordStatus.RETRACTED.value,
        TemporalRecordStatus.INACTIVE.value,
    }
)
VISIBLE_TEMPORAL_STATUSES = ACTIVE_TEMPORAL_STATUSES | HISTORICAL_TEMPORAL_STATUSES

ACTIVE_FORESIGHT_STATUSES = frozenset(
    {
        ForesightStatus.ACTIVE.value,
        ForesightStatus.DUE.value,
        ForesightStatus.OCCURRED.value,
    }
)
TERMINAL_FORESIGHT_STATUSES = frozenset(
    {
        ForesightStatus.STALE.value,
        ForesightStatus.CANCELLED.value,
    }
)
VISIBLE_FORESIGHT_STATUSES = ACTIVE_FORESIGHT_STATUSES | TERMINAL_FORESIGHT_STATUSES

VALID_MEMORY_CLASSES = frozenset(item.value for item in MemoryClass)
VALID_MEMORY_ENDPOINT_KINDS = frozenset(item.value for item in MemoryEndpointKind)
VALID_STABILITY_CLASSES = frozenset(item.value for item in StabilityClass)
VALID_DECAY_CLASSES = frozenset(item.value for item in DecayClass)

__all__ = [
    "ACTIVE_FORESIGHT_STATUSES",
    "ACTIVE_TEMPORAL_STATUSES",
    "HISTORICAL_TEMPORAL_STATUSES",
    "TERMINAL_FORESIGHT_STATUSES",
    "VALID_DECAY_CLASSES",
    "VALID_MEMORY_CLASSES",
    "VALID_MEMORY_ENDPOINT_KINDS",
    "VALID_STABILITY_CLASSES",
    "VISIBLE_FORESIGHT_STATUSES",
    "VISIBLE_TEMPORAL_STATUSES",
    "DecayClass",
    "EndpointKind",
    "ForesightStatus",
    "MemoryClass",
    "MemoryEndpointKind",
    "RecallScoreBreakdown",
    "StabilityClass",
    "TemporalRecordStatus",
    "TemporalStatus",
]
