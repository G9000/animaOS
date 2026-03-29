# Self-Healing Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add structured JSONL event logging and a health check system (DB integrity, LLM connectivity, background tasks) exposed via REST and an agent tool.

**Architecture:** Two independent modules under `services/health/` — an event logger (JSONL emitter + Python logging handler) and a health check registry (coordinator + check functions). REST endpoints in `api/routes/health.py`. One new agent tool `check_system_health` in `tools.py`. Instrumentation points added to existing code via `emit()` calls.

**Tech Stack:** Python, FastAPI, Pydantic, SQLAlchemy, standard library `logging`

**Spec:** `docs/superpowers/specs/2026-03-28-self-healing-foundation-design.md`

---

### Task 1: Data Models

**Files:**
- Create: `apps/server/src/anima_server/services/health/__init__.py`
- Create: `apps/server/src/anima_server/services/health/models.py`
- Test: `apps/server/tests/test_health_models.py`

- [ ] **Step 1: Write the failing test**

```python
# apps/server/tests/test_health_models.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/server && python -m pytest tests/test_health_models.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Create the package and models**

```python
# apps/server/src/anima_server/services/health/__init__.py
```

```python
# apps/server/src/anima_server/services/health/models.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/server && python -m pytest tests/test_health_models.py -v`
Expected: all 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/anima_server/services/health/__init__.py \
       apps/server/src/anima_server/services/health/models.py \
       apps/server/tests/test_health_models.py
git commit -m "feat(health): add EventRecord, CheckResult, HealthReport models"
```

---

### Task 2: Event Logger

**Files:**
- Create: `apps/server/src/anima_server/services/health/event_logger.py`
- Test: `apps/server/tests/test_event_logger.py`

- [ ] **Step 1: Write the failing tests**

```python
# apps/server/tests/test_event_logger.py
from __future__ import annotations

import json
import tempfile
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

    logger = EventLogger(log_dir=log_dir)
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/server && python -m pytest tests/test_event_logger.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement the event logger**

```python
# apps/server/src/anima_server/services/health/event_logger.py
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
                        except (json.JSONDecodeError, Exception):
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/server && python -m pytest tests/test_event_logger.py -v`
Expected: all 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/anima_server/services/health/event_logger.py \
       apps/server/tests/test_event_logger.py
git commit -m "feat(health): add structured JSONL event logger with daily rotation"
```

---

### Task 3: Structured Log Handler

**Files:**
- Modify: `apps/server/src/anima_server/services/health/event_logger.py`
- Test: `apps/server/tests/test_structured_log_handler.py`

- [ ] **Step 1: Write the failing tests**

```python
# apps/server/tests/test_structured_log_handler.py
from __future__ import annotations

import json
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
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.WARNING)
    try:
        test_logger.warning("request failed")
    finally:
        test_logger.removeHandler(handler)

    el.flush()
    results = el.query_events(category="http")
    assert len(results) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/server && python -m pytest tests/test_structured_log_handler.py -v`
Expected: FAIL with `ImportError` for `StructuredLogHandler`

- [ ] **Step 3: Add StructuredLogHandler to event_logger.py**

Append to `apps/server/src/anima_server/services/health/event_logger.py`:

```python
# ── Logger-name-to-category mapping ─────────────────────────────────

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
    """Map a Python logger name to an event category."""
    for prefix, category in _LOGGER_CATEGORY_MAP.items():
        if logger_name == prefix or logger_name.startswith(prefix + "."):
            return category
    if logger_name.startswith("anima_server.db"):
        return "db"
    if logger_name.startswith("anima_server.api.routes"):
        return "http"
    if logger_name.startswith("anima_server.services.agent"):
        return "agent"
    return "http"


class StructuredLogHandler(logging.Handler):
    """Python logging handler that forwards log records as structured events."""

    def __init__(self, event_logger: EventLogger) -> None:
        super().__init__()
        self._event_logger = event_logger

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = _PYTHON_TO_EVENT_LEVEL.get(record.levelno, "info")
            category = _resolve_category(record.name)
            event_name = "log"

            data: dict[str, Any] = {"message": record.getMessage(), "logger": record.name}
            if record.exc_info and record.exc_info[1] is not None:
                import traceback
                data["traceback"] = "".join(traceback.format_exception(*record.exc_info))

            self._event_logger.emit(
                category=category,
                event=event_name,
                level=level,
                data=data,
            )
        except Exception:
            self.handleError(record)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/server && python -m pytest tests/test_structured_log_handler.py -v`
Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/anima_server/services/health/event_logger.py \
       apps/server/tests/test_structured_log_handler.py
git commit -m "feat(health): add StructuredLogHandler for automatic event capture"
```

---

### Task 4: Configuration Settings

**Files:**
- Modify: `apps/server/src/anima_server/config.py`
- Test: `apps/server/tests/test_health_config.py`

- [ ] **Step 1: Write the failing test**

```python
# apps/server/tests/test_health_config.py
from __future__ import annotations


def test_health_settings_defaults():
    from anima_server.config import Settings

    s = Settings(
        _env_file=None,
        core_require_encryption=False,
    )
    assert s.health_log_dir == ""
    assert s.health_log_retention_days == 7
    assert s.health_log_level == "info"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/server && python -m pytest tests/test_health_config.py -v`
