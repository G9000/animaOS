from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

EventLevel = Literal["error", "warn", "info", "trace"]
EventCategory = Literal["llm", "tool", "db", "memory", "background", "agent", "http"]
HealthStatus = Literal["healthy", "degraded", "unhealthy"]

_STATUS_RANK = {"healthy": 0, "degraded": 1, "unhealthy": 2}


class EventRecord(BaseModel):
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    level: EventLevel
    category: EventCategory
    event: str
    user_id: int | None = None
    thread_id: int | None = None
    run_id: str | None = None
    data: dict[str, Any] | None = None
    duration_ms: float | None = None

    def to_jsonl(self) -> str:
        d: dict[str, Any] = {"ts": self.ts.isoformat()}
        d["level"] = self.level
        d["category"] = self.category
        d["event"] = self.event
        if self.user_id is not None:
            d["user_id"] = self.user_id
        if self.thread_id is not None:
            d["thread_id"] = self.thread_id
        if self.run_id is not None:
            d["run_id"] = self.run_id
        if self.data is not None:
            d["data"] = self.data
        if self.duration_ms is not None:
            d["duration_ms"] = self.duration_ms
        return json.dumps(d, separators=(",", ":")) + "\n"


class CheckResult(BaseModel):
    name: str
    status: HealthStatus
    message: str
    details: dict[str, Any]
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_ms: float


class HealthReport(BaseModel):
    status: HealthStatus
    checks: dict[str, CheckResult]
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def from_checks(cls, checks: dict[str, CheckResult]) -> HealthReport:
        worst = max(checks.values(), key=lambda c: _STATUS_RANK[c.status])
        return cls(status=worst.status, checks=checks)
