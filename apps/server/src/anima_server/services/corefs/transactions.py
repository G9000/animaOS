from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from threading import RLock

# A server process owns one embedded Core. This process-wide lock is acquired
# outside the manifest's publication lock and serializes each credential
# coordinator from source authentication through active-generation verification.
_CREDENTIAL_TRANSACTION_LOCK = RLock()


def serialized_credential_transaction[**P, R](
    function: Callable[P, R],
) -> Callable[P, R]:
    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        with _CREDENTIAL_TRANSACTION_LOCK:
            return function(*args, **kwargs)

    return wrapped