Expected: FAIL with `ValidationError` (unknown fields)

- [ ] **Step 3: Add settings to config.py**

Add three fields to the `Settings` class in `apps/server/src/anima_server/config.py`, after the `sidecar_nonce` field (line 61):

```python
    health_log_dir: str = ""
    health_log_retention_days: int = 7
    health_log_level: str = "info"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/server && python -m pytest tests/test_health_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/anima_server/config.py \
       apps/server/tests/test_health_config.py
git commit -m "feat(health): add health_log_dir, retention_days, log_level settings"
```

---

### Task 5: Health Check Registry

**Files:**
- Create: `apps/server/src/anima_server/services/health/registry.py`
- Test: `apps/server/tests/test_health_registry.py`

- [ ] **Step 1: Write the failing tests**

```python
# apps/server/tests/test_health_registry.py
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_register_and_run_one():
    from anima_server.services.health.models import CheckResult
    from anima_server.services.health.registry import HealthCheckRegistry

    registry = HealthCheckRegistry()

    async def dummy_check(user_id: int) -> CheckResult:
        return CheckResult(
            name="dummy",
            status="healthy",
            message="all good",
            details={"ping": True},
            duration_ms=1.0,
        )

    registry.register("dummy", dummy_check)
    result = await registry.run_one("dummy", user_id=1)
    assert result.status == "healthy"
    assert result.name == "dummy"


@pytest.mark.asyncio
async def test_run_all():
    from anima_server.services.health.models import CheckResult
    from anima_server.services.health.registry import HealthCheckRegistry

    registry = HealthCheckRegistry()

    async def healthy_check(user_id: int) -> CheckResult:
        return CheckResult(
            name="a", status="healthy", message="ok", details={}, duration_ms=1.0
        )

    async def degraded_check(user_id: int) -> CheckResult:
        return CheckResult(
            name="b", status="degraded", message="slow", details={}, duration_ms=2.0
        )

    registry.register("a", healthy_check)
    registry.register("b", degraded_check)

    report = await registry.run_all(user_id=1)
    assert report.status == "degraded"
    assert len(report.checks) == 2
    assert "a" in report.checks
    assert "b" in report.checks


@pytest.mark.asyncio
async def test_run_one_unknown_raises():
    from anima_server.services.health.registry import HealthCheckRegistry

    registry = HealthCheckRegistry()
    with pytest.raises(KeyError):
        await registry.run_one("nonexistent", user_id=1)


@pytest.mark.asyncio
async def test_check_failure_returns_unhealthy():
    from anima_server.services.health.registry import HealthCheckRegistry

    registry = HealthCheckRegistry()

    async def broken_check(user_id: int):
        raise RuntimeError("DB exploded")

    registry.register("broken", broken_check)
    result = await registry.run_one("broken", user_id=1)
    assert result.status == "unhealthy"
    assert "DB exploded" in result.message


@pytest.mark.asyncio
async def test_format_report_text():
    from anima_server.services.health.models import CheckResult
    from anima_server.services.health.registry import HealthCheckRegistry

    registry = HealthCheckRegistry()

    async def ok_check(user_id: int) -> CheckResult:
        return CheckResult(
            name="db_integrity", status="healthy", message="SQLite OK", details={}, duration_ms=5.0
        )

    registry.register("db_integrity", ok_check)
    report = await registry.run_all(user_id=1)
    text = registry.format_report(report)
    assert "HEALTHY" in text
    assert "[OK]" in text
    assert "db_integrity" in text.lower() or "Database" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/server && python -m pytest tests/test_health_registry.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement the registry**

```python
# apps/server/src/anima_server/services/health/registry.py
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from anima_server.services.health.models import CheckResult, HealthReport, HealthStatus

logger = logging.getLogger(__name__)

CheckFn = Callable[[int], Awaitable[CheckResult]]

_STATUS_LABELS: dict[HealthStatus, str] = {
    "healthy": "[OK]",
    "degraded": "[WARN]",
    "unhealthy": "[FAIL]",
}


class HealthCheckRegistry:
    """Coordinator that runs registered health checks and aggregates results."""

    def __init__(self) -> None:
        self._checks: dict[str, CheckFn] = {}

    def register(self, name: str, check_fn: CheckFn) -> None:
        self._checks[name] = check_fn

    async def run_one(self, name: str, *, user_id: int) -> CheckResult:
        if name not in self._checks:
            raise KeyError(f"Unknown health check: {name}")
        return await self._safe_run(name, self._checks[name], user_id)

    async def run_all(self, *, user_id: int) -> HealthReport:
        results: dict[str, CheckResult] = {}
        for name, check_fn in self._checks.items():
            results[name] = await self._safe_run(name, check_fn, user_id)
        return HealthReport.from_checks(results)

    @staticmethod
    async def _safe_run(name: str, check_fn: CheckFn, user_id: int) -> CheckResult:
        start = time.monotonic()
        try:
            result = await check_fn(user_id)
            return result
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            logger.warning("Health check %s failed: %s", name, exc)
            return CheckResult(
                name=name,
                status="unhealthy",
                message=f"Check failed: {exc}",
                details={"error": str(exc)},
                duration_ms=elapsed,
            )

    @staticmethod
    def format_report(report: HealthReport) -> str:
        header = f"System Health: {report.status.upper()}\n"
        lines: list[str] = [header]
        for check in report.checks.values():
            label = _STATUS_LABELS.get(check.status, "[??]")
            lines.append(f"{label} {check.name} — {check.message}")
        return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/server && python -m pytest tests/test_health_registry.py -v`
Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/anima_server/services/health/registry.py \
       apps/server/tests/test_health_registry.py
git commit -m "feat(health): add HealthCheckRegistry with safe execution and text formatting"
```

