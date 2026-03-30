from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query, Request

from anima_server.api.deps.unlock import require_unlocked_session
from anima_server.services.health.event_logger import get_event_logger
from anima_server.services.health.models import EventCategory, EventLevel
from anima_server.services.health.registry import get_default_registry

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("/detailed")
async def health_detailed(request: Request) -> dict[str, Any]:
    session = require_unlocked_session(request)
    registry = get_default_registry()
    report = await registry.run_all(user_id=session.user_id)
    return report.model_dump(mode="json")


@router.get("/check/{name}")
async def health_check_one(name: str, request: Request) -> dict[str, Any]:
    session = require_unlocked_session(request)
    registry = get_default_registry()
    result = await registry.run_one(name, user_id=session.user_id)
    return result.model_dump(mode="json")


@router.get("/logs")
async def health_logs(
    request: Request,
    category: str | None = None,
    level: str | None = None,
    since_hours: float = 24,
    limit: int = Query(default=100, le=1000),
) -> list[dict[str, Any]]:
    session = require_unlocked_session(request)
    el = get_event_logger()
    since = datetime.now(UTC) - timedelta(hours=since_hours)
    events = el.query_events(
        category=category,
        level=level,
        since=since,
        user_id=session.user_id,
        limit=limit,
    )
    return [e.model_dump(mode="json") for e in events]


@router.get("/logs/summary")
async def health_logs_summary(request: Request, hours: float = 24) -> dict[str, int]:
    # NOTE: Summary shows aggregate warn/error counts across all users.
    # This is intentional — it's operational data (category-level counts),
    # not user-private event details. The shared JSONL log doesn't support
    # efficient per-user aggregation. For user-specific events, use /logs.
    require_unlocked_session(request)
    el = get_event_logger()
    since = datetime.now(UTC) - timedelta(hours=hours)

    categories = ["llm", "tool", "db", "memory", "background", "agent", "http"]
    summary: dict[str, int] = {}
    for cat in categories:
        events = el.query_events(
            category=cat,
            level="warn",
            since=since,
            limit=10000,
        )
        errors = el.query_events(
            category=cat,
            level="error",
            since=since,
            limit=10000,
        )
        count = len(events) + len(errors)
        if count > 0:
            summary[cat] = count
    return summary
