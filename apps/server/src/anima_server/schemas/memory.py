from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class MemoryItemResponse(BaseModel):
    id: int
    content: str
    category: str
    importance: int
    source: str
    isSuperseded: bool = False
    createdAt: datetime | None = None
    updatedAt: datetime | None = None


class MemoryItemCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=500)
    category: str = Field(default="fact")
    importance: int = Field(default=3, ge=1, le=5)


class MemoryItemUpdateRequest(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=500)
    category: str | None = None
    importance: int | None = Field(default=None, ge=1, le=5)


class MemoryEpisodeResponse(BaseModel):
    id: int
    date: str
    time: str | None = None
    summary: str
    topics: list[str] = Field(default_factory=list)
    emotionalArc: str | None = None
    significanceScore: int = 3
    turnCount: int | None = None
    createdAt: datetime | None = None


class MemoryOverview(BaseModel):
    totalItems: int
    factCount: int
    preferenceCount: int
    goalCount: int
    relationshipCount: int
    currentFocus: str | None = None
    episodeCount: int


class MemoryEvidenceAuditResponse(BaseModel):
    totalActive: int
    withEvidence: int
    missingEvidence: int
    coverageRatio: float
    coveragePercent: float
    missingItemIds: list[int] = Field(default_factory=list)


class MemoryEvidenceBackfillResponse(BaseModel):
    scanned: int
    created: int
    skippedExisting: int
    skippedEmpty: int


class MemoryEvidenceBackfillRunResponse(BaseModel):
    backfill: MemoryEvidenceBackfillResponse
    audit: MemoryEvidenceAuditResponse