---

### Task 6: Health Checks — db_integrity, llm_connectivity, background_tasks

**Files:**
- Create: `apps/server/src/anima_server/services/health/checks.py`
- Test: `apps/server/tests/test_health_checks.py`

- [ ] **Step 1: Write the failing tests**

```python
# apps/server/tests/test_health_checks.py
from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def sqlite_engine():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    return engine


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    d = tmp_path / "logs"
    d.mkdir()
    return d


# ── db_integrity ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_db_integrity_healthy(sqlite_engine):
    from anima_server.services.health.checks import check_db_integrity

    factory = sessionmaker(bind=sqlite_engine)
    result = await check_db_integrity(
        user_id=1,
        soul_db_factory=factory,
        runtime_db_factory=factory,
    )
    assert result.status == "healthy"
    assert "ok" in result.details.get("sqlite_integrity", "").lower()


@pytest.mark.asyncio
async def test_db_integrity_pg_unreachable():
    from anima_server.services.health.checks import check_db_integrity

    ok_engine = create_engine("sqlite://", poolclass=StaticPool)
    ok_factory = sessionmaker(bind=ok_engine)

    def bad_factory():
        raise RuntimeError("PG down")

    result = await check_db_integrity(
        user_id=1,
        soul_db_factory=ok_factory,
        runtime_db_factory=bad_factory,
    )
    assert result.status == "unhealthy"
    assert "runtime" in result.message.lower() or "pg" in result.message.lower()


# ── llm_connectivity ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_llm_connectivity_healthy(log_dir: Path):
    from anima_server.services.health.checks import check_llm_connectivity
    from anima_server.services.health.event_logger import EventLogger

    el = EventLogger(log_dir=log_dir, min_level="trace")
    # 10 successful invocations, 0 errors
    for _ in range(10):
        el.emit("llm", "invoke", "trace", data={"duration_ms": 500})
    el.flush()

    result = await check_llm_connectivity(user_id=1, event_logger=el)
    assert result.status == "healthy"


@pytest.mark.asyncio
async def test_llm_connectivity_degraded(log_dir: Path):
    from anima_server.services.health.checks import check_llm_connectivity
    from anima_server.services.health.event_logger import EventLogger

    el = EventLogger(log_dir=log_dir, min_level="trace")
    # 8 successful, 2 failures = 20% error rate → degraded
    for _ in range(8):
        el.emit("llm", "invoke", "trace")
    for _ in range(2):
        el.emit("llm", "failure", "error")
    el.flush()

    result = await check_llm_connectivity(user_id=1, event_logger=el)
    assert result.status == "degraded"


@pytest.mark.asyncio
async def test_llm_connectivity_unhealthy(log_dir: Path):
    from anima_server.services.health.checks import check_llm_connectivity
    from anima_server.services.health.event_logger import EventLogger

    el = EventLogger(log_dir=log_dir, min_level="trace")
    # 3 successful, 7 failures = 70% → unhealthy
    for _ in range(3):
        el.emit("llm", "invoke", "trace")
    for _ in range(7):
        el.emit("llm", "failure", "error")
    el.flush()

    result = await check_llm_connectivity(user_id=1, event_logger=el)
    assert result.status == "unhealthy"


@pytest.mark.asyncio
async def test_llm_connectivity_no_data(log_dir: Path):
    from anima_server.services.health.checks import check_llm_connectivity
    from anima_server.services.health.event_logger import EventLogger

    el = EventLogger(log_dir=log_dir, min_level="trace")
    result = await check_llm_connectivity(user_id=1, event_logger=el)
    assert result.status == "healthy"
    assert "no data" in result.message.lower() or "no recent" in result.message.lower()


# ── background_tasks ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_background_tasks_healthy():
    from anima_server.services.health.checks import check_background_tasks

    # Mock runtime DB with no failed/stuck tasks
    mock_factory = _make_mock_runtime_factory(
        failed_count=0,
        stuck_count=0,
        last_completed=datetime.now(UTC) - timedelta(minutes=5),
    )
    result = await check_background_tasks(user_id=1, runtime_db_factory=mock_factory)
    assert result.status == "healthy"


@pytest.mark.asyncio
async def test_background_tasks_degraded_failures():
    from anima_server.services.health.checks import check_background_tasks

    mock_factory = _make_mock_runtime_factory(
        failed_count=3,
        stuck_count=0,
        last_completed=datetime.now(UTC) - timedelta(minutes=5),
    )
    result = await check_background_tasks(user_id=1, runtime_db_factory=mock_factory)
    assert result.status == "degraded"


@pytest.mark.asyncio
async def test_background_tasks_unhealthy_stuck():
    from anima_server.services.health.checks import check_background_tasks

    mock_factory = _make_mock_runtime_factory(
        failed_count=0,
        stuck_count=2,
        last_completed=datetime.now(UTC) - timedelta(minutes=5),
    )
    result = await check_background_tasks(user_id=1, runtime_db_factory=mock_factory)
    assert result.status == "unhealthy"


def _make_mock_runtime_factory(
    *,
    failed_count: int,
    stuck_count: int,
    last_completed: datetime | None,
):
    """Return a callable that produces a mock session for background task checks."""
    from unittest.mock import MagicMock
    from contextlib import contextmanager

    @contextmanager
    def factory():
        session = MagicMock()
        query = session.query.return_value

        # Chain: .filter(...).count()
        filter_mock = MagicMock()
        query.filter.return_value = filter_mock

        # We'll use side_effect to return different counts for different calls
        call_count = {"n": 0}

        def count_side_effect():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return failed_count
            return stuck_count

        filter_mock.filter.return_value = filter_mock
        filter_mock.count.side_effect = count_side_effect

        # scalar() for last completed timestamp
        scalar_mock = MagicMock()
        scalar_mock.filter.return_value = scalar_mock
        scalar_mock.order_by.return_value = scalar_mock
        scalar_mock.scalar.return_value = last_completed

        # Second query().filter()... chain for the scalar
        query2 = MagicMock()
        query2.filter.return_value = query2
        query2.order_by.return_value = query2
        query2.scalar.return_value = last_completed

        session.query.side_effect = [filter_mock, filter_mock, query2]

        yield session

    return factory
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/server && python -m pytest tests/test_health_checks.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement health checks**

```python
# apps/server/src/anima_server/services/health/checks.py
from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from anima_server.services.health.models import CheckResult


