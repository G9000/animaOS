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

from sqlalchemy.exc import OperationalError

from anima_server.services.documents.parsing_pack import parsing_pack_ready
from anima_server.services.documents.reparse import list_reparse_candidates, reparse_document
from anima_server.services.health.event_logger import emit as health_emit

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────

SLEEPTIME_FREQUENCY: int = 3
HEAT_THRESHOLD_CONSOLIDATION: float = 5.0  # Min heat for expensive ops
_EPISODE_GEN_LOCK_RETRIES: int = 2
_EPISODE_GEN_LOCK_RETRY_DELAY_SECONDS: float = 1.0


def should_run_sleeptime(conversation_turn_count: int | None) -> bool:
    """Return whether the full sleeptime orchestrator should fire on this turn."""
    if conversation_turn_count is None or conversation_turn_count <= 0:
        return False
    return conversation_turn_count % SLEEPTIME_FREQUENCY == 0


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


def _is_database_locked_error(exc: OperationalError) -> bool:
    return "database is locked" in str(exc).lower()


# ── Input-freshness gating ───────────────────────────────────────────


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _newest_memory_item_at(db: Any, user_id: int) -> datetime | None:
    from sqlalchemy import func as sa_func
    from sqlalchemy import select

    from anima_server.models import MemoryItem

    created = db.scalar(
        select(sa_func.max(MemoryItem.created_at)).where(MemoryItem.user_id == user_id)
    )
    updated = db.scalar(
        select(sa_func.max(MemoryItem.updated_at)).where(MemoryItem.user_id == user_id)
    )
    candidates = [ts for ts in (_as_utc(created), _as_utc(updated)) if ts is not None]
    return max(candidates) if candidates else None


def _newest_episode_at(db: Any, user_id: int) -> datetime | None:
    from sqlalchemy import func as sa_func
    from sqlalchemy import select

    from anima_server.models.agent_runtime import MemoryEpisode

    return _as_utc(
        db.scalar(
            select(sa_func.max(MemoryEpisode.created_at)).where(
                MemoryEpisode.user_id == user_id
            )
        )
    )


def _newest_claim_at(db: Any, user_id: int) -> datetime | None:
    """Newest MemoryClaim timestamp for the user.

    Profile synthesis reconciles ``user_profile_fields`` from claims, and a
    claim can be created/updated without a newer ``MemoryItem`` row (its
    ``memory_item_id`` is nullable), so the profile freshness gate must fold in
    claim changes or claim-only edits would be treated as "unchanged".
    """
    from sqlalchemy import func as sa_func
    from sqlalchemy import select

    from anima_server.models.agent_runtime import MemoryClaim

    created = db.scalar(
        select(sa_func.max(MemoryClaim.created_at)).where(MemoryClaim.user_id == user_id)
    )
    updated = db.scalar(
        select(sa_func.max(MemoryClaim.updated_at)).where(MemoryClaim.user_id == user_id)
    )
    candidates = [ts for ts in (_as_utc(created), _as_utc(updated)) if ts is not None]
    return max(candidates) if candidates else None


