"""F5 — Async sleep-time agent orchestrator.

Replaces per-turn consolidation + 5-minute inactivity reflection with a
unified, frequency-gated, heat-threshold-aware async orchestrator.

Background tasks are structured and explicit — not autonomous agents.
Each task opens its own DB session via ``db_factory()``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from anima_server.config import settings
from anima_server.services.health.event_logger import emit as health_emit

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────

HEAT_THRESHOLD_CONSOLIDATION: float = 5.0  # Min heat for expensive ops


# ── Heat gating ──────────────────────────────────────────────────────


def _should_run_expensive(
    db: Any,
    user_id: int,
) -> bool:
    """Check if accumulated heat justifies running expensive tasks."""
    from anima_server.services.agent.heat_scoring import get_hottest_items

    hottest = get_hottest_items(db, user_id=user_id, limit=1)
    if not hottest:
        return False
    item = hottest[0]
    heat = getattr(item, "heat", None)
    if heat is None:
        return False
    return heat >= HEAT_THRESHOLD_CONSOLIDATION


# ── Task tracking ────────────────────────────────────────────────────

async def _issue_background_task(
    *,
    user_id: int,
    task_type: str,
    task_fn: Callable[..., Any],
    db_factory: Callable[..., object] | None = None,
    runtime_db_factory: Callable[..., object] | None = None,
    **kwargs: Any,
) -> str:
    """Fire a tracked background task.

    1. Create RuntimeBackgroundTaskRun with status='pending'
    2. Update to 'running' with started_at
    3. Execute task_fn
    4. Update to 'completed' or 'failed' with result/error
    Uses finally-block to ensure state is always saved.
    Task tracking is stored in the Runtime (PG) database.
    """
    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.models.runtime import RuntimeBackgroundTaskRun

    rt_factory = runtime_db_factory or get_runtime_session_factory()

    # Create the task run record
    with rt_factory() as rt_db:
        run = RuntimeBackgroundTaskRun(
            user_id=user_id,
            task_type=task_type,
            status="pending",
        )
        rt_db.add(run)
        rt_db.commit()
        run_id = run.id

    health_emit("background", "task_start", "trace", user_id=user_id, data={
        "task_type": task_type,
        "run_id": run_id,
    })

    # Mark running
    status = "running"
    result_json: dict | None = None
    error_message: str | None = None

    with rt_factory() as rt_db:
        run = rt_db.get(RuntimeBackgroundTaskRun, run_id)
        if run is not None:
            run.status = "running"
            run.started_at = datetime.now(UTC)
            rt_db.commit()

    task_runtime_db_factory = kwargs.pop("_task_runtime_db_factory", None)

    # Execute the task function (no session held open here)
    try:
        task_kwargs = {
            "user_id": user_id,
            "db_factory": db_factory,
            **kwargs,
        }
        if task_runtime_db_factory is not None:
            task_kwargs["runtime_db_factory"] = task_runtime_db_factory

        result = await task_fn(
            **task_kwargs,
        )
        status = "completed"
        health_emit("background", "task_complete", "trace", user_id=user_id, data={
            "task_type": task_type,
            "run_id": run_id,
        })
        if isinstance(result, dict):
            result_json = result
    except Exception as exc:
        status = "failed"
        error_message = str(exc)
        logger.exception(
            "Background task %s (run %s) failed for user %s",
            task_type,
            run_id,
            user_id,
        )
        health_emit("background", "task_failed", "error", user_id=user_id, data={
            "task_type": task_type,
            "run_id": run_id,
            "error": str(exc),
        })

    # Always update final status
    try:
        with rt_factory() as rt_db:
            run = rt_db.get(RuntimeBackgroundTaskRun, run_id)
            if run is not None:
                run.status = status
                run.completed_at = datetime.now(UTC)
                if result_json is not None:
                    run.result_json = result_json
                if error_message is not None:
                    run.error_message = error_message
                rt_db.commit()
    except Exception:
        logger.exception("Failed to update task run %s status", run_id)

    return f"{task_type}:{run_id}"


# ── Orchestrator ─────────────────────────────────────────────────────


async def run_sleeptime_agents(
    *,
    user_id: int,
    user_message: str,
    assistant_response: str,
    thread_id: int | None = None,
    db_factory: Callable[..., object] | None = None,
    runtime_db_factory: Callable[..., object] | None = None,
    force: bool = False,
) -> list[str]:
    """Orchestrate all background tasks.

    Parallel group (always run):
    1. Memory consolidation (predict-calibrate from F3)
    2. Embedding backfill
    3. Knowledge graph ingestion (F4)
    4. Heat decay (F2)
    5. Episode generation check

    Sequential group (heat-gated, skipped if heat < threshold):
    6. Contradiction scan
    7. Profile synthesis

    Time-gated:
    8. Deep monologue (only once per 24h)

    When force=True (inactivity timer): bypass heat gates.
    Returns list of task run IDs for tracking.
    """
    run_ids: list[str] = []

    # ── Sequential group (always run) ─────────────────────────────
    # These run sequentially to avoid SQLite/SQLCipher write
    # contention — the single-writer model doesn't tolerate
    # concurrent commits even with WAL mode and busy_timeout.

    for task_type, task_fn, extra_kwargs in [
        (
            "consolidation",
            _task_consolidation,
            {
                "user_message": user_message,
                "assistant_response": assistant_response,
                "thread_id": thread_id,
                "_task_runtime_db_factory": runtime_db_factory,
            },
        ),
        ("embedding_backfill", _task_embedding_backfill, {}),
        (
            "graph_ingestion",
            _task_graph_ingestion,
            {
                "user_message": user_message,
                "assistant_response": assistant_response,
            },
        ),
        ("heat_decay", _task_heat_decay, {}),
        ("episode_gen", _task_episode_gen, {}),
    ]:
        try:
            r = await _issue_background_task(
                user_id=user_id,
                task_type=task_type,
                task_fn=task_fn,
                db_factory=db_factory,
                runtime_db_factory=runtime_db_factory,
                **extra_kwargs,
            )
            run_ids.append(r)
        except Exception as exc:
            logger.error("Background task %s failed: %s", task_type, exc)

    # ── Sequential group (heat-gated) ────────────────────────────

    run_expensive = force
    if not run_expensive:
        try:
            from anima_server.db.session import SessionLocal

            factory = db_factory or SessionLocal
            with factory() as db:
                run_expensive = _should_run_expensive(db, user_id)
        except Exception:
            logger.debug("Heat check failed, skipping expensive tasks")

    if run_expensive:
        try:
            rid = await _issue_background_task(
                user_id=user_id,
                task_type="contradiction_scan",
                task_fn=_task_contradiction_scan,
                db_factory=db_factory,
                runtime_db_factory=runtime_db_factory,
            )
            run_ids.append(rid)
        except Exception:
            logger.exception("Contradiction scan task failed")

        try:
            rid = await _issue_background_task(
                user_id=user_id,
                task_type="profile_synthesis",
                task_fn=_task_profile_synthesis,
                db_factory=db_factory,
                runtime_db_factory=runtime_db_factory,
            )
            run_ids.append(rid)
        except Exception:
            logger.exception("Profile synthesis task failed")

    # ── Time-gated: deep monologue ───────────────────────────────

    try:
        from anima_server.services.agent.sleep_tasks import _should_run_deep_monologue

        if _should_run_deep_monologue(user_id, db_factory=db_factory):
            rid = await _issue_background_task(
                user_id=user_id,
                task_type="deep_monologue",
                task_fn=_task_deep_monologue,
                db_factory=db_factory,
                runtime_db_factory=runtime_db_factory,
            )
            run_ids.append(rid)
    except Exception:
        logger.exception("Deep monologue task failed")

    # Invalidate companion memory cache so the next turn sees fresh data.
    try:
        from anima_server.services.agent.companion import get_companion

        companion = get_companion(user_id)
        if companion is not None:
            companion.invalidate_memory()
    except Exception:
        logger.debug("Companion cache invalidation failed for user %s", user_id)

    return run_ids


_INNER_REASONING_MARKER = "[Agent's inner reasoning]"
_USER_RESPONSE_MARKER = "[Agent's response to user]"


def _strip_inner_reasoning(text: str) -> str:
    """Remove the [Agent's inner reasoning] section from enriched responses.

    The consolidation pipeline adds this prefix for memory extraction, but
    downstream consumers (e.g. KG ingestion) should only see the actual response.
    """
    if _USER_RESPONSE_MARKER in text:
        idx = text.index(_USER_RESPONSE_MARKER) + len(_USER_RESPONSE_MARKER)
        return text[idx:].lstrip("\n")
    if text.startswith(_INNER_REASONING_MARKER):
        # Fallback: strip everything up to double newline
        parts = text.split("\n\n", 1)
        return parts[1] if len(parts) > 1 else text
    return text


# ── Task implementations (thin wrappers) ─────────────────────────────


async def _task_consolidation(
    *,
    user_id: int,
    user_message: str = "",
    assistant_response: str = "",
    thread_id: int | None = None,
    db_factory: Callable[..., object] | None = None,
    runtime_db_factory: Callable[..., object] | None = None,
) -> dict:
    """Promote pending memory ops to soul store.

    Per-turn memory extraction is now handled by ``run_background_extraction``
    which writes to PG-only ``MemoryCandidate`` rows; the Soul Writer
    orchestrator batches those into the soul store.  This task only needs
    to flush any remaining ``PendingMemoryOp`` rows (core-memory tool
    writes) into the soul blocks.
    """
    from anima_server.services.agent.soul_writer import run_soul_writer

    await run_soul_writer(user_id)

    return {
        "thread_id": thread_id,
        "last_processed_message_id": None,
        "messages_processed": 1 if (user_message or assistant_response) else 0,
    }


async def _task_embedding_backfill(
    *,
    user_id: int,
    db_factory: Callable[..., object] | None = None,
) -> None:
    """Backfill embeddings for existing user memories."""
    from anima_server.services.agent.consolidation import _backfill_user_embeddings

    try:
        await _backfill_user_embeddings(user_id, db_factory=db_factory)
    except Exception:
        logger.debug("Embedding backfill skipped for user %s", user_id)


async def _task_graph_ingestion(
    *,
    user_id: int,
    user_message: str = "",
    assistant_response: str = "",
    db_factory: Callable[..., object] | None = None,
) -> dict:
    """Run knowledge graph ingestion (F4)."""
    if settings.agent_provider == "scaffold":
        return {"entities": 0, "relations": 0, "pruned": 0}

    from anima_server.db.session import SessionLocal
    from anima_server.services.agent.knowledge_graph import ingest_conversation_graph

    # Strip inner reasoning prefix — the KG extraction prompt doesn't
    # understand it and may extract spurious entities from it.
    clean_response = _strip_inner_reasoning(assistant_response)

    factory = db_factory or SessionLocal
    with factory() as db:
        entities, relations, pruned = await ingest_conversation_graph(
            db,
            user_id=user_id,
            user_message=user_message,
            assistant_response=clean_response,
        )
        db.commit()

    return {"entities": entities, "relations": relations, "pruned": pruned}


async def _task_heat_decay(
    *,
    user_id: int,
    db_factory: Callable[..., object] | None = None,
) -> dict:
    """Decay heat scores for all items (F2)."""
    from anima_server.db.session import SessionLocal
    from anima_server.services.agent.heat_scoring import decay_all_heat

    factory = db_factory or SessionLocal
    with factory() as db:
        count = decay_all_heat(db, user_id=user_id)
        db.commit()

    return {"items_decayed": count}


async def _task_episode_gen(
    *,
    user_id: int,
    db_factory: Callable[..., object] | None = None,
) -> dict:
    """Check and generate episode if appropriate."""
    from anima_server.services.agent.episodes import maybe_generate_episode

    episode = await maybe_generate_episode(user_id=user_id, db_factory=db_factory)
    return {"generated": episode is not None}


async def _task_contradiction_scan(
    *,
    user_id: int,
    db_factory: Callable[..., object] | None = None,
) -> dict:
    """Scan for contradictions in memory items."""
    from anima_server.services.agent.sleep_tasks import scan_contradictions

    found, resolved = await scan_contradictions(user_id=user_id, db_factory=db_factory)
    return {"found": found, "resolved": resolved}


async def _task_profile_synthesis(
    *,
    user_id: int,
    db_factory: Callable[..., object] | None = None,
) -> dict:
    """Synthesize user profile from facts."""
    from anima_server.services.agent.sleep_tasks import synthesize_profile

    merged = await synthesize_profile(user_id=user_id, db_factory=db_factory)
    return {"merged": merged}


async def _task_deep_monologue(
    *,
    user_id: int,
    db_factory: Callable[..., object] | None = None,
) -> dict:
    """Run deep inner monologue."""
    from anima_server.services.agent.inner_monologue import run_deep_monologue
    from anima_server.services.agent.sleep_tasks import mark_deep_monologue_done

    monologue = await run_deep_monologue(user_id=user_id, db_factory=db_factory)
    if not monologue.errors:
        mark_deep_monologue_done(user_id)
    return {"errors": monologue.errors if monologue.errors else []}


# ── Restart cursor ───────────────────────────────────────────────────


def get_last_processed_message_id(
    user_id: int,
    thread_id: int | None = None,
    *,
    runtime_db_factory: Callable[..., object] | None = None,
    db_factory: Callable[..., object] | None = None,
) -> int | None:
    """Get the last processed message ID for the active cursor scope.

    Reads from the most recent completed RuntimeBackgroundTaskRun where
    task_type='consolidation' and result_json.thread_id matches.
    """
    from sqlalchemy import desc, select

    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.models.runtime import RuntimeBackgroundTaskRun

    factory = runtime_db_factory or get_runtime_session_factory()
    with factory() as rt_db:
        stmt = (
            select(RuntimeBackgroundTaskRun)
            .where(
                RuntimeBackgroundTaskRun.user_id == user_id,
                RuntimeBackgroundTaskRun.task_type == "consolidation",
                RuntimeBackgroundTaskRun.status == "completed",
            )
            .order_by(desc(RuntimeBackgroundTaskRun.completed_at))
        )
        runs = list(rt_db.scalars(stmt).all())

    for run in runs:
        rj = run.result_json
        if not isinstance(rj, dict):
            continue
        if rj.get("thread_id") == thread_id:
            msg_id = rj.get("last_processed_message_id")
            if msg_id is not None:
                return int(msg_id)
    return None


def update_last_processed_message_id(
    user_id: int,
    thread_id: int | None,
    message_id: int,
    messages_processed: int,
    *,
    runtime_db_factory: Callable[..., object] | None = None,
    db_factory: Callable[..., object] | None = None,
) -> None:
    """Persist the consolidation restart cursor in the most recent run."""
    from sqlalchemy import desc, select

    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.models.runtime import RuntimeBackgroundTaskRun

    factory = runtime_db_factory or get_runtime_session_factory()
    with factory() as rt_db:
        stmt = (
            select(RuntimeBackgroundTaskRun)
            .where(
                RuntimeBackgroundTaskRun.user_id == user_id,
                RuntimeBackgroundTaskRun.task_type == "consolidation",
                RuntimeBackgroundTaskRun.status == "completed",
            )
            .order_by(desc(RuntimeBackgroundTaskRun.completed_at))
        )
        runs = list(rt_db.scalars(stmt).all())
        run = None
        for candidate in runs:
            rj = candidate.result_json
            if isinstance(rj, dict) and rj.get("thread_id") == thread_id:
                run = candidate
                break
        if run is not None:
            run.result_json = {
                "thread_id": thread_id,
                "last_processed_message_id": message_id,
                "messages_processed": messages_processed,
            }
            rt_db.commit()