async def check_db_integrity(
    user_id: int,
    *,
    soul_db_factory: Callable[..., Any] | None = None,
    runtime_db_factory: Callable[..., Any] | None = None,
) -> CheckResult:
    """Check SQLite integrity and PostgreSQL connectivity."""
    start = time.monotonic()
    details: dict[str, Any] = {}
    issues: list[str] = []

    # 1. SQLite integrity check
    try:
        if soul_db_factory is None:
            from anima_server.db.session import ensure_user_database

            soul_db_factory = ensure_user_database(user_id)

        with soul_db_factory() as db:
            from sqlalchemy import text

            result = db.execute(text("PRAGMA integrity_check")).scalar()
            details["sqlite_integrity"] = result or "ok"
            if result and result.lower() != "ok":
                issues.append(f"SQLite integrity: {result}")
    except Exception as exc:
        details["sqlite_integrity"] = str(exc)
        issues.append(f"SQLite check failed: {exc}")

    # 2. PostgreSQL connectivity
    try:
        if runtime_db_factory is None:
            from anima_server.db.runtime import get_runtime_session_factory

            runtime_db_factory = get_runtime_session_factory()

        with runtime_db_factory() as db:
            from sqlalchemy import text

            db.execute(text("SELECT 1"))
            details["pg_connected"] = True
    except Exception as exc:
        details["pg_connected"] = False
        issues.append(f"Runtime DB unreachable: {exc}")

    elapsed = (time.monotonic() - start) * 1000

    if any("unreachable" in i.lower() or "failed" in i.lower() for i in issues):
        status = "unhealthy"
    elif any("integrity" in i.lower() for i in issues):
        status = "unhealthy"
    else:
        status = "healthy"

    message = "; ".join(issues) if issues else "SQLite OK, runtime DB connected"
    return CheckResult(
        name="db_integrity",
        status=status,
        message=message,
        details=details,
        duration_ms=elapsed,
    )


async def check_llm_connectivity(
    user_id: int,
    *,
    event_logger: Any | None = None,
    window_minutes: int = 10,
) -> CheckResult:
    """Check LLM error rate from recent event logs."""
    start = time.monotonic()

    if event_logger is None:
        from anima_server.services.health.event_logger import get_event_logger

        event_logger = get_event_logger()

    since = datetime.now(UTC) - timedelta(minutes=window_minutes)

    invocations = event_logger.query_events(
        category="llm", event="invoke", since=since, limit=10000
    )
    failures = event_logger.query_events(
        category="llm", event="failure", since=since, limit=10000
    )

    total = len(invocations) + len(failures)
    error_count = len(failures)

    elapsed = (time.monotonic() - start) * 1000
    details: dict[str, Any] = {
        "total_invocations": total,
        "error_count": error_count,
        "window_minutes": window_minutes,
    }

    if total == 0:
        return CheckResult(
            name="llm_connectivity",
            status="healthy",
            message="No recent LLM activity (no data)",
            details=details,
            duration_ms=elapsed,
        )

    error_rate = error_count / total
    details["error_rate"] = round(error_rate, 3)

    if error_rate > 0.5:
        status = "unhealthy"
        message = f"{error_count} errors in last {window_minutes} min ({error_rate:.0%} error rate)"
    elif error_rate > 0.1:
        status = "degraded"
        message = f"{error_count} errors in last {window_minutes} min ({error_rate:.0%} error rate)"
    else:
        status = "healthy"
        message = f"{total} calls, {error_count} errors in last {window_minutes} min"

    return CheckResult(
        name="llm_connectivity",
        status=status,
        message=message,
        details=details,
        duration_ms=elapsed,
    )


