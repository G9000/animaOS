from __future__ import annotations

from threading import Lock

from anima_server.services.agent.state import StoredMessage


class ThreadStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._threads: dict[int, list[StoredMessage]] = {}

    def read(self, user_id: int) -> list[StoredMessage]:
        with self._lock:
            return list(self._threads.get(user_id, []))

    def append_turn(self, user_id: int, user_message: str, assistant_message: str) -> None:
        with self._lock:
            thread = self._threads.setdefault(user_id, [])
            thread.append(StoredMessage(role="user", content=user_message))
            thread.append(StoredMessage(role="assistant", content=assistant_message))

    def reset(self, user_id: int) -> None:
        with self._lock:
            self._threads.pop(user_id, None)

    def clear(self) -> None:
        with self._lock:
            self._threads.clear()


thread_store = ThreadStore()
