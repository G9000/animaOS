# Dual-Session Scope Helpers (Audit A-6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix audit finding A-6 (2026-06-11 agent-server audit): replace copy-pasted dual-DB commit ordering with shared `session_scope()` / `dual_session_scope()` helpers in `anima_server.db.helpers`, and migrate 9 hand-rolled call sites.

**Architecture:** AnimaOS runs two databases per the three-tier PRDs (P2/P3): a per-user SQLCipher "Soul" store (enduring identity/memory) and an embedded-Postgres "Runtime" store (working cognition, staging). Service code that touches both currently hand-rolls session lifecycle and commit ordering at each site — the audit found 8+ such sites and flagged inconsistent ordering as a latent consistency bug. This plan adds two context managers next to the existing session factories (`apps/server/src/anima_server/db/`) and migrates the mechanical sites. The helper encodes one ordering rule: **soul commits first, runtime second.** Rationale: the runtime store stages `PendingMemoryOp`s that a serialized Soul Writer promotes to the soul store with content-hash idempotency. If runtime committed first and the soul commit then failed, runtime would record work as done that never landed in the soul — silent memory loss. Soul-first means a runtime-commit failure only causes a re-attempt of already-idempotent work (at-least-once delivery, duplicates suppressed by content hash).

**Tech Stack:** Python 3, SQLAlchemy ORM (`Session`, `sessionmaker`), pytest. Tests run via `bun run test` from repo root (wraps `uv run --project apps/server pytest`).

## Global Constraints