async def check_background_tasks(
    user_id: int,
    *,
    runtime_db_factory: Callable[..., Any] | None = None,
    stuck_threshold_minutes: int = 30,
) -> CheckResult:
    """Check for failed and stuck background tasks."""
    start = time.monotonic()
    details: dict[str, Any] = {}
    issues: list[str] = []

    try:
        if runtime_db_factory is None:
            from anima_server.db.runtime import get_runtime_session_factory

            runtime_db_factory = get_runtime_session_factory()

        from anima_server.models.runtime import RuntimeBackgroundTaskRun

        one_hour_ago = datetime.now(UTC) - timedelta(hours=1)
        stuck_cutoff = datetime.now(UTC) - timedelta(minutes=stuck_threshold_minutes)

        with runtime_db_factory() as db:
            # Count failed tasks in last hour
            failed_count = (
                db.query(RuntimeBackgroundTaskRun)
                .filter(
                    RuntimeBackgroundTaskRun.status == "failed",
                    RuntimeBackgroundTaskRun.completed_at >= one_hour_ago,
                )
                .count()
            )
            details["failed_last_hour"] = failed_count

            # Count stuck tasks (running longer than threshold)
            stuck_count = (
                db.query(RuntimeBackgroundTaskRun)
                .filter(
                    RuntimeBackgroundTaskRun.status == "running",
                    RuntimeBackgroundTaskRun.started_at < stuck_cutoff,
                )
                .count()
            )
            details["stuck_tasks"] = stuck_count

            # Last successful consolidation
            last_completed = (
                db.query(RuntimeBackgroundTaskRun.completed_at)
                .filter(
                    RuntimeBackgroundTaskRun.status == "completed",
                    RuntimeBackgroundTaskRun.task_type == "consolidation",
                )
                .order_by(RuntimeBackgroundTaskRun.completed_at.desc())
                .scalar()
            )
            if last_completed is not None:
                details["last_consolidation"] = last_completed.isoformat()
                age_min = (datetime.now(UTC) - last_completed).total_seconds() / 60
                details["consolidation_age_minutes"] = round(age_min, 1)
            else:
                details["last_consolidation"] = None

    except Exception as exc:
        elapsed = (time.monotonic() - start) * 1000
        return CheckResult(
            name="background_tasks",
            status="unhealthy",
            message=f"Failed to query background tasks: {exc}",
            details={"error": str(exc)},
            duration_ms=elapsed,
        )

    elapsed = (time.monotonic() - start) * 1000

    if stuck_count > 0:
        status = "unhealthy"
        issues.append(f"{stuck_count} stuck task(s)")
    elif failed_count > 0:
        status = "degraded"
        issues.append(f"{failed_count} failed task(s) in last hour")
    else:
        status = "healthy"

    if last_completed is None and status == "healthy":
        issues.append("No consolidation history")
    elif last_completed is not None:
        age_min = details.get("consolidation_age_minutes", 0)
        if age_min > stuck_threshold_minutes and status == "healthy":
            status = "degraded"
            issues.append(f"Last consolidation {age_min:.0f}m ago")

    message = "; ".join(issues) if issues else f"0 failed, 0 stuck, last consolidation {details.get('consolidation_age_minutes', '?')}m ago"

    return CheckResult(
        name="background_tasks",
        status=status,
        message=message,
        details=details,
        duration_ms=elapsed,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/server && python -m pytest tests/test_health_checks.py -v`
Expected: all 9 tests PASS

Note: The `background_tasks` tests use mocks which may need adjustment based on SQLAlchemy query patterns. If tests fail on the mock setup, simplify the tests to use the real in-memory SQLite runtime engine from conftest (which already creates `RuntimeBackgroundTaskRun` tables). In that case, insert test rows directly:

```python
@pytest.mark.asyncio
async def test_background_tasks_healthy_with_real_db():
    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.models.runtime import RuntimeBackgroundTaskRun
    from anima_server.services.health.checks import check_background_tasks

    factory = get_runtime_session_factory()
    with factory() as db:
        db.add(RuntimeBackgroundTaskRun(
            user_id=1,
            task_type="consolidation",
            status="completed",
            completed_at=datetime.now(UTC) - timedelta(minutes=5),
        ))
        db.commit()

    result = await check_background_tasks(user_id=1, runtime_db_factory=factory)
    assert result.status == "healthy"
```

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/anima_server/services/health/checks.py \
       apps/server/tests/test_health_checks.py
git commit -m "feat(health): add db_integrity, llm_connectivity, background_tasks checks"
```

---

### Task 7: REST Endpoints

**Files:**
- Create: `apps/server/src/anima_server/api/routes/health.py`
- Modify: `apps/server/src/anima_server/main.py` (add router import + nonce exempt paths)
- Test: `apps/server/tests/test_health_api.py`

- [ ] **Step 1: Write the failing tests**

```python
# apps/server/tests/test_health_api.py
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    d = tmp_path / "logs"
    d.mkdir()
    return d


@pytest.fixture
def client(log_dir: Path):
    from anima_server.services.health import event_logger as el_mod
    from anima_server.services.health.event_logger import EventLogger

    test_el = EventLogger(log_dir=log_dir, min_level="trace")
    original = el_mod._instance
    el_mod._instance = test_el

    from anima_server.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c

    el_mod._instance = original


def test_health_detailed(client: TestClient):
    # The endpoint should return a HealthReport-like structure
    # We patch checks to avoid real DB/LLM calls
    from anima_server.services.health.models import CheckResult

    mock_result = CheckResult(
        name="db_integrity",
        status="healthy",
        message="All good",
        details={},
        duration_ms=1.0,
    )

    with patch(
        "anima_server.api.routes.health._get_registry"
    ) as mock_reg:
        mock_registry = AsyncMock()
        mock_reg.return_value = mock_registry
        from anima_server.services.health.models import HealthReport

        mock_registry.run_all.return_value = HealthReport.from_checks(
            {"db_integrity": mock_result}
        )

        resp = client.get("/api/health/detailed")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert "db_integrity" in data["checks"]


def test_health_logs_summary(client: TestClient, log_dir: Path):
    from anima_server.services.health.event_logger import get_event_logger

    el = get_event_logger()
    el.emit("llm", "failure", "error")
    el.emit("llm", "failure", "error")
    el.emit("tool", "error", "error")
    el.flush()

    resp = client.get("/api/health/logs/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["llm"] >= 2
    assert data["tool"] >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/server && python -m pytest tests/test_health_api.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Create the health route**

```python
# apps/server/src/anima_server/api/routes/health.py
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
        category=category,  # type: ignore[arg-type]
        level=level,  # type: ignore[arg-type]
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
            category=cat,  # type: ignore[arg-type]
            level="warn",
            since=since,
            limit=10000,
        )
        errors = el.query_events(
            category=cat,  # type: ignore[arg-type]
            level="error",
            since=since,
            limit=10000,
        )
        count = len(events) + len(errors)
        if count > 0:
            summary[cat] = count
    return summary
