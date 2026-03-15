"""Tests for per-user turn coordinator lock management."""

from __future__ import annotations

import asyncio

import pytest

from anima_server.services.agent import turn_coordinator


@pytest.fixture(autouse=True)
def _reset_locks() -> None:
    """Clear the global lock registry before each test."""
    with turn_coordinator._global_lock:
        turn_coordinator._user_locks.clear()


# --------------------------------------------------------------------------- #
# Basic lock behaviour
# --------------------------------------------------------------------------- #


def test_get_user_lock_returns_asyncio_lock() -> None:
    lock = turn_coordinator.get_user_lock(1)
    assert isinstance(lock, asyncio.Lock)


def test_get_user_lock_same_user_returns_same_lock() -> None:
    lock_a = turn_coordinator.get_user_lock(1)
    lock_b = turn_coordinator.get_user_lock(1)
    assert lock_a is lock_b


def test_get_user_lock_different_users_return_different_locks() -> None:
    lock_a = turn_coordinator.get_user_lock(1)
    lock_b = turn_coordinator.get_user_lock(2)
    assert lock_a is not lock_b


# --------------------------------------------------------------------------- #
# LRU eviction
# --------------------------------------------------------------------------- #


def test_eviction_keeps_cache_bounded() -> None:
    """Inserting more than _MAX_USER_LOCKS entries evicts oldest."""
    max_locks = turn_coordinator._MAX_USER_LOCKS

    for user_id in range(max_locks + 10):
        turn_coordinator.get_user_lock(user_id)

    assert len(turn_coordinator._user_locks) <= max_locks


def test_eviction_preserves_recently_used() -> None:
    """Re-accessing a user moves it to the end of the LRU, preventing eviction."""
    max_locks = turn_coordinator._MAX_USER_LOCKS

    # Fill cache
    for user_id in range(max_locks):
        turn_coordinator.get_user_lock(user_id)

    # Re-access user 0 to make it recently used
    turn_coordinator.get_user_lock(0)

    # Add more users to trigger eviction
    for user_id in range(max_locks, max_locks + 5):
        turn_coordinator.get_user_lock(user_id)

    # User 0 should survive because it was recently accessed
    assert 0 in turn_coordinator._user_locks


@pytest.mark.asyncio
async def test_eviction_skips_locked_entries() -> None:
    """Locked entries are not evicted even if they are the oldest."""
    max_locks = turn_coordinator._MAX_USER_LOCKS

    # Create lock for user 0 and acquire it
    lock_0 = turn_coordinator.get_user_lock(0)
    await lock_0.acquire()

    try:
        # Fill rest of cache
        for user_id in range(1, max_locks + 5):
            turn_coordinator.get_user_lock(user_id)

        # User 0's lock is held, so it should not be evicted
        assert 0 in turn_coordinator._user_locks
    finally:
        lock_0.release()


# --------------------------------------------------------------------------- #
# Concurrent serialization
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_lock_serializes_concurrent_access() -> None:
    """Two coroutines for the same user should be serialized."""
    lock = turn_coordinator.get_user_lock(99)
    results: list[str] = []

    async def worker(name: str, delay: float) -> None:
        async with lock:
            results.append(f"{name}_start")
            await asyncio.sleep(delay)
            results.append(f"{name}_end")

    # Start both concurrently; first should finish before second starts
    await asyncio.gather(worker("a", 0.05), worker("b", 0.01))

    assert results == ["a_start", "a_end", "b_start", "b_end"]
