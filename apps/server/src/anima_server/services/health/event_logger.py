from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from anima_server.services.health.models import EventCategory, EventLevel, EventRecord

_LEVEL_RANK = {"trace": 0, "info": 1, "warn": 2, "error": 3}

logger = logging.getLogger(__name__)


class EventLogger:
    """Structured JSONL event logger with daily rotation."""

    def __init__(
        self,
        log_dir: Path,
        min_level: EventLevel = "info",
        retention_days: int = 7,
    ) -> None:
        self._log_dir = log_dir
        self._min_level = min_level
        self._retention_days = retention_days
        self._buffer: list[str] = []
        self._lock = threading.Lock()
        self._log_dir.mkdir(parents=True, exist_ok=True)

    def emit(
        self,
        category: EventCategory,
        event: str,
        level: EventLevel,
        *,
        data: dict[str, Any] | None = None,
        user_id: int | None = None,
        thread_id: int | None = None,
        run_id: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        if _LEVEL_RANK.get(level, 0) < _LEVEL_RANK.get(self._min_level, 0):
            return

        record = EventRecord(
            level=level,
            category=category,
            event=event,
            data=data,
            user_id=user_id,
            thread_id=thread_id,
            run_id=run_id,
            duration_ms=duration_ms,
        )
        line = record.to_jsonl()

        with self._lock:
            self._buffer.append(line)

        # Flush immediately on error/warn for visibility
        if level in ("error", "warn"):
            self.flush()

    def flush(self) -> None:
        with self._lock:
            if not self._buffer:
                return
            lines = self._buffer[:]
            self._buffer.clear()

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        log_file = self._log_dir / f"events-{today}.jsonl"
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.writelines(lines)
        except OSError:
            logger.debug("Failed to write health event log to %s", log_file)

    def query_events(
        self,
        *,
        category: EventCategory | None = None,
        level: EventLevel | None = None,
        event: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        user_id: int | None = None,
        limit: int = 100,
    ) -> list[EventRecord]:
        # Flush pending buffer first so queries see recent events
        self.flush()

        results: list[EventRecord] = []
        log_files = sorted(self._log_dir.glob("events-*.jsonl"))

        for log_file in log_files:
            try:
                with open(log_file, encoding="utf-8") as f:
                    for raw_line in f:
                        raw_line = raw_line.strip()
                        if not raw_line:
                            continue
                        try:
                            data = json.loads(raw_line)
                            record = EventRecord(**data)
                        except Exception:
                            continue

                        if category and record.category != category:
                            continue
                        if level and record.level != level:
                            continue
                        if event and record.event != event:
                            continue
                        if since and record.ts < since:
                            continue
                        if until and record.ts > until:
                            continue
                        if user_id is not None and record.user_id != user_id:
                            continue

                        results.append(record)
                        if len(results) >= limit:
                            return results
            except OSError:
                continue

        return results

    def cleanup_old_logs(self) -> None:
        cutoff = datetime.now(UTC) - timedelta(days=self._retention_days)
        for log_file in self._log_dir.glob("events-*.jsonl"):
            try:
                # Extract date from filename: events-YYYY-MM-DD.jsonl
                date_str = log_file.stem.replace("events-", "")
                file_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
                if file_date < cutoff:
                    log_file.unlink()
            except (ValueError, OSError):
                continue


# ── Module-level singleton ──────────────────────────────────────────

_instance: EventLogger | None = None
_instance_lock = threading.Lock()


def get_event_logger() -> EventLogger:
    """Return the module-level EventLogger singleton.

    Lazily initializes on first call using the configured log directory.
    """
    global _instance
    if _instance is not None:
        return _instance
    with _instance_lock:
        if _instance is not None:
            return _instance
        from anima_server.config import settings

        log_dir = Path(settings.health_log_dir) if settings.health_log_dir else settings.data_dir / "logs"
        _instance = EventLogger(
            log_dir=log_dir,
            min_level=settings.health_log_level,  # type: ignore[arg-type]
            retention_days=settings.health_log_retention_days,
        )
        return _instance


def emit(
    category: EventCategory,
    event: str,
    level: EventLevel,
    **kwargs: Any,
) -> None:
    """Module-level convenience for ``get_event_logger().emit(...)``."""
    get_event_logger().emit(category, event, level, **kwargs)


# ── Structured Log Handler ──────────────────────────────────────────

import traceback as _traceback_mod

_LOGGER_CATEGORY_MAP: dict[str, EventCategory] = {
    "anima_server.services.agent.runtime": "llm",
    "anima_server.services.agent.llm": "llm",
    "anima_server.services.agent.executor": "tool",
    "anima_server.services.agent.consolidation": "memory",
    "anima_server.services.agent.embeddings": "memory",
    "anima_server.services.agent.sleep_agent": "background",
    "anima_server.services.agent.sleep_tasks": "background",
    "anima_server.services.agent.service": "agent",
}

_PYTHON_TO_EVENT_LEVEL: dict[int, EventLevel] = {
    logging.DEBUG: "trace",
    logging.INFO: "info",
    logging.WARNING: "warn",
    logging.ERROR: "error",
    logging.CRITICAL: "error",
}


def _resolve_category(logger_name: str) -> EventCategory:
    """Map a Python logger name to an EventCategory."""
    # Exact match
    if logger_name in _LOGGER_CATEGORY_MAP:
        return _LOGGER_CATEGORY_MAP[logger_name]

    # Prefix match (logger_name starts with a mapped key + ".")
    for prefix, category in _LOGGER_CATEGORY_MAP.items():
        if logger_name.startswith(prefix + "."):
            return category

    # Fallback rules
    if logger_name.startswith("anima_server.db"):
        return "db"
    if logger_name.startswith("anima_server.api.routes"):
        return "http"
    if logger_name.startswith("anima_server.services.agent"):
        return "agent"

    return "http"


class StructuredLogHandler(logging.Handler):
    """Intercepts Python logging calls and forwards them as structured events."""

    def __init__(self, event_logger: EventLogger) -> None:
        super().__init__()
        self._event_logger = event_logger

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = _PYTHON_TO_EVENT_LEVEL.get(record.levelno, "info")
            category = _resolve_category(record.name)

            data: dict[str, Any] = {
                "message": record.getMessage(),
                "logger": record.name,
            }

            if record.exc_info and record.exc_info[1] is not None:
                data["traceback"] = "".join(
                    _traceback_mod.format_exception(*record.exc_info)
                )

            self._event_logger.emit(
                category=category,
                event="log",
                level=level,
                data=data,
            )
        except Exception:
            self.handleError(record)
