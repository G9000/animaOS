from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    d = tmp_path / "logs"
    d.mkdir()
    return d


def test_emit_creates_daily_file(log_dir: Path):
    from anima_server.services.health.event_logger import EventLogger

    logger = EventLogger(log_dir=log_dir)
    logger.emit("llm", "invoke", "info", data={"model": "qwen"})
    logger.flush()

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    log_file = log_dir / f"events-{today}.jsonl"
    assert log_file.exists()
    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["category"] == "llm"
    assert record["event"] == "invoke"
    assert record["data"]["model"] == "qwen"


def test_emit_multiple_events(log_dir: Path):
    from anima_server.services.health.event_logger import EventLogger

    logger = EventLogger(log_dir=log_dir, min_level="trace")
    logger.emit("llm", "invoke", "trace")
    logger.emit("tool", "error", "error", data={"tool": "recall_memory"})
    logger.emit("agent", "turn_start", "trace", user_id=1)
    logger.flush()

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    log_file = log_dir / f"events-{today}.jsonl"
    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 3


def test_emit_respects_min_level(log_dir: Path):
    from anima_server.services.health.event_logger import EventLogger

    logger = EventLogger(log_dir=log_dir, min_level="warn")
    logger.emit("llm", "invoke", "trace")  # below min_level, should be skipped
    logger.emit("llm", "invoke", "info")   # below min_level, should be skipped
    logger.emit("llm", "retry", "warn")    # at min_level, should be written
    logger.emit("llm", "failure", "error") # above min_level, should be written
    logger.flush()

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    log_file = log_dir / f"events-{today}.jsonl"
    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 2


def test_query_events_filters_by_category(log_dir: Path):
    from anima_server.services.health.event_logger import EventLogger

    logger = EventLogger(log_dir=log_dir, min_level="trace")
    logger.emit("llm", "invoke", "trace")
    logger.emit("tool", "execute", "trace")
    logger.emit("llm", "retry", "warn")
    logger.flush()

    results = logger.query_events(category="llm")
    assert len(results) == 2
    assert all(r.category == "llm" for r in results)


def test_query_events_filters_by_level(log_dir: Path):
    from anima_server.services.health.event_logger import EventLogger

    logger = EventLogger(log_dir=log_dir, min_level="trace")
    logger.emit("llm", "invoke", "trace")
    logger.emit("llm", "retry", "warn")
    logger.emit("llm", "failure", "error")
    logger.flush()

    results = logger.query_events(level="warn")
    assert len(results) == 1
    assert results[0].event == "retry"


def test_query_events_filters_by_since(log_dir: Path):
    from anima_server.services.health.event_logger import EventLogger

    logger = EventLogger(log_dir=log_dir, min_level="trace")
    logger.emit("llm", "invoke", "trace")
    logger.flush()

    # Query with since in the future should return nothing
    future = datetime.now(UTC) + timedelta(hours=1)
    results = logger.query_events(since=future)
    assert len(results) == 0


def test_query_events_limit(log_dir: Path):
    from anima_server.services.health.event_logger import EventLogger

    logger = EventLogger(log_dir=log_dir, min_level="trace")
    for i in range(10):
        logger.emit("llm", f"event_{i}", "trace")
    logger.flush()

    results = logger.query_events(limit=3)
    assert len(results) == 3


def test_cleanup_old_files(log_dir: Path):
    from anima_server.services.health.event_logger import EventLogger

    # Create fake old log files
    old_date = (datetime.now(UTC) - timedelta(days=10)).strftime("%Y-%m-%d")
    recent_date = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
    (log_dir / f"events-{old_date}.jsonl").write_text("")
    (log_dir / f"events-{recent_date}.jsonl").write_text("")

    logger = EventLogger(log_dir=log_dir, retention_days=7)
    logger.cleanup_old_logs()

    assert not (log_dir / f"events-{old_date}.jsonl").exists()
    assert (log_dir / f"events-{recent_date}.jsonl").exists()