def _inputs_changed_since_last_run(
    *,
    user_id: int,
    task_type: str,
    latest_input_at: datetime | None,
    runtime_db_factory: Callable[..., object] | None,
) -> bool:
    """Dirty check: skip a task when nothing it reads has changed.

    An idle user who triggers repeated reflection lulls used to pay the
    full synthesis suite each time for identical inputs.  Unknown state
    (no runtime DB, no timestamps) errs toward running the task.
    """
    if latest_input_at is None:
        return False

    from sqlalchemy import desc, select

    from anima_server.models.runtime import RuntimeBackgroundTaskRun

    try:
        if runtime_db_factory is None:
            from anima_server.db.runtime import get_runtime_session_factory

            runtime_db_factory = get_runtime_session_factory()
        with runtime_db_factory() as rt_db:
            last_completed = rt_db.scalar(
                select(RuntimeBackgroundTaskRun.completed_at)
                .where(
                    RuntimeBackgroundTaskRun.user_id == user_id,
                    RuntimeBackgroundTaskRun.task_type == task_type,
                    RuntimeBackgroundTaskRun.status == "completed",
                )
                .order_by(desc(RuntimeBackgroundTaskRun.completed_at))
                .limit(1)
            )
    except Exception:
        logger.debug("Freshness check failed for %s; running task", task_type)
        return True

    if last_completed is None:
        return True
    return latest_input_at > _as_utc(last_completed)


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

    missing_task_runtime_db_factory = object()
    task_runtime_db_factory = kwargs.pop(
        "_task_runtime_db_factory",
        missing_task_runtime_db_factory,
    )

    # Execute the task function (no session held open here)
    try:
        task_kwargs = {
            "user_id": user_id,
            "db_factory": db_factory,
            **kwargs,
        }
        if task_runtime_db_factory is not missing_task_runtime_db_factory:
            task_kwargs["runtime_db_factory"] = task_runtime_db_factory or rt_factory

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
    manual: bool = False,
) -> list[str]:
    """Orchestrate all background tasks.

    Sequential group (always run):
    1. Memory consolidation (predict-calibrate from F3)
    2. Embedding backfill
    3. Knowledge graph ingestion (F4)
    4. Heat decay (F2)
    5. Foresight lifecycle sweep
    6. Episode generation check

    Sequential group (heat-gated, skipped if heat < threshold):
    7. Contradiction scan
    8. Profile synthesis
    9. Pattern synthesis

    Time-gated:
    10. Deep monologue (only once per 24h)

    When force=True (inactivity timer): bypass heat gates.  ``manual=True`` (a
    user-triggered /sleep) additionally runs the contradiction scan regardless
    of heat — an idle-lull force still heat-gates that most-expensive task, but
    an on-demand maintenance click should honor the user's intent.
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
        ("foresight_lifecycle", _task_foresight_lifecycle, {}),
        ("episode_gen", _task_episode_gen, {}),
        (
            "knowledge_autocompile",
            _task_knowledge_autocompile,
            {"_task_runtime_db_factory": runtime_db_factory},
        ),
        (
            "document_reparse",
            _task_reparse_pending_documents,
            {"_task_runtime_db_factory": runtime_db_factory},
        ),
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

    heat_gate_passed = False
    try:
        from anima_server.db.session import SessionLocal

        factory = db_factory or SessionLocal
        with factory() as db:
            heat_gate_passed = _should_run_expensive(db, user_id)
    except Exception:
        logger.debug("Heat check failed, skipping expensive tasks")

    run_expensive = force or heat_gate_passed

    if run_expensive:
        newest_item_at: datetime | None = None
        newest_episode_at: datetime | None = None
        newest_claim_at: datetime | None = None
        try:
            from anima_server.db.session import SessionLocal

            factory = db_factory or SessionLocal
            with factory() as db:
                newest_item_at = _newest_memory_item_at(db, user_id)
                newest_episode_at = _newest_episode_at(db, user_id)
                newest_claim_at = _newest_claim_at(db, user_id)
        except Exception:
            logger.debug("Input freshness lookup failed; running all tasks")

        # Profile synthesis reconciles from both memory items and claims, so its
        # freshness gate is the newer of the two.
        newest_profile_input_at = max(
            (ts for ts in (newest_item_at, newest_claim_at) if ts is not None),
            default=None,
        )

        def _fresh(task_type: str, latest_input_at: datetime | None) -> bool:
            # `force` bypasses the heat gate (above), not this input-freshness
            # dirty-check: even a manual /sleep or idle catch-up run skips work
            # whose inputs have not changed since the last completed run.
            changed = _inputs_changed_since_last_run(
                user_id=user_id,
                task_type=task_type,
                latest_input_at=latest_input_at,
                runtime_db_factory=runtime_db_factory,
            )
            if not changed:
                logger.info(
                    "skipped_unchanged: %s for user %s (no new inputs since "
                    "the last completed run)",
                    task_type,
                    user_id,
                )
            return changed

        # The contradiction scan is the most expensive recurring task
        # (up to 40 LLM calls): it honors the heat gate even on forced
        # idle-lull runs, but a manual /sleep runs it on demand.  Either way it
        # still skips entirely when no memory changed (input-freshness gate).
        if (heat_gate_passed or manual) and _fresh("contradiction_scan", newest_item_at):
            try:
                rid = await _issue_background_task(
                    user_id=user_id,
                    task_type="contradiction_scan",
                    task_fn=_task_contradiction_scan,
                    db_factory=db_factory,
                    runtime_db_factory=runtime_db_factory,
                    _task_runtime_db_factory=runtime_db_factory,
                )
                run_ids.append(rid)
            except Exception:
                logger.exception("Contradiction scan task failed")

        try:
            rid = await _issue_background_task(
                user_id=user_id,
                task_type="memory_evolution_scan",
                task_fn=_task_memory_evolution_scan,
                db_factory=db_factory,
                runtime_db_factory=runtime_db_factory,
            )
            run_ids.append(rid)
        except Exception:
            logger.exception("Memory evolution scan task failed")

        if _fresh("profile_synthesis", newest_profile_input_at):
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

        if _fresh("pattern_synthesis", newest_episode_at):
            try:
                rid = await _issue_background_task(
                    user_id=user_id,
                    task_type="pattern_synthesis",
                    task_fn=_task_pattern_synthesis,
                    db_factory=db_factory,
                    runtime_db_factory=runtime_db_factory,
                )
                run_ids.append(rid)
            except Exception:
                logger.exception("Pattern synthesis task failed")

        # IL4 crystallization is capped per-run (bounds LLM cost), so it
        # rides the same heat/manual gate as the other expensive synthesis
        # tasks rather than waiting for the weekly decay cadence below.
        try:
            rid = await _issue_background_task(
                user_id=user_id,
                task_type="latent_crystallization",
                task_fn=_task_latent_crystallization,
                db_factory=db_factory,
                runtime_db_factory=runtime_db_factory,
                _task_runtime_db_factory=runtime_db_factory,
            )
            run_ids.append(rid)
        except Exception:
            logger.exception("Latent trace crystallization task failed")

    # ── Time-gated: deep monologue ───────────────────────────────

    try:
        from anima_server.services.agent.sleep_tasks import _should_run_deep_monologue

        if _should_run_deep_monologue(
            user_id,
            db_factory=db_factory,
            runtime_db_factory=runtime_db_factory,
        ):
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

    # ── Time-gated: IL4 latent trace weekly decay ────────────────

    try:
        from anima_server.services.agent.sleep_tasks import _should_run_latent_decay

        if _should_run_latent_decay(
            user_id,
            db_factory=db_factory,
            runtime_db_factory=runtime_db_factory,
        ):
            rid = await _issue_background_task(
                user_id=user_id,
                task_type="latent_decay",
                task_fn=_task_latent_decay,
                db_factory=db_factory,
                runtime_db_factory=runtime_db_factory,
            )
            run_ids.append(rid)
    except Exception:
        logger.exception("Latent trace decay task failed")

    # Invalidate companion memory cache so the next turn sees fresh data.
    try:
        from anima_server.services.agent.companion import get_companion

        companion = get_companion(user_id)
        if companion is not None:
            companion.invalidate_memory()
    except Exception:
        logger.debug(
            "Companion cache invalidation failed for user %s", user_id)

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

    await run_soul_writer(
        user_id,
        soul_db_factory=db_factory,
        runtime_db_factory=runtime_db_factory,
    )

    cursor = _runtime_message_cursor(
        user_id=user_id,
        thread_id=thread_id,
        runtime_db_factory=runtime_db_factory,
    )
    if cursor is not None:
        last_processed_message_id, messages_processed = cursor
        # Persist the cursor to its dedicated table.  The task's result_json
        # is no longer the cursor store, so the advance must be written
        # explicitly (the returned dict below is now purely informational for
        # task-run inspection).
        if last_processed_message_id is not None:
            update_last_processed_message_id(
                user_id,
                thread_id,
                last_processed_message_id,
                messages_processed,
                runtime_db_factory=runtime_db_factory,
            )
        return {
            "thread_id": thread_id,
            "last_processed_message_id": last_processed_message_id,
            "messages_processed": messages_processed,
        }

    return {
        "thread_id": thread_id,
        "last_processed_message_id": None,
        "messages_processed": 1 if (user_message or assistant_response) else 0,
    }


def _runtime_message_cursor(
    *,
    user_id: int,
    thread_id: int | None,
    runtime_db_factory: Callable[..., object] | None,
) -> tuple[int | None, int] | None:
    """Return the latest runtime message id and unprocessed message count."""
    if runtime_db_factory is None:
        return None

    from sqlalchemy import func, select

    from anima_server.models.runtime import RuntimeMessage

    previous_message_id = get_last_processed_message_id(
        user_id,
        thread_id=thread_id,
        runtime_db_factory=runtime_db_factory,
    )

    with runtime_db_factory() as rt_db:
        if _has_unprocessed_memory_backlog(
            rt_db,
            user_id=user_id,
            thread_id=thread_id,
            previous_message_id=previous_message_id,
        ):
            return previous_message_id, 0

        filters = [
            RuntimeMessage.user_id == user_id,
        ]
        if thread_id is not None:
            filters.append(RuntimeMessage.thread_id == thread_id)

        latest_message_id = rt_db.scalar(
            select(func.max(RuntimeMessage.id)).where(*filters)
        )
        if latest_message_id is None:
            return None

        count_filters = list(filters)
        if previous_message_id is not None:
            count_filters.append(RuntimeMessage.id > previous_message_id)
        messages_processed = int(
            rt_db.scalar(select(func.count(RuntimeMessage.id)).where(*count_filters)) or 0
        )

    return int(latest_message_id), messages_processed


def _has_unprocessed_memory_backlog(
    rt_db: Any,
    *,
    user_id: int,
    thread_id: int | None,
    previous_message_id: int | None,
) -> bool:
    from sqlalchemy import select

    from anima_server.models.runtime import RuntimeMessage
    from anima_server.models.runtime_memory import MemoryCandidate, MemoryExtractionFailure
    from anima_server.services.agent.soul_writer import MAX_RETRY_COUNT

    candidates = list(
        rt_db.scalars(
            select(MemoryCandidate).where(
                MemoryCandidate.user_id == user_id,
                MemoryCandidate.status.in_(["extracted", "queued", "failed"]),
            )
        ).all()
    )
    backlog_message_ids: set[int] = set()
    for candidate in candidates:
        if (
            candidate.status == "failed"
            and (candidate.retry_count or 0) >= MAX_RETRY_COUNT
        ):
            continue
        for message_id in candidate.source_message_ids or []:
            try:
                numeric_message_id = int(message_id)
            except (TypeError, ValueError):
                continue
            if previous_message_id is None or numeric_message_id > previous_message_id:
                backlog_message_ids.add(numeric_message_id)

    extraction_failures = list(
        rt_db.scalars(
            select(MemoryExtractionFailure).where(
                MemoryExtractionFailure.user_id == user_id,
                MemoryExtractionFailure.status == "failed",
                MemoryExtractionFailure.retry_count < MAX_RETRY_COUNT,
            )
        ).all()
    )
    for failure in extraction_failures:
        for message_id in failure.source_message_ids or []:
            try:
                numeric_message_id = int(message_id)
            except (TypeError, ValueError):
                continue
            if previous_message_id is None or numeric_message_id > previous_message_id:
                backlog_message_ids.add(numeric_message_id)

    if not backlog_message_ids:
        return False

    filters = [
        RuntimeMessage.user_id == user_id,
        RuntimeMessage.id.in_(backlog_message_ids),
    ]
    if thread_id is not None:
        filters.append(RuntimeMessage.thread_id == thread_id)

    return rt_db.scalar(select(RuntimeMessage.id).where(*filters).limit(1)) is not None


async def _task_embedding_backfill(
    *,
    user_id: int,
    db_factory: Callable[..., object] | None = None,
) -> dict:
    """Backfill embeddings; also the recovery path for the embedding
    contract (re-embed after a model switch), failed vector-store writes,
    and orphaned pgvector rows.

    Returns ``{"backfilled": n, "resynced": m}`` so the manual /sleep summary
    reports the real counts instead of a hard-coded zero."""
    from anima_server.services.agent.consolidation import _backfill_user_embeddings
    from anima_server.services.agent.embedding_contract import (
        ensure_pgvector_dimension,
        has_reset_done,
        is_reembed_required,
        mark_reset_done,
        mark_user_reembed_complete,
        reset_derived_embedding_stores,
        sweep_orphaned_runtime_embeddings,
    )

    reembedding = False
    try:
        from anima_server.db.session import SessionLocal

        factory = db_factory or SessionLocal
        if is_reembed_required(user_id):
            reembedding = True
            # Reset only ONCE per re-embed cycle, tracked by an explicit marker
            # rather than inferred from null embedding counts.  The backfill
            # below only re-embeds ~10 items per pass, so re-running the reset
            # on every sleeptime pass (while reembed_required stays true) would
            # re-null the batch the previous pass just embedded — `remaining`
            # never reaches 0 and semantic search stays disabled forever for
            # users with more than one batch of memories.  A null-count guard
            # also mis-fires the moment the first batch is embedded (count drops
            # to 0), triggering a second destructive reset mid-cycle.
            if not has_reset_done(user_id):
                with factory() as db:
                    cleared = reset_derived_embedding_stores(db, user_id=user_id)
                    db.commit()
                # Align the pgvector column to the active model's dimension
                # (a no-op on sqlite and when the dimension is unchanged); a
                # dimension change can't be satisfied by deleting rows alone.
                from anima_server.config import resolve_embedding_dim

                aligned = ensure_pgvector_dimension(resolve_embedding_dim())
                if aligned:
                    # Only record the reset as done once the column is actually
                    # aligned — otherwise a transient PG failure would strand
                    # the column at the old vector(N) type with every upsert
                    # failing and no later pass retrying the ALTER.
                    mark_reset_done(user_id)
                    logger.info(
                        "Re-embed started for user %s: %d items reset after an "
                        "embedding model/dimension change",
                        user_id,
                        cleared,
                    )
                else:
                    logger.warning(
                        "pgvector column alignment failed for user %s; leaving "
                        "reset un-marked so a later pass retries the ALTER",
                        user_id,
                    )
            else:
                logger.debug(
                    "Re-embed already reset for user %s this cycle; "
                    "continuing backfill without resetting",
                    user_id,
                )
    except Exception:
        logger.exception("Re-embed reset failed for user %s", user_id)
        reembedding = False

    backfilled = 0
    resynced = 0
    try:
        backfilled = await _backfill_user_embeddings(user_id, db_factory=db_factory)
    except Exception:
        logger.debug("Embedding backfill skipped for user %s", user_id)
        return {"backfilled": 0, "resynced": 0}

    try:
        from anima_server.db.session import SessionLocal

        factory = db_factory or SessionLocal

        # Only complete when the reset is confirmed done for this user — which
        # includes a successful pgvector alignment (`mark_reset_done` is skipped
        # when the ALTER fails).  Otherwise a swallowed pgvector upsert failure
        # during the backfill could drive `remaining` to 0 and mark the user
        # complete against a still-misaligned `vector(N)` column, re-enabling
        # semantic search and skipping the ALTER retry.
        if reembedding and has_reset_done(user_id):
            # This user's items are all re-embedded with the active model:
            # mark THIS user complete so semantic search comes back for them.
            # The global flag is intentionally left set — clearing it would
            # re-enable semantic search for other users whose vectors are
            # still stale (re-embed is per-user; soul stores are per-user
            # encrypted so there is no global reset).
            with factory() as db:
                from sqlalchemy import func as sa_func
                from sqlalchemy import select

                from anima_server.models import MemoryItem

                remaining = db.scalar(
                    select(sa_func.count())
                    .select_from(MemoryItem)
                    .where(
                        MemoryItem.user_id == user_id,
                        MemoryItem.superseded_by.is_(None),
                        MemoryItem.embedding_json.is_(None),
                        # IL5 tombstones never get an embedding — excluding
                        # them lets the re-embed completion check reach zero.
                        MemoryItem.distilled_at.is_(None),
                    )
                )
            if not remaining:
                mark_user_reembed_complete(user_id)

        from anima_server.services.agent.vector_store import (
            consume_vector_store_dirty,
        )

        if consume_vector_store_dirty(user_id):
            from anima_server.services.agent.embeddings import sync_to_vector_store

            with factory() as db:
                resynced = sync_to_vector_store(db, user_id=user_id)
            logger.info(
                "Re-synced %d embeddings to the vector store for user %s "
                "after a failed upsert",
                resynced,
                user_id,
            )

        with factory() as db:
            sweep_orphaned_runtime_embeddings(db, user_id=user_id)
    except Exception:
        logger.debug(
            "Embedding maintenance (contract/re-sync/orphan sweep) failed "
            "for user %s",
            user_id,
            exc_info=True,
        )

    # Report the real work done so the manual /sleep summary isn't hard-coded 0.
    return {"backfilled": backfilled, "resynced": resynced}


async def _task_graph_ingestion(
    *,
    user_id: int,
    user_message: str = "",
    assistant_response: str = "",
    db_factory: Callable[..., object] | None = None,
) -> dict:
    """Run knowledge graph ingestion (F4)."""
    from anima_server.db.session import SessionLocal
    from anima_server.services.agent.knowledge_graph import ingest_conversation_graph

    # Strip inner reasoning prefix — the KG extraction prompt doesn't
    # understand it and may extract spurious entities from it.
    clean_response = _strip_inner_reasoning(assistant_response)

    # No turn text (e.g. a manual /sleep maintenance run) — skip: the
    # entity-extraction LLM would otherwise burn a billable call on an empty
    # prompt and could persist hallucinated entities.
    if not (user_message or "").strip() and not clean_response.strip():
        return {"entities": 0, "relations": 0, "pruned": 0}

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
    """Decay heat scores for all items (F2), then distill any casual/
    transient/emotional_pattern items that decayed below the visibility
    floor into tendency claims (IL5 — PRD "Forgetting as Distillation").

    Distillation runs as its own per-item transactional pass AFTER the
    heat-decay commit: a distillation failure on one item must never roll
    back heat updates that already landed for the whole sweep.
    """
    from anima_server.config import settings
    from anima_server.db.helpers import session_scope
    from anima_server.db.session import SessionLocal
    from anima_server.services.agent.distillation import distill_due_items
    from anima_server.services.agent.heat_scoring import decay_all_heat

    factory = db_factory or SessionLocal
    with session_scope(factory) as db:
        count = decay_all_heat(db, user_id=user_id)

    with factory() as db:
        distillation = distill_due_items(
            db,
            user_id=user_id,
            max_per_run=settings.distill_max_per_run,
        )

    return {
        "items_decayed": count,
        "items_distilled": distillation.distilled,
        "distillation_failed": distillation.failed,
    }


async def _task_knowledge_autocompile(
    *,
    user_id: int,
    db_factory: Callable[..., object] | None = None,
    runtime_db_factory: Callable[..., object] | None = None,
) -> dict:
    """Compile orphan knowledge sources (spans but no concepts) within budget."""
    del db_factory  # knowledge lives in the runtime database
    from sqlalchemy import select

    from anima_server.config import settings
    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.models.runtime import RuntimeSourceSpan
    from anima_server.services.agent.embeddings import generate_embedding
    from anima_server.services.ingestion.document_compiler import (
        compile_source_knowledge_auto,
        find_autocompile_candidates,
    )

    policy = settings.knowledge_autocompile
    if policy == "off":
        return {"policy": "off", "compiled": []}

    factory = runtime_db_factory or get_runtime_session_factory()
    compiled: list[dict] = []
    with factory() as runtime_db:
        candidates = find_autocompile_candidates(
            runtime_db,
            user_id=user_id,
            policy=policy,
            budget=settings.knowledge_autocompile_budget_per_cycle,
            cooldown_hours=settings.knowledge_autocompile_cooldown_hours,
        )
        for source in candidates:
            spans = list(
                runtime_db.scalars(
                    select(RuntimeSourceSpan)
                    .where(
                        RuntimeSourceSpan.source_id == source.id,
                        RuntimeSourceSpan.user_id == user_id,
                    )
                    .order_by(RuntimeSourceSpan.id)
                ).all()
            )
            result = await compile_source_knowledge_auto(
                runtime_db,
                source=source,
                spans=spans,
                embedding_fn=generate_embedding,
            )
            runtime_db.commit()
            compiled.append(
                {
                    "source_id": source.id,
                    "status": result.status,
                    "concepts": result.concept_count,
                }
            )
    return {"policy": policy, "compiled": compiled}


# Statuses from reparse_document() that mean "the pack state changed or the
# parser is unhealthy" rather than "this one document had a problem" — the
# cycle aborts on these instead of burning the rest of the budget against a
# sick parser, and picks the remaining candidates back up next cycle.
#
# "upgraded_unembedded" joins this set for the same reason: it means the
# embedding backend is down. reparse_document() already re-cut the document
# to docling-quality chunks via replace_document_chunks() (which resets the
# document to non-indexed and deletes the old chunk vectors) *before*
# embedding, so an embedding failure leaves the document flushed into a
# non-indexed, unembedded state. Committing that would silently orphan a
# previously-searchable indexed preview document — it drops out of both
# search (status != "indexed") and list_reparse_candidates (which requires
# status == "indexed") — permanently, from a background job the user never
# asked to run right now. The cycle rolls that document's session back
# instead (discarding the flush, preserving the original indexed preview)
# and, since an unavailable embedding backend affects every remaining
# candidate identically, aborts the rest of the budget to retry next cycle
# once embeddings recover.
_REPARSE_ABORT_STATUSES = frozenset(
    {"pack_not_ready", "parse_degraded", "parser_unavailable", "upgraded_unembedded"}
)


async def _task_reparse_pending_documents(
    *,
    user_id: int,
    runtime_db_factory: Callable[..., object] | None = None,
    **_: Any,
) -> str:
    """Re-parse preview/legacy-quality documents through Docling once the
    parsing pack becomes ready, closing the loop so early-ingested documents
    upgrade themselves without a manual reparse click.

    reparse_document() already runs sync_document_source(compile_knowledge=
    True) internally on a successful upgrade — this task must NOT add a
    second compile pass on top of it.

    Docling parsing runs synchronously and can take minutes on a large PDF,
    so the whole per-cycle body — session open, candidate listing, reparse
    loop, commit — runs in a single worker thread via asyncio.to_thread
    (matching soul_writer's convention of keeping a session's entire
    lifecycle on the thread that uses it, and never pinning a pooled
    connection to the event loop across a minutes-long parse); the budget
    keeps a single cycle bounded regardless.
    """
    from anima_server.config import settings
    from anima_server.db.runtime import get_runtime_session_factory

    # Cheap, DB-free gates stay on the event loop.
    if settings.document_auto_reparse != "on":
        return "auto-reparse disabled"
    if not parsing_pack_ready():
        return "parsing pack not ready"

    factory = runtime_db_factory or get_runtime_session_factory()
    budget = settings.document_auto_reparse_budget

    def _reparse_cycle() -> str:
        reparsed = 0
        missing = 0
        embeddings_unavailable = False
        candidates: list[int] = []
        with factory() as runtime_db:
            candidates = list_reparse_candidates(runtime_db, user_id=user_id)[:budget]
            for document_id in candidates:
                result = reparse_document(
                    runtime_db,
                    user_id=user_id,
                    document_id=document_id,
                )
                if result.status == "upgraded":
                    reparsed += 1
                    runtime_db.commit()
                    continue
                if result.status in _REPARSE_ABORT_STATUSES:
                    # Pack state changed mid-cycle, the parser is sick, or
                    # (upgraded_unembedded) the embedding backend is down —
                    # nothing left to commit for this attempt, stop and retry
                    # the remaining candidates next cycle. For
                    # upgraded_unembedded specifically, this rollback is load
                    # bearing: it discards the flushed replace_document_chunks
                    # reset so the document's original indexed preview
                    # survives instead of being silently orphaned.
                    runtime_db.rollback()
                    if result.status == "upgraded_unembedded":
                        embeddings_unavailable = True
                    break
                # "not_found": the document vanished (deleted concurrently)
                # between listing and reparse — nothing to commit, but this
                # doesn't reflect pack/parser health, so keep going. It's
                # also not "pending": the document is gone, so it must not
                # inflate the pending count in the summary.
                missing += 1
                runtime_db.rollback()

        pending = len(candidates) - reparsed - missing
        if not candidates:
            return "no documents pending reparse"
        if embeddings_unavailable:
            return (
                f"reparsed {reparsed} documents "
                f"(embeddings unavailable, {pending} pending)"
            )
        if pending:
            return f"reparsed {reparsed} documents ({pending} pending)"
        return f"reparsed {reparsed} documents"

    return await asyncio.to_thread(_reparse_cycle)


async def _task_foresight_lifecycle(
    *,
    user_id: int,
    db_factory: Callable[..., object] | None = None,
) -> dict:
    """Advance due/occurred/stale foresight states during scheduled sleep."""
    from anima_server.db.helpers import session_scope
    from anima_server.db.session import SessionLocal
    from anima_server.services.agent.foresight import sweep_foresight_lifecycle

    factory = db_factory or SessionLocal
    with session_scope(factory) as db:
        transitions = sweep_foresight_lifecycle(db, user_id=user_id)
    return transitions


async def _task_episode_gen(
    *,
    user_id: int,
    db_factory: Callable[..., object] | None = None,
) -> dict:
    """Check and generate episode if appropriate."""
    from anima_server.services.agent.episodes import maybe_generate_episode

    for retry_index in range(_EPISODE_GEN_LOCK_RETRIES + 1):
        try:
            episode = await maybe_generate_episode(user_id=user_id, db_factory=db_factory)
            return {"generated": episode is not None}
        except OperationalError as exc:
            if not _is_database_locked_error(exc):
                raise
            if retry_index >= _EPISODE_GEN_LOCK_RETRIES:
                logger.warning(
                    "Episode generation skipped for user %s after %d lock retries",
                    user_id,
                    _EPISODE_GEN_LOCK_RETRIES,
                )
                return {"generated": False, "skipped": "database_locked"}
            delay = _EPISODE_GEN_LOCK_RETRY_DELAY_SECONDS * (retry_index + 1)
            logger.warning(
                "Episode generation hit locked database for user %s; retrying in %.1fs (retry %d/%d)",
                user_id,
                delay,
                retry_index + 1,
                _EPISODE_GEN_LOCK_RETRIES,
            )
            await asyncio.sleep(delay)


async def _task_contradiction_scan(
    *,
    user_id: int,
    db_factory: Callable[..., object] | None = None,
    runtime_db_factory: Callable[..., object] | None = None,
) -> dict:
    """Scan for contradictions in memory items."""
    from anima_server.services.agent.sleep_tasks import scan_contradictions

    found, resolved = await scan_contradictions(
        user_id=user_id,
        db_factory=db_factory,
        runtime_db_factory=runtime_db_factory,
    )
    return {"found": found, "resolved": resolved}


async def _task_profile_synthesis(
    *,
    user_id: int,
    db_factory: Callable[..., object] | None = None,
) -> dict:
    """Synthesize the user profile from facts and reconcile structured profile
    fields from active claims.

    Reconciliation used to live only in the manual ``/sleep`` orchestrator
    (``run_sleep_tasks``); folding it in here means the automatic sleep path
    reconciles profile fields too, and keeps ``run_sleeptime_agents`` a true
    superset now that ``run_sleep_tasks`` is gone.  The companion cache is
    invalidated once at the end of the orchestrator regardless.
    """
    from anima_server.db.session import SessionLocal
    from anima_server.services.agent.sleep_tasks import synthesize_profile
    from anima_server.services.agent.user_profile import reconcile_profile_from_claims

    merged = await synthesize_profile(user_id=user_id, db_factory=db_factory)

    factory = db_factory or SessionLocal
    with factory() as db:
        reconciled = reconcile_profile_from_claims(db, user_id=user_id)
        if reconciled > 0:
            db.commit()

    return {"merged": merged, "profile_fields_reconciled": reconciled}


async def _task_pattern_synthesis(
    *,
    user_id: int,
    db_factory: Callable[..., object] | None = None,
) -> dict:
    """Synthesize repeated patterns across episodes."""
    from anima_server.services.agent.pattern_synthesis import synthesize_cross_episode_patterns

    result = await synthesize_cross_episode_patterns(
        user_id=user_id,
        db_factory=db_factory,
    )
    return {
        "sampled": result.sampled,
        "proposed": result.proposed,
        "created": result.created,
        "updated": result.updated,
        "skipped": result.skipped,
    }


async def _task_memory_evolution_scan(
    *,
    user_id: int,
    db_factory: Callable[..., object] | None = None,
) -> dict:
    """Surface linked and possible soft memory evolution during sleep time."""
    from anima_server.db.session import SessionLocal
    from anima_server.services.agent.memory_salience import surface_memory_drift

    factory = db_factory or SessionLocal
    with factory() as db:
        return surface_memory_drift(db, user_id=user_id)


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


async def _task_latent_decay(
    *,
    user_id: int,
    db_factory: Callable[..., object] | None = None,
) -> dict:
    """IL4 weekly latent-trace decay + cap (soul-store)."""
    from anima_server.db.helpers import session_scope
    from anima_server.db.session import SessionLocal
    from anima_server.services.agent.latent_traces import decay_and_cap_traces
    from anima_server.services.agent.sleep_tasks import mark_latent_decay_done

    factory = db_factory or SessionLocal
    with session_scope(factory) as soul_db:
        stats = decay_and_cap_traces(soul_db, user_id=user_id)
    mark_latent_decay_done(user_id)
    return stats


async def _task_latent_crystallization(
    *,
    user_id: int,
    db_factory: Callable[..., object] | None = None,
    runtime_db_factory: Callable[..., object] | None = None,
) -> dict:
    """IL4 crystallization: synthesize durable memories from latent traces
    that crossed the crystallization threshold (capped per run to bound
    LLM cost — see ``latent_traces.crystallize_due_traces``)."""
    from anima_server.services.agent.latent_traces import crystallize_due_traces

    return await crystallize_due_traces(
        user_id=user_id,
        db_factory=db_factory,
        runtime_db_factory=runtime_db_factory,
    )


# ── Restart cursor ───────────────────────────────────────────────────


def _cursor_scope_filter(thread_id: int | None):
    """SQL predicate selecting the cursor row for a ``(user, thread)`` scope.

    ``thread_id`` is nullable for the thread-agnostic scope, and SQL treats
    NULL as distinct — so the global scope must be matched with ``IS NULL``
    rather than ``== None``.
    """
    from anima_server.models.runtime import RuntimeConsolidationCursor

    if thread_id is None:
        return RuntimeConsolidationCursor.thread_id.is_(None)
    return RuntimeConsolidationCursor.thread_id == thread_id


def get_last_processed_message_id(
    user_id: int,
    thread_id: int | None = None,
    *,
    runtime_db_factory: Callable[..., object] | None = None,
    db_factory: Callable[..., object] | None = None,
) -> int | None:
    """Get the last processed message ID for the active cursor scope.

    Reads the dedicated ``runtime_consolidation_cursors`` row (indexed on
    ``(user_id, thread_id)``) rather than scanning every completed
    consolidation task-run and Python-filtering ``result_json`` — so the
    cursor survives task-run pruning.
    """
    from sqlalchemy import select

    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.models.runtime import RuntimeConsolidationCursor

    factory = runtime_db_factory or get_runtime_session_factory()
    with factory() as rt_db:
        msg_id = rt_db.scalar(
            select(RuntimeConsolidationCursor.last_processed_message_id).where(
                RuntimeConsolidationCursor.user_id == user_id,
                _cursor_scope_filter(thread_id),
            )
        )
    return int(msg_id) if msg_id is not None else None


def update_last_processed_message_id(
    user_id: int,
    thread_id: int | None,
    message_id: int,
    messages_processed: int,
    *,
    runtime_db_factory: Callable[..., object] | None = None,
    db_factory: Callable[..., object] | None = None,
) -> None:
    """Upsert the consolidation restart cursor for a ``(user, thread)`` scope."""
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.models.runtime import RuntimeConsolidationCursor

    def _load(rt_db):
        return rt_db.scalar(
            select(RuntimeConsolidationCursor).where(
                RuntimeConsolidationCursor.user_id == user_id,
                _cursor_scope_filter(thread_id),
            )
        )

    def _advance(cursor) -> None:
        # Only ever move the cursor FORWARD.  Overlapping tasks (post-turn,
        # reflection, manual sleep) run without a per-cursor lock, so an older
        # task can compute a smaller message_id and commit after a newer task
        # already advanced the cursor; rewinding would make the next run treat
        # already-processed runtime messages as new and re-run extraction.
        if message_id <= (cursor.last_processed_message_id or 0):
            return
        cursor.last_processed_message_id = message_id
        cursor.messages_processed = messages_processed

    factory = runtime_db_factory or get_runtime_session_factory()
    with factory() as rt_db:
        cursor = _load(rt_db)
        if cursor is not None:
            _advance(cursor)
            rt_db.commit()
            return

        rt_db.add(
            RuntimeConsolidationCursor(
                user_id=user_id,
                thread_id=thread_id,
                last_processed_message_id=message_id,
                messages_processed=messages_processed,
            )
        )
        try:
            rt_db.commit()
        except IntegrityError:
            # Post-turn sleeptime, reflection, and manual sleep can overlap
            # without a per-cursor lock, so two tasks can both read "no cursor"
            # and race the insert; the partial-unique index rejects the second.
            # Recover by advancing the row the winner wrote instead of letting
            # the background task fail with the cursor unadvanced.
            rt_db.rollback()
            cursor = _load(rt_db)
            if cursor is not None:
                _advance(cursor)
                rt_db.commit()
