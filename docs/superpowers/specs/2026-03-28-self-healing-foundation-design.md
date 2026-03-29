# Self-Healing Foundation — Design Spec

**Date:** 2026-03-28
**Status:** Draft
**Scope:** Structured event logging + health check system (v1)

## Overview

AnimaOS has resilience patterns scattered across the codebase (LLM retry with backoff, tool failure exclusion, context overflow recovery, SQLite write retry, stale lockfile recovery) but lacks centralized observability and self-diagnostic capability. This spec defines a two-part foundation:

1. **Structured Event Logger** — JSONL files capturing errors, health signals, and operational traces across all layers
2. **Health Check Registry** — on-demand diagnostic checks exposed via REST endpoints and an agent tool

These two systems are independent but composable. The event logger provides the data substrate; health checks consume recent logs alongside direct system queries to produce diagnostic reports. Together they form the foundation for a future AI-driven repair pipeline (error trigger → trace → AI diagnosis → auto-fix or human-in-the-loop).

## Non-Goals (v1)

- AI analysis pipeline / auto-fix actions
- Alerting or notification system
- Memory, embedding, or storage health checks
- WebSocket push for real-time health updates
- Log shipping to external systems
- Circuit breaker patterns

---

## 1. Structured Event Logger

### 1.1 Responsibility

Capture structured events from all server layers and write them as JSONL to daily-rotated log files in the `.anima` data directory.

### 1.2 Event Schema

Each JSONL line:

```json
{
  "ts": "2026-03-28T23:15:00.123456Z",
  "level": "error",
  "category": "llm",
  "event": "llm_retry",
  "user_id": 1,
  "thread_id": 42,
  "run_id": "abc-123",
  "data": {
    "attempt": 2,
    "error": "rate_limit_exceeded",
    "backoff_ms": 1000
  },
  "duration_ms": 1234
}
```

**Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `ts` | ISO 8601 string | yes | UTC timestamp with microsecond precision |
| `level` | enum | yes | `error`, `warn`, `info`, `trace` |
| `category` | enum | yes | `llm`, `tool`, `db`, `memory`, `background`, `agent`, `http` |
| `event` | string | yes | Specific event name within category |
| `user_id` | int | no | User context if available |
| `thread_id` | int | no | Thread context if available |
| `run_id` | string | no | Run context if available |
| `data` | object | no | Event-specific payload |
| `duration_ms` | float | no | Duration of the operation |

### 1.3 Event Catalog