- Never commit to main — all work on branch `fix/a6-dual-session-scope`, integrated via PR (user's standing rule).
- Backend test files live under `apps/server/tests/` (AGENTS.md).
- Behavior-preserving migration: call sites that currently swallow exceptions must keep swallowing them (their own try/except stays; only session lifecycle boilerplate moves into the helper).
- No changes to `services/memory/*` package structure (avoid colliding with the backlog MPB epic; the helper lives in `anima_server/db/`, outside MPB scope).
- Do NOT migrate `inner_monologue.py`, `consolidation.py`, `service.py`, `sleep_agent.py`'s dual-phase sites in this PR — they interleave versioned-block conflict handling or deliberately release sessions around LLM calls. They are listed as follow-up in the ticket (Task 6).
- LLM-related behavior is not touched; no provider calls in any new test.

---

### Task 1: `session_scope` / `dual_session_scope` helpers (TDD)

**Files:**
- Create: `apps/server/src/anima_server/db/helpers.py`
- Test: `apps/server/tests/test_db_helpers.py`

**Interfaces:**
- Produces: `session_scope(factory: Callable[[], Session]) -> ContextManager[Session]` — yields a session; commits on clean exit; rolls back and re-raises on exception; always closes.
- Produces: `dual_session_scope(soul_factory: Callable[[], Session], runtime_factory: Callable[[], Session]) -> ContextManager[tuple[Session, Session]]` — yields `(soul, runtime)`; on clean exit commits **soul first, then runtime**; any failure rolls back both (rollback after successful commit is a harmless no-op); always closes both; re-raises.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for anima_server.db.helpers — shared session lifecycle (audit A-6)."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import Column, Integer, String, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from anima_server.db.helpers import dual_session_scope, session_scope


class _Base(DeclarativeBase):
    pass


class _Row(_Base):
    __tablename__ = "helper_test_rows"
    id = Column(Integer, primary_key=True)
    value = Column(String, nullable=False)


def _make_factory() -> tuple[Engine, sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _Base.metadata.create_all(bind=engine)
    factory = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    return engine, factory


@pytest.fixture()
def soul() -> Generator[tuple[Engine, sessionmaker[Session]], None, None]:
    engine, factory = _make_factory()
    yield engine, factory
    engine.dispose()


@pytest.fixture()
def runtime() -> Generator[tuple[Engine, sessionmaker[Session]], None, None]:
    engine, factory = _make_factory()
    yield engine, factory
    engine.dispose()


def _count(factory: sessionmaker[Session]) -> int:
    with factory() as s:
        return len(s.scalars(select(_Row)).all())


class TestSessionScope:
    def test_commits_on_clean_exit(self, soul) -> None:
        _, factory = soul
        with session_scope(factory) as db:
            db.add(_Row(value="kept"))
        assert _count(factory) == 1

    def test_rolls_back_and_reraises_on_exception(self, soul) -> None:
        _, factory = soul
        with pytest.raises(RuntimeError, match="boom"):
            with session_scope(factory) as db:
                db.add(_Row(value="discarded"))
                raise RuntimeError("boom")
        assert _count(factory) == 0

    def test_session_closed_after_exit(self, soul) -> None:
        _, factory = soul
        with session_scope(factory) as db:
            held = db
        # A closed session re-opens a new connection transparently; assert
        # the transaction from the scope is gone instead.
        assert not held.in_transaction()


class TestDualSessionScope:
    def test_commits_both_on_clean_exit(self, soul, runtime) -> None:
        _, soul_factory = soul
        _, runtime_factory = runtime
        with dual_session_scope(soul_factory, runtime_factory) as (s, r):
            s.add(_Row(value="soul"))
            r.add(_Row(value="runtime"))
        assert _count(soul_factory) == 1
        assert _count(runtime_factory) == 1

    def test_body_exception_rolls_back_both(self, soul, runtime) -> None:
        _, soul_factory = soul
        _, runtime_factory = runtime
        with pytest.raises(RuntimeError, match="boom"):
            with dual_session_scope(soul_factory, runtime_factory) as (s, r):
                s.add(_Row(value="soul"))
                r.add(_Row(value="runtime"))
                raise RuntimeError("boom")
        assert _count(soul_factory) == 0
        assert _count(runtime_factory) == 0

    def test_soul_commit_failure_leaves_runtime_uncommitted(
        self, soul, runtime, monkeypatch
    ) -> None:
        """Ordering rule: soul commits first. If it fails, runtime must not commit."""
        _, soul_factory = soul
        _, runtime_factory = runtime
        with pytest.raises(RuntimeError, match="soul-commit-fail"):
            with dual_session_scope(soul_factory, runtime_factory) as (s, r):
                s.add(_Row(value="soul"))
                r.add(_Row(value="runtime"))
                monkeypatch.setattr(
                    s, "commit", lambda: (_ for _ in ()).throw(
                        RuntimeError("soul-commit-fail")
                    )
                )
        assert _count(soul_factory) == 0
        assert _count(runtime_factory) == 0

    def test_runtime_commit_failure_preserves_soul_commit(
        self, soul, runtime, monkeypatch
    ) -> None:
        """At-least-once semantics: soul data is durable; runtime re-stages on retry."""
        _, soul_factory = soul
        _, runtime_factory = runtime
        with pytest.raises(RuntimeError, match="runtime-commit-fail"):
            with dual_session_scope(soul_factory, runtime_factory) as (s, r):
                s.add(_Row(value="soul"))
                r.add(_Row(value="runtime"))
                monkeypatch.setattr(
                    r, "commit", lambda: (_ for _ in ()).throw(
                        RuntimeError("runtime-commit-fail")
                    )
                )
        assert _count(soul_factory) == 1  # soul committed before runtime failed
        assert _count(runtime_factory) == 0

    def test_both_sessions_closed_after_exit(self, soul, runtime) -> None:
        _, soul_factory = soul
        _, runtime_factory = runtime
        with dual_session_scope(soul_factory, runtime_factory) as (s, r):
            held = (s, r)
        assert not held[0].in_transaction()
        assert not held[1].in_transaction()
```

- [ ] **Step 2: Run tests to verify they fail**

Run (repo root): `bun run test -- tests/test_db_helpers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'anima_server.db.helpers'` (collection error counts).

- [ ] **Step 3: Write the implementation**

```python
"""Shared session-lifecycle helpers (audit finding A-6).

Two stores, one ordering rule. Service code that writes to both the Soul
store (SQLCipher, enduring identity) and the Runtime store (Postgres,
staging/working cognition) must commit **soul first, runtime second**:

- Soul-first + a runtime-commit failure means already-idempotent promotion
  work is simply re-attempted on the next cycle (at-least-once; content-hash
  dedup in the Soul Writer suppresses duplicates).
- Runtime-first + a soul-commit failure would record staged work as promoted
  when it never reached the soul — silent memory loss.

Use ``session_scope`` for single-store units of work and
``dual_session_scope`` for promotion paths that write both stores.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager

from sqlalchemy.orm import Session


@contextmanager
def session_scope(factory: Callable[[], Session]) -> Generator[Session, None, None]:
    """Yield a session; commit on clean exit, roll back and re-raise on error."""
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def dual_session_scope(
    soul_factory: Callable[[], Session],
    runtime_factory: Callable[[], Session],
) -> Generator[tuple[Session, Session], None, None]:
    """Yield ``(soul, runtime)``; commit soul first, then runtime.

    Any failure rolls back whatever has not committed and re-raises.
    Rolling back an already-committed session is a no-op, so the error
    path is uniform. Callers on promotion paths must be idempotent
    (they are: Soul Writer dedups by content hash), because a runtime
    commit failure after a successful soul commit re-runs the work.
    """
    soul = soul_factory()
    try:
        runtime = runtime_factory()
    except BaseException:
        soul.close()
        raise
    try:
        yield soul, runtime
        soul.commit()
        runtime.commit()
    except BaseException:
        soul.rollback()
        runtime.rollback()
        raise
    finally:
        soul.close()
        runtime.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bun run test -- tests/test_db_helpers.py -v`
Expected: PASS — 9 tests.

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/anima_server/db/helpers.py apps/server/tests/test_db_helpers.py
git commit -m "db: add session_scope/dual_session_scope helpers (audit A-6)"
```

---

### Task 2: Migrate Soul Writer Phase 4 (the canonical dual-write site)

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/soul_writer.py:496-516`
- Test: `apps/server/tests/test_soul_writer.py` (append one test)

**Interfaces:**
- Consumes: `dual_session_scope(soul_factory, runtime_factory)` from Task 1.
- Produces: nothing new — behavior-preserving refactor of the emotional-pattern promotion block.

Current code (soul_writer.py, inside the Phase 4 `try:` block — note it currently
commits soul then runtime only when `promoted > 0`, and the enclosing
`except Exception: logger.debug(...)` swallows all errors; both behaviors must
be preserved):

```python
        with rt_factory() as runtime_db, soul_factory() as soul_db:
            if should_promote_emotional_patterns(
                soul_db=soul_db,
                pg_db=runtime_db,
                user_id=user_id,
            ):
                promoted = promote_emotional_patterns(
                    soul_db=soul_db,
                    pg_db=runtime_db,
                    user_id=user_id,
                )
                if promoted > 0:
                    soul_db.commit()
                    runtime_db.commit()
                    logger.info(
                        "Soul Writer promoted %d emotional patterns for user %s",
                        promoted,
                        user_id,
                    )
```

- [ ] **Step 1: Write the failing test (append to test_soul_writer.py)**

The file already has `_make_soul_factory` / runtime-factory helpers and seeds
emotional signals for other Phase-4 tests — reuse the existing fixtures in the
file (read its `TestEmotionalPatternPromotion`-adjacent tests first and copy
their setup). The new test asserts the failure-ordering contract at this site:

```python
def test_phase4_runtime_commit_failure_does_not_lose_soul_promotion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A-6: if the runtime commit fails after the soul commit, the promoted
    patterns stay in the soul store and no exception escapes run_soul_writer
    (Phase 4 errors are logged and swallowed by design)."""
    # Arrange exactly as the existing Phase-4 promotion test in this file does
    # (seed enough emotional signals that should_promote_... returns True),
    # then monkeypatch the runtime session's commit to raise on the Phase 4
    # scope only, run run_soul_writer, and assert:
    #   1. run_soul_writer completes without raising
    #   2. the soul store contains the promoted emotional pattern rows
```

Write the test fully by mirroring the existing Phase-4 test's arrange block in
`test_soul_writer.py` (search for `promote_emotional_patterns` in the file).

- [ ] **Step 2: Run it — expected to FAIL only if the migration changes swallow behavior**

Run: `bun run test -- tests/test_soul_writer.py -v -k phase4`
Note: against the *current* code this test may already pass (ordering is
already soul-first here); its job is to pin the contract before refactoring.
If it passes pre-migration, that is acceptable — record it, proceed.

- [ ] **Step 3: Migrate the block**

```python
        with dual_session_scope(soul_factory, rt_factory) as (soul_db, runtime_db):
            if should_promote_emotional_patterns(
                soul_db=soul_db,
                pg_db=runtime_db,
                user_id=user_id,
            ):
                promoted = promote_emotional_patterns(
                    soul_db=soul_db,
                    pg_db=runtime_db,
                    user_id=user_id,
                )
                if promoted > 0:
                    logger.info(
                        "Soul Writer promoted %d emotional patterns for user %s",
                        promoted,
                        user_id,
                    )
```

Add the import at the top of soul_writer.py alongside the existing db imports:

```python
from anima_server.db.helpers import dual_session_scope
```

Note the semantic delta: the helper commits on every clean exit, not only when
`promoted > 0`. Committing a session with no pending changes is a no-op flush;
this is acceptable and simplifies the contract. The enclosing
`try/except Exception: logger.debug` already swallows helper re-raises,
preserving Phase 4's fire-and-forget behavior.

- [ ] **Step 4: Run the soul_writer suite**

Run: `bun run test -- tests/test_soul_writer.py -v`
Expected: PASS — all existing tests plus the new one.

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/anima_server/services/agent/soul_writer.py apps/server/tests/test_soul_writer.py
git commit -m "soul_writer: route Phase 4 dual-write through dual_session_scope (A-6)"
```

---

### Task 3: Migrate eager_consolidation's six hand-rolled sites to `session_scope`

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/eager_consolidation.py` (six blocks; line refs below are pre-edit)
- Test: existing `apps/server/tests/` coverage for eager consolidation (find via `grep -rl eager_consolidation apps/server/tests/`)

**Interfaces:**
- Consumes: `session_scope(factory)` from Task 1.
- Produces: nothing new — behavior-preserving.

The six sites and their exact transformation. Pattern rule: the helper replaces
`db = factory()` / `db.commit()` / `except: db.rollback()` / `finally: db.close()`
**only**. Sites that swallow exceptions and return a sentinel keep their own
`try/except` *around* the `with` block.

Add the import once at the top of the file:

```python
from anima_server.db.helpers import session_scope
```

- [ ] **Step 1: Site 1 — `on_thread_close` archival block (~lines 96-134).** Current shape:

```python
    db = ...factory()
    try:
        ...
        thread = db.get(RuntimeThread, thread_id)
        if thread is not None:
            thread.is_archived = True
            db.commit()
    except Exception:
        logger.exception("Thread close archival failed for thread %d", thread_id)
        db.rollback()
    finally:
        db.close()
```

Becomes:

```python
    try:
        with session_scope(resolved_runtime_db_factory) as db:
            ...
            thread = db.get(RuntimeThread, thread_id)
            if thread is not None:
                thread.is_archived = True
    except Exception:
        logger.exception("Thread close archival failed for thread %d", thread_id)
```

(Use the factory variable name actually present at that site — read the full
function before editing; the body between the ellipses moves verbatim.)

- [ ] **Step 2: Site 2 — `_link_episode_to_transcript` (~lines 150-163).** Current:

```python
    with soul_db_factory() as db:
        if not isinstance(db, Session):
            raise TypeError("Expected SQLAlchemy Session from soul_db_factory")
        episode = db.get(MemoryEpisode, episode_id)
        if episode is None:
            return
        episode.transcript_ref = transcript_ref
        db.commit()
```

Becomes:

```python
    with session_scope(soul_db_factory) as db:
        if not isinstance(db, Session):
            raise TypeError("Expected SQLAlchemy Session from soul_db_factory")
        episode = db.get(MemoryEpisode, episode_id)
        if episode is None:
            return
        episode.transcript_ref = transcript_ref
```

Caveat: `session_scope` commits on the early `return` too (clean generator
exit) — same net effect as today (nothing dirty to flush).
Note `soul_db_factory` is typed `Callable[..., object]` here; `session_scope`
expects it to produce a `Session` — the isinstance guard inside preserves the
existing TypeError behavior before any commit can matter.

- [ ] **Step 3: Site 3 — `inactivity_sweep` stale-thread scan (~lines 200-225).** The
`except` swallows and returns `0`; keep that outside:

```python
    try:
        with session_scope(resolved_runtime_db_factory) as db:
            stale_threads = [...]   # body moves verbatim
            ...
            for thread_id, _user_id in stale_threads:
                thread = db.get(RuntimeThread, thread_id)
                if thread is None:
                    continue
                thread.status = "closed"
                thread.closed_at = closed_at
    except Exception:
        logger.exception("Inactivity sweep failed")
        return 0
```

- [ ] **Step 4: Site 4 — `_record_archive_retry` (~lines 258-300).** This function has a
**mid-function early-return commit** (`db.commit(); return` when resetting
retry state) plus a final commit. Restructure so both paths exit the `with`
cleanly:

```python
    try:
        with session_scope(runtime_db_factory) as db:
            thread = db.get(RuntimeThread, thread_id)
            if thread is None:
                return
            if thread.is_archived or thread.status != "closed":
                if thread.archive_retry_count or thread.archive_next_retry_at:
                    thread.archive_retry_count = 0
                    thread.archive_next_retry_at = None
                return
            thread.archive_retry_count = (thread.archive_retry_count or 0) + 1
            if thread.archive_retry_count >= _ARCHIVE_MAX_RETRIES:
                thread.archive_failed = True
                thread.archive_next_retry_at = None
                degraded_logger.warning(
                    "Thread %d archival permanently failed after %d attempts; "
                    "giving up (clear archive_failed to retry manually)",
                    thread_id,
                    thread.archive_retry_count,
                )
            else:
                delay_minutes = min(
                    _ARCHIVE_BACKOFF_BASE_MINUTES
                    * 2 ** (thread.archive_retry_count - 1),
                    _ARCHIVE_BACKOFF_CAP_MINUTES,
                )
                thread.archive_next_retry_at = datetime.now(UTC) + timedelta(
                    minutes=delay_minutes
                )
    except Exception:
        logger.exception(
            "Failed to update archival retry state for thread %d", thread_id
        )
```

Semantic delta: the no-op path (thread exists, not archived, no retry state)
now issues an empty commit instead of none — harmless.

- [ ] **Step 5: Sites 5 & 6 — `prune_expired_messages` (~lines 315-333) and the
background-task-run pruning block (~lines 355-376).** Both share the shape
"delete → commit → log → return count / swallow → return 0":

```python
    try:
        with session_scope(resolved_runtime_db_factory) as db:
            result = db.execute(
                delete(RuntimeMessage).where(
                    RuntimeMessage.created_at < cutoff,
                    RuntimeMessage.thread_id.in_(archived_thread_ids),
                )
            )
            deleted = int(result.rowcount or 0)
        if deleted:
            logger.info("Pruned %d expired archived runtime messages", deleted)
        return deleted
    except Exception:
        logger.exception("Message pruning failed")
        return 0
```

Apply the same shape to the task-run pruning block (`RuntimeBackgroundTaskRun`,
its own logger message). Note the pre-`with` guard clauses (e.g.
`return 0` when there are no archived threads) stay where they are.

- [ ] **Step 6: Run the eager-consolidation tests**

Run: `grep -rl eager_consolidation apps/server/tests/` then
`bun run test -- <those files> -v`
Expected: PASS, zero behavior change.

- [ ] **Step 7: Commit**

```bash
git add apps/server/src/anima_server/services/agent/eager_consolidation.py
git commit -m "eager_consolidation: adopt session_scope at six sites (A-6)"
```

---

### Task 4: Migrate the two simple sleep-task sites

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/sleep_agent.py` — `_task_heat_decay` (~line 899-912) and `_task_foresight_lifecycle` (~line 977-991) only. Do NOT touch the other sleep tasks in this PR.
- Test: existing sleep-task tests (`grep -rl "_task_heat_decay\|foresight_lifecycle" apps/server/tests/`)

**Interfaces:**
- Consumes: `session_scope(factory)` from Task 1.

- [ ] **Step 1: Migrate `_task_heat_decay`.** Current:

```python
    factory = db_factory or SessionLocal
    with factory() as db:
        count = decay_all_heat(db, user_id=user_id)
        db.commit()

    return {"items_decayed": count}
```

Becomes:

```python
    factory = db_factory or SessionLocal
    with session_scope(factory) as db:
        count = decay_all_heat(db, user_id=user_id)

    return {"items_decayed": count}
```

Import once near the function's existing local imports:
`from anima_server.db.helpers import session_scope`

- [ ] **Step 2: Migrate `_task_foresight_lifecycle`.** Current:

```python
    factory = db_factory or SessionLocal
    with factory() as db:
        transitions = sweep_foresight_lifecycle(db, user_id=user_id)
        if any(transitions.values()):
            db.commit()
    return transitions
```

Becomes:

```python
    factory = db_factory or SessionLocal
    with session_scope(factory) as db:
        transitions = sweep_foresight_lifecycle(db, user_id=user_id)
    return transitions
```

(Semantic delta: unconditional empty commit when no transitions — harmless no-op.)

- [ ] **Step 3: Run sleep tests**

Run: `bun run test -- $(grep -rl "heat_decay\|foresight" apps/server/tests/ | tr '\n' ' ') -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add apps/server/src/anima_server/services/agent/sleep_agent.py
git commit -m "sleep_agent: adopt session_scope in heat-decay and foresight tasks (A-6)"
```

---

### Task 5: Ticket + audit-doc status update

**Files:**
- Create: `tickets/agent-server-audit-remediation/ASR-001-dual-session-scope.md`
- Modify: `docs/audit/2026-06-11-agent-server-audit.md` (Deferred section, ~line 281)

- [ ] **Step 1: Write the ticket** (frontmatter style mirrors `tickets/memory-package-boundary/MPB-000-parent.md`):

```markdown
# ASR-001 - Shared session_scope/dual_session_scope helpers (audit A-6)

- Status: in_review
- Priority: P2
- Scope: `apps/server/src/anima_server/db`, `services/agent/soul_writer.py`, `services/agent/eager_consolidation.py`, `services/agent/sleep_agent.py`
- Parent: none
- Depends on: docs/audit/2026-06-11-agent-server-audit.md (finding A-6)
- Owner: unassigned
- PRD: docs/prds/three-tier-architecture/P2-runtime-messages.md (§ dual-session pattern)
- Plan: docs/superpowers/plans/2026-07-14-dual-session-scope-a6.md
- Created: 2026-07-14
- Updated: 2026-07-14

## Goal

Fix audit finding A-6: copy-pasted dual-DB commit ordering is a latent
consistency bug. Add `anima_server.db.helpers` with `session_scope()` and
`dual_session_scope()` (soul commits first, runtime second — at-least-once
promotion relying on Soul Writer content-hash idempotency) and migrate the
mechanical call sites (9 in this pass).

## Out of scope / follow-up

- `inner_monologue.py` quick/deep reflection blocks (interleaved
  SoulBlockConflict handling; needs its own careful pass)
- `consolidation.py` Phase A/B/C (sessions deliberately released around the
  LLM call; restructuring risks re-pinning pool connections)
- `service.py` runtime commits (high-churn file; single-store only)
- Remaining `sleep_agent.py` tasks with mixed factories

## Validation

- `bun run test -- tests/test_db_helpers.py tests/test_soul_writer.py -v`
- Full backend suite: `bun run test`
```

- [ ] **Step 2: Update the audit doc's Deferred list.** In
`docs/audit/2026-06-11-agent-server-audit.md` (~line 281), annotate A-6:
change the deferred mention to note `A-6: helpers landed + 9 sites migrated
(ASR-001, 2026-07-14); inner_monologue/consolidation/service.py sites remain`.
Keep A-4/A-5 as-is.

- [ ] **Step 3: Commit**

```bash
git add tickets/agent-server-audit-remediation/ docs/audit/2026-06-11-agent-server-audit.md
git commit -m "tickets: ASR-001 dual-session-scope (A-6) status + audit doc note"
```

---

### Task 6: Full suite, verification, PR

- [ ] **Step 1: Run the full backend suite**

Run (repo root): `bun run test`
Expected: PASS. If pre-existing failures unrelated to this change appear,
record them verbatim in the PR description; do not fix them here.

- [ ] **Step 2: Verify end-to-end behavior (superpowers:verification-before-completion)**

Confirm: new helper tests pass; soul_writer suite passes; grep confirms no
remaining hand-rolled commit/rollback/close boilerplate in the migrated
blocks: `grep -n "db.rollback()" apps/server/src/anima_server/services/agent/eager_consolidation.py`
Expected: no matches in the six migrated blocks (other files untouched).

- [ ] **Step 3: Push branch and open PR**

```bash
git push -u origin fix/a6-dual-session-scope
gh pr create --title "Fix audit A-6: shared session_scope/dual_session_scope helpers" --body "$(cat <<'EOF'
## Summary
- Adds `anima_server.db.helpers` with `session_scope()` / `dual_session_scope()` per audit finding A-6 (docs/audit/2026-06-11-agent-server-audit.md)
- Encodes the dual-store commit ordering rule: soul first, runtime second (at-least-once promotion; Soul Writer content-hash idempotency absorbs retries)
- Migrates 9 hand-rolled sites: soul_writer Phase 4 (dual), eager_consolidation ×6, sleep_agent ×2
- Ticket: tickets/agent-server-audit-remediation/ASR-001-dual-session-scope.md

## Out of scope (follow-up in ASR-001)
inner_monologue.py, consolidation.py, service.py sites — interleaved conflict handling / deliberate session release around LLM calls.

## Test plan
- [ ] `bun run test -- tests/test_db_helpers.py -v` (9 new tests incl. failure-ordering)
- [ ] `bun run test -- tests/test_soul_writer.py -v`
- [ ] `bun run test` full suite
EOF
)"
```

---

## Self-Review Notes

- **Spec coverage:** A-6 asks for `db_helpers.py` + `session_scope`/`dual_session_scope` + 8+ sites. Delivered: helpers module (named `helpers.py` under the existing `db/` package rather than a new `db_helpers.py` at services level — closer to the session factories it wraps), 9 sites, failure-ordering tests the audit's concern implies.
- **Ordering-rule justification** is documented in the module docstring and the plan header — reviewers can challenge it in the PR rather than discovering it implicitly.
- **Type consistency:** `session_scope(factory)` and `dual_session_scope(soul_factory, runtime_factory)` used consistently across Tasks 2-4; both yield what Task 1's tests pin.
- **Known accepted deltas** (each flagged inline): empty commits on previously-conditional paths (no-op), commit-on-early-return in `_link_episode_to_transcript` (no dirty state).
