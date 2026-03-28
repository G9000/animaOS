from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query

from anima_server.services.health.event_logger import get_event_logger
from anima_server.services.health.models import EventCategory, EventLevel

router = APIRouter(prefix="/api/health", tags=["health"])


def _get_registry():
    from anima_server.services.health.registry import HealthCheckRegistry
    from anima_server.services.health.checks import (
        check_db_integrity,
        check_llm_connectivity,
        check_background_tasks,
    )

    registry = HealthCheckRegistry()
    registry.register("db_integrity", check_db_integrity)
    registry.register("llm_connectivity", lambda uid: check_llm_connectivity(uid))
    registry.register("background_tasks", lambda uid: check_background_tasks(uid))
    return registry


@router.get("/detailed")
async def health_detailed(user_id: int = 1) -> dict[str, Any]:
    registry = _get_registry()
    report = await registry.run_all(user_id=user_id)
    return report.model_dump(mode="json")


@router.get("/check/{name}")
async def health_check_one(name: str, user_id: int = 1) -> dict[str, Any]:
    registry = _get_registry()
    result = await registry.run_one(name, user_id=user_id)
    return result.model_dump(mode="json")


@router.get("/logs")
async def health_logs(
    category: str | None = None,
    level: str | None = None,
    since_hours: float = 24,
    limit: int = Query(default=100, le=1000),
) -> list[dict[str, Any]]:
    el = get_event_logger()
    since = datetime.now(UTC) - timedelta(hours=since_hours)
    events = el.query_events(
        category=category,
        level=level,
        since=since,
        limit=limit,
    )
    return [e.model_dump(mode="json") for e in events]


@router.get("/logs/summary")
async def health_logs_summary(hours: float = 24) -> dict[str, int]:
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
