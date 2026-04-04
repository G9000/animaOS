from __future__ import annotations

import logging
from pathlib import Path

import pytest


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    d = tmp_path / "logs"
    d.mkdir()
    return d


def test_handler_captures_warning(log_dir: Path):
    from anima_server.services.health.event_logger import EventLogger, StructuredLogHandler

    el = EventLogger(log_dir=log_dir, min_level="trace")
    handler = StructuredLogHandler(el)

    test_logger = logging.getLogger("anima_server.services.agent.runtime")
    test_logger.disabled = False  # Alembic fileConfig may disable existing loggers
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.WARNING)
    try:
        test_logger.warning("LLM call failed: timeout")
    finally:
        test_logger.removeHandler(handler)

    el.flush()
    results = el.query_events(category="llm")
    assert len(results) == 1
    assert results[0].level == "warn"
    assert results[0].data is not None
    assert "LLM call failed: timeout" in results[0].data.get("message", "")


def test_handler_captures_exception(log_dir: Path):
    from anima_server.services.health.event_logger import EventLogger, StructuredLogHandler

    el = EventLogger(log_dir=log_dir, min_level="trace")
    handler = StructuredLogHandler(el)

    test_logger = logging.getLogger("anima_server.services.agent.executor")
    test_logger.disabled = False  # Alembic fileConfig may disable existing loggers
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.ERROR)
    try:
        try:
            raise ValueError("bad args")
        except ValueError:
            test_logger.exception("Tool crashed")
    finally:
        test_logger.removeHandler(handler)

    el.flush()
    results = el.query_events(category="tool")
    assert len(results) == 1
    assert results[0].level == "error"
    assert "ValueError" in results[0].data.get("traceback", "")


def test_handler_maps_unknown_logger_to_agent(log_dir: Path):
    from anima_server.services.health.event_logger import EventLogger, StructuredLogHandler

    el = EventLogger(log_dir=log_dir, min_level="trace")
    handler = StructuredLogHandler(el)

    test_logger = logging.getLogger("anima_server.services.agent.something_new")
    test_logger.disabled = False  # Alembic fileConfig may disable existing loggers
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.WARNING)
    try:
        test_logger.warning("unknown module log")
    finally:
        test_logger.removeHandler(handler)

    el.flush()
    results = el.query_events(category="agent")
    assert len(results) == 1


def test_handler_maps_db_logger(log_dir: Path):
    from anima_server.services.health.event_logger import EventLogger, StructuredLogHandler

    el = EventLogger(log_dir=log_dir, min_level="trace")
    handler = StructuredLogHandler(el)

    test_logger = logging.getLogger("anima_server.db.session")
    test_logger.disabled = False  # Alembic fileConfig may disable existing loggers
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.WARNING)
    try:
        test_logger.warning("database locked")
    finally:
        test_logger.removeHandler(handler)

    el.flush()
    results = el.query_events(category="db")
    assert len(results) == 1


def test_handler_maps_route_logger_to_http(log_dir: Path):
    from anima_server.services.health.event_logger import EventLogger, StructuredLogHandler

    el = EventLogger(log_dir=log_dir, min_level="trace")
    handler = StructuredLogHandler(el)

    test_logger = logging.getLogger("anima_server.api.routes.chat")
    test_logger.disabled = False  # Alembic fileConfig may disable existing loggers
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.WARNING)
    try:
        test_logger.warning("request failed")
    finally:
        test_logger.removeHandler(handler)

    el.flush()
    results = el.query_events(category="http")
    assert len(results) == 1
