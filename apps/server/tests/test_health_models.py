from __future__ import annotations

from datetime import UTC, datetime

import pytest


def test_event_record_serializes_to_json():
    from anima_server.services.health.models import EventRecord

    record = EventRecord(
        level="error",
        category="llm",
        event="llm_failure",
        data={"model": "qwen", "error": "timeout"},
        user_id=1,
        thread_id=42,
        run_id="abc-123",
        duration_ms=1500.5,
    )
    line = record.to_jsonl()
    assert '"level":"error"' in line or '"level": "error"' in line
    assert '"category":"llm"' in line or '"category": "llm"' in line
    assert "abc-123" in line
    assert line.endswith("\n")


def test_event_record_ts_auto_generated():
    from anima_server.services.health.models import EventRecord

    record = EventRecord(level="info", category="agent", event="turn_start")
    assert record.ts is not None


def test_event_record_minimal():
    from anima_server.services.health.models import EventRecord

    record = EventRecord(level="trace", category="tool", event="execute")
    line = record.to_jsonl()
    assert '"event":"execute"' in line or '"event": "execute"' in line
    assert "user_id" not in line  # optional fields omitted when None


def test_check_result_creation():
    from anima_server.services.health.models import CheckResult

    result = CheckResult(
        name="db_integrity",
        status="healthy",
        message="All good",
        details={"sqlite": "ok", "pg": "ok"},
        duration_ms=12.3,
    )
    assert result.status == "healthy"
    assert result.checked_at is not None


def test_health_report_aggregate_status():
    from anima_server.services.health.models import CheckResult, HealthReport

    checks = {
        "db": CheckResult(
            name="db", status="healthy", message="ok", details={}, duration_ms=1.0
        ),
        "llm": CheckResult(
            name="llm", status="degraded", message="slow", details={}, duration_ms=2.0
        ),
    }
    report = HealthReport.from_checks(checks)
    assert report.status == "degraded"


def test_health_report_unhealthy_wins():
    from anima_server.services.health.models import CheckResult, HealthReport

    checks = {
        "db": CheckResult(
            name="db", status="unhealthy", message="corrupt", details={}, duration_ms=1.0
        ),
        "llm": CheckResult(
            name="llm", status="degraded", message="slow", details={}, duration_ms=2.0
        ),
    }
    report = HealthReport.from_checks(checks)
    assert report.status == "unhealthy"


def test_health_report_all_healthy():
    from anima_server.services.health.models import CheckResult, HealthReport

    checks = {
        "a": CheckResult(name="a", status="healthy", message="ok", details={}, duration_ms=1.0),
        "b": CheckResult(name="b", status="healthy", message="ok", details={}, duration_ms=1.0),
    }
    report = HealthReport.from_checks(checks)
    assert report.status == "healthy"