| Category | Event | Level | When |
|----------|-------|-------|------|
| `llm` | `invoke` | `trace` | Every LLM call (model, token counts) |
| `llm` | `retry` | `warn` | Each retry attempt (attempt #, error, backoff) |
| `llm` | `failure` | `error` | Final LLM failure after retries exhausted |
| `llm` | `context_overflow` | `warn` | Context window exceeded |
| `llm` | `compaction` | `info` | Emergency or proactive compaction triggered |
| `tool` | `execute` | `trace` | Tool execution (tool name, duration) |
| `tool` | `timeout` | `warn` | Tool exceeded `agent_tool_timeout` |
| `tool` | `error` | `error` | Tool raised an exception |
| `tool` | `excluded` | `warn` | Tool excluded after consecutive failures |
| `db` | `locked` | `warn` | SQLite "database is locked" retry |
| `db` | `migration` | `info` | Alembic migration applied |
| `db` | `integrity_error` | `error` | Database constraint violation or corruption |
| `memory` | `consolidation` | `trace` | Consolidation run (items processed) |
| `memory` | `extraction_failed` | `warn` | LLM extraction returned empty/failed |
| `memory` | `conflict` | `info` | Memory conflict detected and resolved |
| `memory` | `embedding_failed` | `warn` | Embedding generation returned None |
| `background` | `task_start` | `trace` | Background task begins |
| `background` | `task_complete` | `trace` | Background task finishes successfully |
| `background` | `task_failed` | `error` | Background task raised an exception |
| `agent` | `turn_start` | `trace` | Agent turn begins (user message received) |
| `agent` | `turn_complete` | `trace` | Agent turn finishes (stop reason, step count) |
| `agent` | `step` | `trace` | Individual agent step (tool calls made) |
| `agent` | `cancel` | `info` | Agent turn cancelled |
| `http` | `error_response` | `warn` | HTTP 4xx/5xx response |

### 1.4 Implementation

**`services/health/event_logger.py`:**

- `emit(category, event, level, data, user_id, thread_id, run_id, duration_ms)` — the primary API. Serializes to JSON and appends to the current day's log file. Non-blocking: writes are buffered and flushed periodically or on `error`/`warn` level events.
- `StructuredLogHandler(logging.Handler)` — a Python logging handler that intercepts `logger.warning()`, `logger.error()`, and `logger.exception()` calls and forwards them as structured events. Maps the logger name to a category (e.g., `anima_server.services.agent.runtime` → `llm`). Installed once at startup via `logging.root.addHandler()`.
- `query_events(category, level, event, since, until, limit)` — reads recent log files and returns matching events. Used by health checks to compute error rates and trends.
- Daily rotation: one file per day (`events-YYYY-MM-DD.jsonl`). On startup, delete files older than `health_log_retention_days`.

**Log directory:** `{settings.data_dir}/logs/` (e.g., `.anima/dev/logs/events-2026-03-28.jsonl`).

### 1.5 Logger-to-Category Mapping

The `StructuredLogHandler` maps Python logger names to event categories:

```python
_LOGGER_CATEGORY_MAP = {
    "anima_server.services.agent.runtime": "llm",
    "anima_server.services.agent.llm": "llm",
    "anima_server.services.agent.executor": "tool",
    "anima_server.services.agent.consolidation": "memory",
    "anima_server.services.agent.embeddings": "memory",
    "anima_server.services.agent.sleep_agent": "background",
    "anima_server.services.agent.sleep_tasks": "background",
    "anima_server.services.agent.service": "agent",
    "anima_server.db": "db",
}
```

Unmapped loggers default to category `"http"` for route handlers or `"agent"` for everything else under `services.agent`.

---

## 2. Health Check Registry

### 2.1 Responsibility

Run diagnostic checks against system components on demand. Aggregate results into a unified health report. Expose via REST and agent tool.

### 2.2 Data Models

**`services/health/models.py`:**

```python
class CheckResult(BaseModel):
    name: str
    status: Literal["healthy", "degraded", "unhealthy"]
    message: str
    details: dict[str, Any]
    checked_at: datetime
    duration_ms: float

class HealthReport(BaseModel):
    status: Literal["healthy", "degraded", "unhealthy"]
    checks: dict[str, CheckResult]
    checked_at: datetime
```

Aggregate `status` is the worst status among all checks (`unhealthy` > `degraded` > `healthy`).

### 2.3 Health Check Registry

**`services/health/registry.py`:**

- `register(name, check_fn)` — registers a named async check function `() -> CheckResult`
- `run_all(user_id) -> HealthReport` — runs all registered checks, returns aggregate
- `run_one(name, user_id) -> CheckResult` — runs a single named check

Checks receive a `user_id` parameter because some checks (DB integrity) are per-user.

### 2.4 v1 Health Checks

**`services/health/checks.py`:**

#### 2.4.1 `db_integrity`

1. Run `PRAGMA integrity_check` on the user's SQLite database
2. Test PostgreSQL runtime connection with `SELECT 1`
3. Check for WAL file presence and report its existence
4. **healthy:** both pass, **unhealthy:** integrity check fails or PG unreachable. WAL presence is expected (WAL mode is intentionally enabled) and is reported in details but does not affect status.

#### 2.4.2 `llm_connectivity`

1. Query recent event logs for LLM error/retry events in the last 10 minutes
2. Compute error rate (errors / total invocations)
3. If error rate > 50%: **unhealthy**. If error rate > 10%: **degraded**. Otherwise: **healthy**.
4. Report: error count, total invocations, average latency from recent `llm.invoke` trace events

No live LLM probe in v1 — we rely on recent operational data from the event logger. This avoids adding latency to health checks and wasting LLM tokens.

#### 2.4.3 `background_tasks`

1. Query `RuntimeBackgroundTaskRun` for tasks with status `'failed'` in the last hour
2. Query for tasks stuck in `'running'` status longer than `consolidation_health_threshold_minutes` (30 min default — the existing unused config value, now put to use)
3. Find the timestamp of the last successful consolidation task
4. **healthy:** no failures, no stuck tasks, last consolidation < 30 min ago. **degraded:** some failures or consolidation stale. **unhealthy:** stuck tasks detected.

---

## 3. REST Endpoints

**New route file: `api/routes/health.py`**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health/detailed` | Run all checks, return `HealthReport` |
| `GET` | `/api/health/check/{name}` | Run a single check, return `CheckResult` |
| `GET` | `/api/health/logs` | Query events with filters: `category`, `level`, `since`, `until`, `limit` (default 100) |
| `GET` | `/api/health/logs/summary` | Error counts grouped by category over last N hours (default 24) |

The existing `/health` and `/api/health` liveness endpoints remain unchanged.

All health endpoints are authenticated (require valid user session) but exempt from sidecar nonce validation (added to `_NONCE_EXEMPT_PATHS`).

---

## 4. Agent Tool

### 4.1 `check_system_health`

Added to `get_extension_tools()` in `tools.py`. When invoked:

1. Calls `registry.run_all(user_id)` to execute all health checks
2. Formats the `HealthReport` into a concise text summary:

```
System Health: DEGRADED

[OK] Database Integrity — SQLite OK, PG connected
[WARN] LLM Connectivity — 3 errors in last 10 min (15% error rate), avg latency 2.1s
[OK] Background Tasks — Last consolidation 4m ago, 0 failed, 0 stuck
```

3. Returns the formatted string as the tool result

The agent can use this to answer user questions about system status, or reason about issues it encounters during operation.

---

## 5. Instrumentation Points

Explicit `emit()` calls added to existing code. These are additive — no existing logic changes.

| File | Function/Location | Event |
|------|-------------------|-------|
| `services/agent/runtime.py` | `_invoke_llm_with_retry()` — on retry | `llm.retry` |
| `services/agent/runtime.py` | `_invoke_llm_with_retry()` — on final failure | `llm.failure` |
| `services/agent/runtime.py` | `invoke()` — turn start/end | `agent.turn_start`, `agent.turn_complete` |
| `services/agent/runtime.py` | `invoke()` — tool excluded | `tool.excluded` |
| `services/agent/executor.py` | `execute_tool_call()` — on timeout | `tool.timeout` |
| `services/agent/executor.py` | `execute_tool_call()` — on error | `tool.error` |
| `services/agent/service.py` | context overflow recovery | `llm.context_overflow`, `llm.compaction` |
| `services/agent/consolidation.py` | `run_background_memory_consolidation()` — on failure | `memory.consolidation` (error) |
| `services/agent/consolidation.py` | extraction fallback | `memory.extraction_failed` |
| `services/agent/sleep_agent.py` | `_issue_background_task()` — start/complete/fail | `background.task_start/complete/failed` |
| `main.py` | global exception handlers | `http.error_response` |

---

## 6. Configuration

New fields in `Settings` (config.py):

```python
health_log_dir: str = ""                # defaults to {data_dir}/logs
health_log_retention_days: int = 7      # delete log files older than this
health_log_level: str = "info"          # minimum level to write to JSONL (trace/info/warn/error)
```

The existing `consolidation_health_threshold_minutes: int = 30` is now used by the `background_tasks` check.

---

## 7. File Structure

```
services/health/
├── __init__.py
├── event_logger.py      # JSONL emitter + StructuredLogHandler
├── registry.py          # Health check coordinator
├── checks.py            # db_integrity, llm_connectivity, background_tasks
└── models.py            # CheckResult, HealthReport, EventRecord

api/routes/health.py     # REST endpoints
```

**Dependency direction (no cycles):**

```
event_logger.py  ← standalone, no health module deps
     ↑
checks.py        ← reads recent logs via event_logger.query_events(), queries DB
     ↑
registry.py      ← runs checks, aggregates results
     ↑
api/routes/health.py  ← calls registry
services/agent/tools.py  ← calls registry for check_system_health tool
```

---

## 8. Future Work (Not in This Spec)

These are explicitly deferred to later phases:

- **AI repair pipeline:** error trigger → trace → AI diagnosis → proposed fix → auto-fix (tiered: safe ops auto-fix, risky ops human-in-the-loop)
- **Additional health checks:** memory/embedding coverage, storage/disk, agent state, vector store sync
- **Alerting:** notify user (in-app, via agent) when health degrades
- **Circuit breaker:** fail-fast when LLM provider is consistently down
- **Structured logging migration:** replace plain-text Python logging with structured output everywhere
