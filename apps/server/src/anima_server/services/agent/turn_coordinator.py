"""Per-user turn serialization.

Ensures that concurrent requests for the same user are serialized so that
sequence allocation, thread state, and turn persistence remain consistent.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from threading import Lock


_MAX_USER_LOCKS = 256

_global_lock = Lock()
_user_locks: OrderedDict[int, asyncio.Lock] = OrderedDict()


def get_user_lock(user_id: int) -> asyncio.Lock:
    """Return a per-user asyncio.Lock, creating one if needed.

    Evicts the least-recently-used entry when the cache exceeds ``_MAX_USER_LOCKS``
    to prevent unbounded memory growth.
    """
    with _global_lock:
        lock = _user_locks.get(user_id)
        if lock is not None:
            _user_locks.move_to_end(user_id)
            return lock

        lock = asyncio.Lock()
        _user_locks[user_id] = lock

        # Evict the oldest entry when the cache is full, but only if the
        # lock is not currently held (to avoid breaking an in-progress turn).
        while len(_user_locks) > _MAX_USER_LOCKS:
            oldest_id, oldest_lock = next(iter(_user_locks.items()))
            if oldest_lock.locked():
                break
            _user_locks.pop(oldest_id)

        return lock
