"""Process-resident stateful companion object.

AnimaCompanion is the cache layer above the stateless AgentRuntime.
It holds the companion's active state between turns:
- Memory blocks (static) — cached, invalidated via version counter
- Conversation window — bounded, appended per turn
- System prompt — rebuilt only when memory changes
- Emotional state — updated by consolidation callback

The runtime itself stays stateless. AnimaCompanion feeds it cached state.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from threading import Lock

from anima_server.config import settings
from anima_server.services.agent.memory_blocks import MemoryBlock, build_runtime_memory_blocks
from anima_server.services.agent.persistence import load_thread_history
from anima_server.services.agent.runtime import AgentRuntime
from anima_server.services.agent.state import StoredMessage
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_companion_lock = Lock()
_companion: AnimaCompanion | None = None


class AnimaCompanion:
    """Process-resident companion holding cached state between turns.

    The runtime loop is unchanged — it receives memory_blocks and history
    as arguments and returns an AgentResult.  This object is the cache layer
    above it.
    """

    def __init__(
        self,
        *,
        runtime: AgentRuntime,
        user_id: int,
        keep_last_messages: int | None = None,
    ) -> None:
        self._runtime = runtime
        self._user_id = user_id
        self._thread_id: int | None = None
        self._keep_last_messages = (
            keep_last_messages if keep_last_messages is not None
            else max(1, settings.agent_compaction_keep_last_messages)
        )

        # Version-counter cache for static memory blocks.
        # _memory_version is bumped on every invalidation.
        # _cache_version records when the cache was last populated.
        # The cache is only reloaded when _cache_version < _memory_version.
        self._memory_version: int = 0
        self._cache_version: int = -1
        self._memory_cache: tuple[MemoryBlock, ...] | None = None

        self._conversation_window: list[StoredMessage] = []
        self._system_prompt: str | None = None
        self._emotional_state: dict[str, object] | None = None

    # ------------------------------------------------------------------
    # Public cache API
    # ------------------------------------------------------------------

    @property
    def runtime(self) -> AgentRuntime:
        return self._runtime

    @property
    def user_id(self) -> int:
        return self._user_id

    @property
    def thread_id(self) -> int | None:
        return self._thread_id

    @thread_id.setter
    def thread_id(self, value: int | None) -> None:
        self._thread_id = value

    @property
    def memory_stale(self) -> bool:
        return self._cache_version < self._memory_version

    def get_cached_memory_blocks(self) -> tuple[MemoryBlock, ...] | None:
        """Return the cached static memory blocks, or None if stale/empty."""
        if self._cache_version < self._memory_version:
            return None
        return self._memory_cache

    def set_memory_cache(self, blocks: tuple[MemoryBlock, ...]) -> None:
        """Populate the memory cache and mark it current."""
        self._memory_cache = blocks
        self._cache_version = self._memory_version

    def invalidate_memory(self) -> None:
        """Bump the version counter.

        Does NOT clear _memory_cache — an in-flight turn continues with
        the data it started with.  The *next* cache read sees the version
        mismatch and reloads.
        """
        self._memory_version += 1
        self._system_prompt = None
        logger.debug(
            "Memory invalidated (version=%d) for user %s",
            self._memory_version, self._user_id,
        )

    # -- system prompt ------------------------------------------------

    def get_cached_system_prompt(self) -> str | None:
        if self.memory_stale:
            return None
        return self._system_prompt

    def set_system_prompt(self, prompt: str) -> None:
        self._system_prompt = prompt

    def invalidate_system_prompt(self) -> None:
        self._system_prompt = None

    # -- conversation window ------------------------------------------

    @property
    def conversation_window(self) -> list[StoredMessage]:
        return self._conversation_window

    def set_conversation_window(self, history: list[StoredMessage]) -> None:
        """Replace the window (used at cold-start and after compaction)."""
        cap = self._keep_last_messages
        if len(history) > cap:
            self._conversation_window = history[-cap:]
        else:
            self._conversation_window = list(history)

    def append_to_window(self, messages: Sequence[StoredMessage]) -> None:
        self._conversation_window.extend(messages)
        cap = self._keep_last_messages
        if len(self._conversation_window) > cap:
            self._conversation_window = self._conversation_window[-cap:]

    # -- emotional state ----------------------------------------------

    @property
    def emotional_state(self) -> dict[str, object] | None:
        return self._emotional_state

    @emotional_state.setter
    def emotional_state(self, value: dict[str, object] | None) -> None:
        self._emotional_state = value

    # -- lifecycle ----------------------------------------------------

    def reset(self, new_thread_id: int | None = None) -> None:
        """Clear all caches.  Called on thread reset or identity reset.

        Reassigning the thread_id ensures that in-flight background tasks
        (consolidation, reflection) writing to the old thread can't corrupt
        the new one.
        """
        self._memory_cache = None
        self._cache_version = -1
        self._memory_version += 1
        self._conversation_window.clear()
        self._system_prompt = None
        self._emotional_state = None
        self._thread_id = new_thread_id
        logger.info("Companion reset for user %s (new thread=%s)",
                    self._user_id, new_thread_id)

    def warm(self, db: Session) -> None:
        """Pre-populate caches.  Call during server startup or first request."""
        if self._thread_id is None:
            from anima_server.services.agent.persistence import get_or_create_thread
            thread = get_or_create_thread(db, self._user_id)
            self._thread_id = thread.id

        # Load static memory blocks (without semantic results — those are per-turn)
        blocks = build_runtime_memory_blocks(
            db,
            user_id=self._user_id,
            thread_id=self._thread_id,
            semantic_results=None,
        )
        self.set_memory_cache(blocks)

        # Load conversation window
        history = load_thread_history(
            db, self._thread_id, user_id=self._user_id)
        self.set_conversation_window(history)

        logger.info(
            "Companion warmed for user %s: %d memory blocks, %d history messages",
            self._user_id, len(blocks), len(self._conversation_window),
        )

    def ensure_memory_loaded(self, db: Session) -> tuple[MemoryBlock, ...]:
        """Return cached static blocks, reloading from DB if stale."""
        cached = self.get_cached_memory_blocks()
        if cached is not None:
            return cached

        if self._thread_id is None:
            from anima_server.services.agent.persistence import get_or_create_thread
            thread = get_or_create_thread(db, self._user_id)
            self._thread_id = thread.id

        blocks = build_runtime_memory_blocks(
            db,
            user_id=self._user_id,
            thread_id=self._thread_id,
            semantic_results=None,
        )
        self.set_memory_cache(blocks)
        return blocks

    def ensure_history_loaded(self, db: Session) -> list[StoredMessage]:
        """Return the conversation window, loading from DB if empty."""
        if self._conversation_window:
            return self._conversation_window

        if self._thread_id is None:
            return []

        history = load_thread_history(
            db, self._thread_id, user_id=self._user_id)
        self.set_conversation_window(history)
        return self._conversation_window


# ------------------------------------------------------------------
# Module-level singleton management
# ------------------------------------------------------------------

def get_companion() -> AnimaCompanion | None:
    """Return the current companion instance, or None if not yet created."""
    return _companion


def get_or_build_companion(runtime: AgentRuntime, user_id: int) -> AnimaCompanion:
    """Return the companion singleton, creating it if needed."""
    global _companion
    if _companion is not None and _companion.user_id == user_id:
        return _companion

    with _companion_lock:
        if _companion is not None and _companion.user_id == user_id:
            return _companion
        _companion = AnimaCompanion(runtime=runtime, user_id=user_id)
        return _companion


def invalidate_companion() -> None:
    """Discard the companion singleton (e.g. on settings change)."""
    global _companion
    with _companion_lock:
        _companion = None
