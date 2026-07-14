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
