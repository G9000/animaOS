"""Per-thread turn serialization.

Ensures that concurrent requests for the same thread are serialized so that
sequence allocation, thread state, and turn persistence remain consistent.
"""

from __future__ import annotations

import asyncio
import warnings
from collections import OrderedDict
from threading import Lock

_MAX_THREAD_LOCKS = 512
_MAX_USER_CREATION_LOCKS = 256

_global_lock = Lock()
_thread_locks: OrderedDict[int, asyncio.Lock] = OrderedDict()
_user_creation_locks: OrderedDict[int, asyncio.Lock] = OrderedDict()


def get_thread_lock(thread_id: int) -> asyncio.Lock:
    """Return a per-thread asyncio.Lock, creating one if needed.

    Evicts the least-recently-used entry when the cache exceeds ``_MAX_THREAD_LOCKS``
    to prevent unbounded memory growth.
    """
    with _global_lock:
        lock = _thread_locks.get(thread_id)
        if lock is not None:
            _thread_locks.move_to_end(thread_id)
            return lock

        lock = asyncio.Lock()
        _thread_locks[thread_id] = lock

        # Evict the oldest entry when the cache is full, but only if the
        # lock is not currently held (to avoid breaking an in-progress turn).
        while len(_thread_locks) > _MAX_THREAD_LOCKS:
            oldest_id, oldest_lock = next(iter(_thread_locks.items()))
            if oldest_lock.locked():
                break
            _thread_locks.pop(oldest_id)

        return lock


def get_user_creation_lock(user_id: int) -> asyncio.Lock:
    """Return a per-user asyncio.Lock for serializing thread creation.

    Prevents concurrent first-turn requests from racing on
    ``get_or_create_thread()`` before a thread-level lock can be acquired.
    """
    with _global_lock:
        lock = _user_creation_locks.get(user_id)
        if lock is not None:
            _user_creation_locks.move_to_end(user_id)
            return lock

        lock = asyncio.Lock()
        _user_creation_locks[user_id] = lock

        while len(_user_creation_locks) > _MAX_USER_CREATION_LOCKS:
            oldest_id, oldest_lock = next(iter(_user_creation_locks.items()))
            if oldest_lock.locked():
                break
            _user_creation_locks.pop(oldest_id)

        return lock


def get_user_lock(user_id: int) -> asyncio.Lock:
    """Deprecated. Use get_thread_lock(thread_id) instead."""
    warnings.warn(
        "get_user_lock() is deprecated. Use get_thread_lock(thread_id).",
        DeprecationWarning,
        stacklevel=2,
    )
    return get_thread_lock(user_id)
