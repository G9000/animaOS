# apps/server/tests/test_health_integration.py
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    d = tmp_path / "logs"
    d.mkdir()
    return d


@pytest.mark.asyncio
async def test_full_health_check_flow(log_dir: Path):
    """End-to-end: emit events, run health checks, verify report."""
    from anima_server.services.health.event_logger import EventLogger
    from anima_server.services.health.checks import check_llm_connectivity
    from anima_server.services.health.registry import HealthCheckRegistry

    el = EventLogger(log_dir=log_dir, min_level="trace")

    # Simulate some LLM activity
    for _ in range(9):
        el.emit("llm", "invoke", "trace")
    el.emit("llm", "failure", "error", data={"error": "timeout"})
    el.flush()

    # Run check
    result = await check_llm_connectivity(user_id=1, event_logger=el)
    assert result.status == "healthy"  # 10% error rate = at threshold = healthy
    assert result.details["error_count"] == 1

    # Build a registry and format
    registry = HealthCheckRegistry()
    registry.register("llm_connectivity", lambda uid: check_llm_connectivity(uid, event_logger=el))
    report = await registry.run_all(user_id=1)
    text = registry.format_report(report)
    assert "System Health:" in text


def test_event_record_roundtrip(log_dir: Path):
    """Write an event, read it back, verify fields match."""
    from anima_server.services.health.event_logger import EventLogger

    el = EventLogger(log_dir=log_dir, min_level="trace")
    el.emit("tool", "timeout", "warn", data={"tool": "recall_memory", "timeout_s": 30}, user_id=1)
    el.flush()

    results = el.query_events(category="tool", event="timeout")
    assert len(results) == 1
    r = results[0]
    assert r.category == "tool"
    assert r.event == "timeout"
    assert r.level == "warn"
    assert r.user_id == 1
    assert r.data["tool"] == "recall_memory"
