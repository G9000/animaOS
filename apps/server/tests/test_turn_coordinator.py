"""Tests for per-thread turn coordinator lock management."""

from __future__ import annotations

import asyncio

import pytest
from anima_server.services.agent import turn_coordinator


@pytest.fixture(autouse=True)
def _reset_locks() -> None:
    """Clear the global lock registry before each test."""
    with turn_coordinator._global_lock:
        turn_coordinator._thread_locks.clear()


def test_get_thread_lock_creates_new() -> None:
    lock = turn_coordinator.get_thread_lock(1)

    assert isinstance(lock, asyncio.Lock)
    assert turn_coordinator._thread_locks[1] is lock


def test_get_thread_lock_returns_same_lock_for_same_thread() -> None:
    lock_a = turn_coordinator.get_thread_lock(1)
    lock_b = turn_coordinator.get_thread_lock(1)

    assert lock_a is lock_b


def test_different_threads_get_different_locks() -> None:
    lock_a = turn_coordinator.get_thread_lock(1)
    lock_b = turn_coordinator.get_thread_lock(2)

    assert lock_a is not lock_b


def test_lru_eviction_respects_max() -> None:
    max_locks = turn_coordinator._MAX_THREAD_LOCKS

    for thread_id in range(max_locks + 10):
        turn_coordinator.get_thread_lock(thread_id)

    assert len(turn_coordinator._thread_locks) <= max_locks
    assert 0 not in turn_coordinator._thread_locks


def test_move_to_end_on_access() -> None:
    max_locks = turn_coordinator._MAX_THREAD_LOCKS

    for thread_id in range(max_locks):
        turn_coordinator.get_thread_lock(thread_id)

    turn_coordinator.get_thread_lock(0)
    turn_coordinator.get_thread_lock(max_locks)

    assert 0 in turn_coordinator._thread_locks
    assert 1 not in turn_coordinator._thread_locks


@pytest.mark.asyncio
async def test_lru_eviction_skips_locked() -> None:
    max_locks = turn_coordinator._MAX_THREAD_LOCKS
    lock_0 = turn_coordinator.get_thread_lock(0)
    await lock_0.acquire()

    try:
        for thread_id in range(1, max_locks + 5):
            turn_coordinator.get_thread_lock(thread_id)

        assert 0 in turn_coordinator._thread_locks
    finally:
        lock_0.release()


def test_deprecated_get_user_lock_warns() -> None:
    with pytest.warns(DeprecationWarning):
        lock = turn_coordinator.get_user_lock(42)

    assert lock is turn_coordinator.get_thread_lock(42)

