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

import asyncio
import logging
from collections.abc import Sequence
from threading import Lock

from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.services.agent.memory_blocks import MemoryBlock, build_runtime_memory_blocks
from anima_server.services.agent.persistence import load_thread_history
from anima_server.services.agent.runtime import AgentRuntime
from anima_server.services.agent.state import StoredMessage

logger = logging.getLogger(__name__)

_companion_lock = Lock()
_companions: dict[int, AnimaCompanion] = {}


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
            keep_last_messages
            if keep_last_messages is not None
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

        # Cancellation events keyed by run_id.
        self._cancel_events: dict[int, asyncio.Event] = {}

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
            self._memory_version,
            self._user_id,
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
            self._conversation_window = _trim_at_turn_boundary(history, cap)
        else:
            self._conversation_window = list(history)

    def append_to_window(self, messages: Sequence[StoredMessage]) -> None:
        self._conversation_window.extend(messages)
        cap = self._keep_last_messages
        if len(self._conversation_window) > cap:
            self._conversation_window = _trim_at_turn_boundary(self._conversation_window, cap)

    # -- emotional state ----------------------------------------------

    @property
    def emotional_state(self) -> dict[str, object] | None:
        return self._emotional_state

    @emotional_state.setter
    def emotional_state(self, value: dict[str, object] | None) -> None:
        self._emotional_state = value

    # -- cancellation -------------------------------------------------

    def create_cancel_event(self, run_id: int) -> asyncio.Event:
        """Create and return a cancellation event for *run_id*."""
        event = asyncio.Event()
        self._cancel_events[run_id] = event
        return event

    def set_cancel(self, run_id: int) -> None:
        """Signal cancellation for *run_id* (idempotent)."""
        event = self._cancel_events.get(run_id)
        if event is not None:
            event.set()
        else:
            # Run may have already completed; create a pre-set event
            # so any late check still sees the cancellation.
            ev = asyncio.Event()
            ev.set()
            self._cancel_events[run_id] = ev

    def is_cancelled(self, run_id: int) -> bool:
        """Check whether *run_id* has been cancelled."""
        event = self._cancel_events.get(run_id)
        return event is not None and event.is_set()

    def get_cancel_event(self, run_id: int) -> asyncio.Event | None:
        """Return the cancel event for *run_id*, or None."""
        return self._cancel_events.get(run_id)

    def clear_cancel_event(self, run_id: int) -> None:
        """Remove the cancel event once the run is terminal."""
        self._cancel_events.pop(run_id, None)

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
        logger.info("Companion reset for user %s (new thread=%s)", self._user_id, new_thread_id)

    def warm(self, db: Session, runtime_db: Session) -> None:
        """Pre-populate caches.  Call during server startup or first request."""
        if self._thread_id is None:
            from anima_server.services.agent.persistence import get_or_create_thread

            thread = get_or_create_thread(runtime_db, self._user_id)
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
        history = load_thread_history(runtime_db, self._thread_id, user_id=self._user_id)
        self.set_conversation_window(history)

        logger.info(
            "Companion warmed for user %s: %d memory blocks, %d history messages",
            self._user_id,
            len(blocks),
            len(self._conversation_window),
        )

    def ensure_memory_loaded(self, db: Session) -> tuple[MemoryBlock, ...]:
        """Return cached static blocks, reloading from DB if stale."""
        cached = self.get_cached_memory_blocks()
        if cached is not None:
            return cached

        if self._thread_id is None:
            return ()

        blocks = build_runtime_memory_blocks(
            db,
            user_id=self._user_id,
            thread_id=self._thread_id,
            semantic_results=None,
        )
        self.set_memory_cache(blocks)
        return blocks

    def invalidate_history(self) -> None:
        """Clear the conversation window so the next call reloads from DB."""
        self._conversation_window.clear()

    def ensure_history_loaded(self, db: Session) -> list[StoredMessage]:
        """Return the conversation window, loading from DB if empty."""
        if self._conversation_window:
            return self._conversation_window

        if self._thread_id is None:
            return []

        history = load_thread_history(db, self._thread_id, user_id=self._user_id)
        self.set_conversation_window(history)
        return self._conversation_window


def _trim_at_turn_boundary(
    messages: list[StoredMessage],
    cap: int,
) -> list[StoredMessage]:
    """Trim a message list to approximately *cap* messages at a safe boundary.

    Instead of a naive ``[-cap:]`` slice that can cut in the middle of an
    assistant+tool_call+tool_result sequence, this finds the nearest safe
    boundary near the target cut point.  A safe boundary is a message with
    role ``user`` or ``assistant`` (not ``tool``), so tool result messages
    always have their paired assistant message present in the window.
    """
    if len(messages) <= cap:
        return list(messages)

    # Target cut index (messages before this are dropped)
    cut = len(messages) - cap

    # If the cut point already lands on a safe boundary, use it.
    if messages[cut].role in ("user", "assistant"):
        return list(messages[cut:])

    # Walk backward from cut to find a safe start (user or assistant).
    # This keeps slightly more messages than cap but avoids orphaned
    # tool results referencing a truncated assistant message.
    for i in range(cut - 1, -1, -1):
        if messages[i].role in ("user", "assistant"):
            return list(messages[i:])

    # Walk forward from cut to find the next safe start.
    for i in range(cut + 1, len(messages)):
        if messages[i].role in ("user", "assistant"):
            return list(messages[i:])

    return list(messages[-cap:])


# ------------------------------------------------------------------
# Module-level singleton management
# ------------------------------------------------------------------


def get_companion(user_id: int | None = None) -> AnimaCompanion | None:
    """Return a companion instance by user_id, or None if not yet created.

    When *user_id* is None (e.g. called from a tool without user context),
    returns the companion only if there is exactly one active companion.
    """
    if user_id is not None:
        return _companions.get(user_id)
    if len(_companions) == 1:
        return next(iter(_companions.values()))
    return None


def get_or_build_companion(runtime: AgentRuntime, user_id: int) -> AnimaCompanion:
    """Return the companion for *user_id*, creating it if needed."""
    existing = _companions.get(user_id)
    if existing is not None:
        return existing

    with _companion_lock:
        existing = _companions.get(user_id)
        if existing is not None:
            return existing
        companion = AnimaCompanion(runtime=runtime, user_id=user_id)
        _companions[user_id] = companion
        return companion


def invalidate_companion(user_id: int | None = None) -> None:
    """Discard companion(s). If *user_id* given, discard only that one."""
    with _companion_lock:
        if user_id is not None:
            _companions.pop(user_id, None)
        else:
            _companions.clear()