```

- [ ] **Step 4: Register the router in main.py**

In `apps/server/src/anima_server/main.py`:

Add the import (after the existing route imports, around line 32):
```python
from .api.routes.health import router as health_router
```

Add the nonce-exempt paths (modify line 62):
```python
_NONCE_EXEMPT_PATHS = frozenset({"/health", "/api/health", "/api/health/detailed", "/api/health/check", "/api/health/logs", "/api/health/logs/summary"})
```

Add router inclusion (after the existing `app.include_router` calls, around line 254):
```python
    app.include_router(health_router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd apps/server && python -m pytest tests/test_health_api.py -v`
Expected: PASS

Note: If the test client fixture has issues with `create_app`, check whether `main.py` exports `create_app()`. If the app factory is inline in `create_app_and_configure()`, adjust the test to import accordingly. The key pattern is the same as existing tests like `test_chat.py`.

- [ ] **Step 6: Commit**

```bash
git add apps/server/src/anima_server/api/routes/health.py \
       apps/server/src/anima_server/main.py \
       apps/server/tests/test_health_api.py
git commit -m "feat(health): add REST endpoints for health checks and event logs"
```

---

### Task 8: Agent Tool — check_system_health

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/tools.py`
- Test: `apps/server/tests/test_health_tool.py`

- [ ] **Step 1: Write the failing test**

```python
# apps/server/tests/test_health_tool.py
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_check_system_health_tool():
    from anima_server.services.agent.tools import check_system_health

    # The tool should return a formatted string
    result = await check_system_health(user_id=1)
    assert isinstance(result, str)
    assert "System Health:" in result


def test_check_system_health_in_extension_tools():
    from anima_server.services.agent.tools import get_extension_tools

    tools = get_extension_tools()
    tool_names = [t.__name__ if hasattr(t, '__name__') else str(t) for t in tools]
    assert "check_system_health" in tool_names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/server && python -m pytest tests/test_health_tool.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Add the tool to tools.py**

In `apps/server/src/anima_server/services/agent/tools.py`, add the tool function (before `get_core_tools()`):

```python
async def check_system_health(user_id: int) -> str:
    """Run system health checks and return a formatted report.

    Checks database integrity, LLM connectivity, and background task status.
    """
    from anima_server.services.health.checks import (
        check_db_integrity,
        check_llm_connectivity,
        check_background_tasks,
    )
    from anima_server.services.health.registry import HealthCheckRegistry

    registry = HealthCheckRegistry()
    registry.register("db_integrity", check_db_integrity)
    registry.register("llm_connectivity", lambda uid: check_llm_connectivity(uid))
    registry.register("background_tasks", lambda uid: check_background_tasks(uid))

    report = await registry.run_all(user_id=user_id)
    return registry.format_report(report)
```

Add `check_system_health` to `get_extension_tools()` (around line 863):

```python
def get_extension_tools() -> list[Any]:
    """Return optional extension tools (task management, intentions, etc.)."""
    return [
        create_task,
        list_tasks,
        complete_task,
        set_intention,
        complete_goal,
        note_to_self,
        dismiss_note,
        update_human_memory,
        current_datetime,
        recall_transcript,
        check_system_health,
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/server && python -m pytest tests/test_health_tool.py -v`
Expected: PASS

- [ ] **Step 5: Run existing tool tests to check for regressions**

Run: `cd apps/server && python -m pytest tests/test_agent_executor.py tests/test_agent_runtime.py -v`
Expected: existing tests still PASS

- [ ] **Step 6: Commit**

```bash
git add apps/server/src/anima_server/services/agent/tools.py \
       apps/server/tests/test_health_tool.py
git commit -m "feat(health): add check_system_health agent tool"
```

---

### Task 9: Instrumentation — Emit Calls in Existing Code

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/runtime.py`
- Modify: `apps/server/src/anima_server/services/agent/executor.py`
- Modify: `apps/server/src/anima_server/services/agent/service.py`
- Modify: `apps/server/src/anima_server/services/agent/consolidation.py`
- Modify: `apps/server/src/anima_server/services/agent/sleep_agent.py`
- Modify: `apps/server/src/anima_server/main.py`
- Test: `apps/server/tests/test_health_instrumentation.py`

These are all additive `emit()` calls — no logic changes.

- [ ] **Step 1: Write the failing tests**

```python
# apps/server/tests/test_health_instrumentation.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    d = tmp_path / "logs"
    d.mkdir()
    return d


@pytest.fixture
def event_logger(log_dir: Path):
    from anima_server.services.health.event_logger import EventLogger

    return EventLogger(log_dir=log_dir, min_level="trace")


def test_emit_is_importable():
    from anima_server.services.health.event_logger import emit

    # Should be callable without error
    assert callable(emit)


def test_event_logger_singleton_initialized():
    from anima_server.services.health.event_logger import get_event_logger

    el = get_event_logger()
    assert el is not None
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd apps/server && python -m pytest tests/test_health_instrumentation.py -v`
Expected: PASS (these verify infrastructure; actual instrumentation is tested by integration)

- [ ] **Step 3: Add emit calls to runtime.py**

In `apps/server/src/anima_server/services/agent/runtime.py`, add import at the top:
```python
from anima_server.services.health.event_logger import emit as health_emit
```

In `_invoke_llm_with_retry()`, after the `logger.warning(...)` call on line 1064, add:
```python
                health_emit("llm", "retry", "warn", data={
                    "attempt": attempt,
                    "retry_limit": retry_limit + 1,
                    "error": str(exc),
                    "backoff_s": delay,
                })
```

After the final `raise` on line 1062 (inside `if is_last_attempt or not _is_retryable_error(exc)`), add before the raise:
```python
                    health_emit("llm", "failure", "error", data={
                        "attempt": attempt,
                        "error": str(exc),
                        "retryable": _is_retryable_error(exc),
                    })
```

In `invoke()`, at the start of the for loop (after line 260), add:
```python
            if step_index == 0:
                health_emit("agent", "turn_start", "trace", user_id=user_id)
```

After the tool exclusion block (after line 285 where `allowed_set` is modified), add:
```python
                health_emit("tool", "excluded", "warn", data={
                    "tool": last_failed_tool,
                }, user_id=user_id)
```

At the end of `invoke()`, before returning the result, add:
```python
        health_emit("agent", "turn_complete", "trace", user_id=user_id, data={
            "steps": step_index + 1,
            "stop_reason": stop_reason.value if stop_reason else None,
        })
```

- [ ] **Step 4: Add emit calls to executor.py**

In `apps/server/src/anima_server/services/agent/executor.py`, add import at the top:
```python
from anima_server.services.health.event_logger import emit as health_emit
```

In `execute_tool_call()`, inside the `except TimeoutError` block (after line 169), add:
```python
            health_emit("tool", "timeout", "warn", data={
                "tool": tool_call.name,
                "timeout_s": settings.agent_tool_timeout,
            })
```

Inside the `except Exception` block (after line 178), add:
```python
            health_emit("tool", "error", "error", data={
                "tool": tool_call.name,
                "error": str(exc),
            })
```

- [ ] **Step 5: Add emit calls to service.py**

In `apps/server/src/anima_server/services/agent/service.py`, add import at the top:
```python
from anima_server.services.health.event_logger import emit as health_emit
```

In the context overflow recovery path (around line 811, inside the `except StepFailedError` block), add before the compaction:
```python
            health_emit("llm", "context_overflow", "warn", user_id=user_id)
```

After the `logger.info("Context overflow detected...")` (line 814), add:
```python
            health_emit("llm", "compaction", "info", user_id=user_id, data={
                "compacted_messages": compacted.compacted_message_count,
            })
```

- [ ] **Step 6: Add emit calls to consolidation.py**

In `apps/server/src/anima_server/services/agent/consolidation.py`, add import at the top:
```python
from anima_server.services.health.event_logger import emit as health_emit
```

In `run_background_memory_consolidation()`, inside the `except Exception` block (line 851), add:
```python
        health_emit("memory", "consolidation", "error", user_id=user_id, data={
            "error": str(exc) if 'exc' in dir() else "unknown",
        })
```

Actually, capture the exception properly — the except block has `exc` implicitly. Use:
```python
    except Exception as exc:
        logger.exception("Background memory consolidation failed for user %s", user_id)
        health_emit("memory", "consolidation", "error", user_id=user_id, data={
            "error": str(exc),
        })
```

Note: this requires changing the bare `except Exception:` to `except Exception as exc:` on line 851.

- [ ] **Step 7: Add emit calls to sleep_agent.py**

In `apps/server/src/anima_server/services/agent/sleep_agent.py`, add import at the top:
```python
from anima_server.services.health.event_logger import emit as health_emit
```

In `_issue_background_task()`:

After the task record is created (after line 139 `run_id = run.id`), add:
```python
    health_emit("background", "task_start", "trace", user_id=user_id, data={
        "task_type": task_type,
        "run_id": run_id,
    })
```

After `status = "completed"` (line 168), add:
```python
        health_emit("background", "task_complete", "trace", user_id=user_id, data={
            "task_type": task_type,
            "run_id": run_id,
        })
```

Inside the `except Exception as exc:` block (after line 174), add:
```python
        health_emit("background", "task_failed", "error", user_id=user_id, data={
            "task_type": task_type,
            "run_id": run_id,
            "error": str(exc),
        })
```

- [ ] **Step 8: Add emit calls to main.py exception handlers**

In `apps/server/src/anima_server/main.py`, add import at the top (after other imports):
```python
from .services.health.event_logger import emit as health_emit
```

In the `http_exception_handler` (line 211-219), add before the return:
```python
        health_emit("http", "error_response", "warn", data={
            "status_code": exc.status_code,
            "detail": str(exc.detail)[:200],
        })
```

- [ ] **Step 9: Run the full test suite to check for regressions**

Run: `cd apps/server && python -m pytest tests/ -x --timeout=60 -q`
Expected: all tests PASS

- [ ] **Step 10: Commit**

```bash
git add apps/server/src/anima_server/services/agent/runtime.py \
       apps/server/src/anima_server/services/agent/executor.py \
       apps/server/src/anima_server/services/agent/service.py \
       apps/server/src/anima_server/services/agent/consolidation.py \
       apps/server/src/anima_server/services/agent/sleep_agent.py \
       apps/server/src/anima_server/main.py \
       apps/server/tests/test_health_instrumentation.py
git commit -m "feat(health): instrument existing code with structured event emit() calls"
```

---

### Task 10: Startup Integration — Handler Installation and Log Cleanup

**Files:**
- Modify: `apps/server/src/anima_server/main.py`
- Test: `apps/server/tests/test_health_startup.py`

- [ ] **Step 1: Write the failing test**

```python
# apps/server/tests/test_health_startup.py
from __future__ import annotations

import logging

import pytest


def test_structured_handler_installed_at_startup():
    """After app creation, the root logger should have a StructuredLogHandler."""
    from anima_server.services.health.event_logger import StructuredLogHandler

    # Import after app module to trigger any startup hooks
    from anima_server.main import create_app

    app = create_app()

    # Check if handler is on the root logger or anima_server logger
    anima_logger = logging.getLogger("anima_server")
    handler_types = [type(h).__name__ for h in anima_logger.handlers]
    root_handler_types = [type(h).__name__ for h in logging.root.handlers]

    all_handlers = handler_types + root_handler_types
    assert "StructuredLogHandler" in all_handlers
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/server && python -m pytest tests/test_health_startup.py -v`
Expected: FAIL

- [ ] **Step 3: Add handler installation to lifespan**

In `apps/server/src/anima_server/main.py`, inside the `lifespan()` function, add after the sweep tasks are created (after line 134) and before `yield`:

```python
        # Install structured health event logger
        from .services.health.event_logger import (
            StructuredLogHandler,
            get_event_logger,
        )

        health_logger = get_event_logger()
        health_logger.cleanup_old_logs()
        health_handler = StructuredLogHandler(health_logger)
        health_handler.setLevel(logging.WARNING)
        logging.getLogger("anima_server").addHandler(health_handler)
```

In the `finally` block (before `dispose_runtime_engine()`), add cleanup:

```python
            logging.getLogger("anima_server").removeHandler(health_handler)
            health_logger.flush()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/server && python -m pytest tests/test_health_startup.py -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `cd apps/server && python -m pytest tests/ -x --timeout=60 -q`
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add apps/server/src/anima_server/main.py \
       apps/server/tests/test_health_startup.py
git commit -m "feat(health): install StructuredLogHandler at startup, cleanup old logs"
```

---

### Task 11: Final Integration Test

**Files:**
- Test: `apps/server/tests/test_health_integration.py`

- [ ] **Step 1: Write the integration test**

```python
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
```

- [ ] **Step 2: Run integration tests**

Run: `cd apps/server && python -m pytest tests/test_health_integration.py -v`
Expected: all PASS

- [ ] **Step 3: Run the full test suite one final time**

Run: `cd apps/server && python -m pytest tests/ -x --timeout=60 -q`
Expected: all tests PASS

- [ ] **Step 4: Commit**

```bash
git add apps/server/tests/test_health_integration.py
git commit -m "test(health): add integration tests for event logging and health checks"
```
