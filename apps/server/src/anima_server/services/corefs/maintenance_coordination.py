"""Admission fence for maintenance and operations that pin Core state.

Filesystem mutation authority remains native and OS-locked. This fence prevents
new process-local export/import/credential jobs from being admitted between the
maintenance pin inventory and native lock acquisition.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from threading import RLock

CORE_MAINTENANCE_ADMISSION = RLock()


def serialized_core_maintenance[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        with CORE_MAINTENANCE_ADMISSION:
            return function(*args, **kwargs)

    return wrapped
