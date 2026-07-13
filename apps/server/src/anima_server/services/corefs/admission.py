from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from threading import BoundedSemaphore, Lock


class FsCredentialAdmissionRejected(RuntimeError):
    def __init__(self, retry_after: int) -> None:
        super().__init__("filesystem credential work is temporarily unavailable")
        self.retry_after = retry_after


class FsCredentialAdmission:
    """Precharge and bound unauthenticated filesystem credential work."""

    def __init__(
        self,
        *,
        max_attempts: int = 5,
        window_seconds: float = 60.0,
        max_concurrency: int = 1,
    ) -> None:
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._attempts: dict[str, deque[float]] = {}
        self._attempts_lock = Lock()
        self._work_slots = BoundedSemaphore(max_concurrency)

    @contextmanager
    def admit(self, client_id: str) -> Iterator[None]:
        now = time.monotonic()
        with self._attempts_lock:
            attempts = self._attempts.setdefault(client_id, deque())
            stale_before = now - self._window_seconds
            while attempts and attempts[0] <= stale_before:
                attempts.popleft()
            if len(attempts) >= self._max_attempts:
                retry_after = math.ceil(attempts[0] + self._window_seconds - now)
                raise FsCredentialAdmissionRejected(max(1, retry_after))
            # Charge before attempting the work slot so a busy rejection cannot
            # be used to probe or amplify Argon2 work without consuming budget.
            attempts.append(now)

        acquired = self._work_slots.acquire(blocking=False)
        if not acquired:
            raise FsCredentialAdmissionRejected(1)
        try:
            yield
        finally:
            self._work_slots.release()

    def reset(self) -> None:
        """Clear precharged attempts between isolated test/application lifetimes."""
        with self._attempts_lock:
            self._attempts.clear()
