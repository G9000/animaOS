"""Proactive greeting generation: the agent initiates with context-aware messages.

Generates personalized greetings when the user opens the app, drawing on:
- Self-model (identity, inner state, working memory)
- Emotional context (last known emotional state)
- Pending tasks and deadlines
- Time since last conversation
- Recent episodes and memories
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models import AgentMessage, AgentThread, MemoryEpisode, Task
from anima_server.services.corefs.sealed_runtime import seal_runtime_fields
from anima_server.services.data_crypto import df
from anima_server.services.presence_config import (
    PresenceConfigValues,
    get_presence_config_values,
)

logger = logging.getLogger(__name__)

_GREETING_LLM_TIMEOUT_SECONDS = 8.0
_PROACTIVE_NOTICE_LLM_TIMEOUT_SECONDS = 2.0
_GREETING_PILLS_LLM_TIMEOUT_SECONDS = 6.0
_GREETING_LLM_MAX_TOKENS = 64
_PROACTIVE_NOTICE_LLM_MAX_TOKENS = 64
_GREETING_PILLS_LLM_MAX_TOKENS = 120
_OLLAMA_NATIVE_KEEP_ALIVE = "10m"

# Tags the greeting LLM is allowed to emit. Anything else is dropped so a
# stray hallucinated kind can't leak into the UI.
_GREETING_PILL_KINDS = frozenset({"topic", "emotion", "memory", "task", "time"})
_MAX_GREETING_PILLS = 4
_MAX_GREETING_PILL_LABEL_CHARS = 22


@dataclass(frozen=True)
class GreetingContext:
    current_focus: str | None = None
    open_task_count: int = 0
    overdue_task_count: int = 0
    upcoming_deadlines: list[str] = field(default_factory=list)
    days_since_last_chat: int | None = None
    identity_summary: str | None = None
    emotional_summary: str | None = None
    inner_state_summary: str | None = None
    working_memory_summary: str | None = None
    recent_episode_summary: str | None = None
    is_birthday: bool = False
    days_until_birthday: int | None = None
    # IL-011: one open thread that GENUINELY stayed active while the user was
    # away — verbatim-traceable to a persisted ForesightSignal whose
    # unresolved_thread pressure actually accumulated over the gap. None
    # whenever any grounding condition fails; never synthesized.
    held_thought: str | None = None
    # IL-010: a real dream the "ambient" dream-sharing mode may weave into
    # this greeting — verbatim from the most recent share-worthy unsurfaced
    # dream_journal row (marked surfaced on hand-off). None unless the user
    # explicitly chose the ambient mode; never synthesized.
    ambient_dream: str | None = None


@dataclass(frozen=True)
class GreetingResult:
    message: str
    context: GreetingContext
    llm_generated: bool = False
    pills: list[dict[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # IL-010 (PR #130 review): the same greeting WITHOUT the ambient-dream
    # sentence, for surfaces that forward greeting text into an LLM prompt
    # (the dashboard "explore" handoff seeds chat context). Set only when a
    # dream was actually appended; None means `message` is already safe.
    handoff_message: str | None = None


@dataclass(frozen=True)
class ProactiveNoticeResult:
    id: str
    message: str
    context: GreetingContext
    source: str = "proactive_notice"
    llm_generated: bool = False
    pills: list[dict[str, object]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProactiveImageCandidate:
    image_asset_id: int
    filename: str | None
    source_message_id: int | None
    source_thread_id: int | None
    attachment_id: str | None
    attachment_url: str | None
    snippet: str
    pills: list[dict[str, object]]


@dataclass(frozen=True)
class AgentStateResult:
    user_id: int
    dominant_emotion: str | None
    thought: str
    thought_source: str
    chat_prompt: str
    context_messages: list[dict[str, str]]
    affect_hint: str | None = None


_STATE_THOUGHT_MAX_CHARS = 72

_EMOTION_STATE_LINES = {
    "calm": "quietly present",
    "curious": "following a thread",
    "excited": "holding momentum",
    "hopeful": "keeping a door open",
    "grateful": "noticing what matters",
    "content": "settled for now",
    "playful": "light on the edge",
    "affectionate": "close and attentive",
    "anxious": "holding steady",
    "stressed": "keeping things contained",
    "overwhelmed": "staying with one thread",
    "frustrated": "working through friction",
    "disappointed": "letting the signal settle",
    "sad": "softly present",
    "lonely": "staying near",
    "tired": "low power, still here",
    "relieved": "letting the pressure drop",
}


def _dominant_emotion_from_summary(summary: str | None) -> str | None:
    if not summary:
        return None
    emotion = summary.split("(", 1)[0].strip().lower()
    return emotion or None


def _compact_state_line(value: str | None) -> str | None:
    if not value:
        return None

    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^[#>*\-\s]+", "", line).strip()
        line = re.sub(r"^[A-Za-z][A-Za-z\s_-]{1,32}:\s+", "", line).strip()
        line = re.sub(r"\s+", " ", line)
        if not line:
            continue
        if len(line) <= _STATE_THOUGHT_MAX_CHARS:
            return line
        shortened = line[: _STATE_THOUGHT_MAX_CHARS + 1].rsplit(" ", 1)[0].strip()
        return f"{shortened}..." if shortened else f"{line[:_STATE_THOUGHT_MAX_CHARS]}..."

    return None


def _state_context_message(
    *,
    thought: str,
    dominant_emotion: str | None,
    affect_hint: str | None = None,
) -> str:
    sentence = thought.rstrip(".!?")
    content = f"Current companion state: {sentence}."
    if dominant_emotion:
        content += f" Recent emotion: {dominant_emotion}."
    if affect_hint:
        content += f" Inner tone: {affect_hint}."
    return content


def _birthday_context(birthday_str: str | None, today: date) -> tuple[bool, int | None]:
    """Return (is_birthday, days_until_birthday) given a stored birthday string."""
    if not birthday_str:
        return False, None
    raw = birthday_str.strip()
    bday: date | None = None
    for fmt in ("%Y-%m-%d", "%m-%d", "%m/%d", "%m/%d/%Y"):
        try:
            parsed = datetime.strptime(raw, fmt).date()
            bday = parsed.replace(year=today.year)
            break
        except ValueError:
            continue
    if bday is None:
        return False, None

    if bday.month == today.month and bday.day == today.day:
        return True, 0

    if bday < today:
        bday = bday.replace(year=today.year + 1)
    days = (bday - today).days
    return False, days


def _normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _latest_runtime_user_message(runtime_db: Session, user_id: int) -> object | None:
    try:
        from anima_server.models.runtime import RuntimeMessage

        messages = runtime_db.scalars(
            select(RuntimeMessage)
            .where(
                RuntimeMessage.user_id == user_id,
                RuntimeMessage.role == "user",
            )
            .order_by(RuntimeMessage.created_at.desc())
        )
        return next(
            (
                message
                for message in messages
                if message.content_text not in (None, "")
            ),
            None,
        )
    except Exception as exc:
        logger.debug("Runtime greeting history lookup failed: %s", exc)
        return None


def _ollama_native_base_url() -> str:
    base_url = settings.agent_base_url.strip() or "http://localhost:11434"
    base_url = base_url.rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3].rstrip("/")
    return base_url


async def _invoke_ollama_native_chat(
    messages: list[dict[str, str]],
    *,
    timeout: float,
    max_tokens: int,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str | None:
    options: dict[str, object] = {
        "num_predict": max_tokens,
    }
    if settings.agent_temperature is not None:
        options["temperature"] = settings.agent_temperature

    payload: dict[str, object] = {
        "model": settings.agent_model,
        "messages": messages,
        "stream": False,
        "think": False,
        "keep_alive": _OLLAMA_NATIVE_KEEP_ALIVE,
        "options": options,
    }

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, read=timeout),
        transport=transport,
    ) as client:
        response = await client.post(f"{_ollama_native_base_url()}/api/chat", json=payload)
    response.raise_for_status()

    body = response.json()
    if not isinstance(body, dict):
        return None
    message = body.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, str):
        return None
    stripped = content.strip()
    return stripped or None


def _finalize_ambient_dream(
    db: Session,
    ctx: GreetingContext,
    claim: AmbientDreamClaim | None,
    *,
    user_id: int,
    message: str,
) -> tuple[str, GreetingContext, str | None]:
    """Decide and APPLY the dream's voicing atomically w.r.t. consent
    updates (PR #130 review rounds 9-10).

    The claim commits before generation, and the greeting + pill requests
    can take ~14s combined, so consent must be re-read afterwards. Round 9
    did that read WITHOUT the lock, which is still check-then-act: an
    opt-out could commit between the read and the append. The whole
    decision — re-read, release-or-append — therefore runs under the same
    per-user ``presence_consent_lock`` the config PUT holds through its
    commit, so the two are mutually exclusive.

    Returns ``(message, ctx, handoff_message)``: on consent the dream
    sentence is appended and the pre-append text becomes the handoff copy;
    on withdrawal the claim is RELEASED (the dream stays available for a
    later greeting) — the server knows the narrative never reached the
    user, the opposite trade from IL-015's unknowable client receipt.

    Residual window, unavoidable and shared with
    ``delivery.list_and_mark_delivered``: an opt-out committing while the
    HTTP response is already in flight. No server-side ordering removes it.
    """
    if claim is None or not ctx.ambient_dream:
        return message, ctx, None

    from anima_server.services.presence_config import presence_consent_lock

    with presence_consent_lock(user_id):
        db.expire_all()
        values = get_presence_config_values(db, user_id)
        if values.enabled and values.dream_sharing == "ambient":
            # Append INSIDE the lock: an opt-out can no longer slip between
            # the check and the hand-off.
            return (
                f"{message} {_ambient_dream_sentence(ctx.ambient_dream)}",
                ctx,
                message,
            )
        _release_ambient_dream_claim(db, dream_id=claim.dream_id)
        logger.info(
            "Ambient consent withdrawn during greeting generation for user %s; "
            "dream released unvoiced",
            user_id,
        )
        return message, dataclasses.replace(ctx, ambient_dream=None), None


def _dream_free_static_greeting(ctx: GreetingContext) -> str | None:
    """The static greeting rebuilt WITHOUT the ambient-dream sentence, or
    None when no dream was woven (the message is then already safe to hand
    to an LLM surface)."""
    if not ctx.ambient_dream:
        return None
    return build_static_greeting(dataclasses.replace(ctx, ambient_dream=None))


def _ambient_dream_sentence(dream: str) -> str:
    """The ONE rendering of a consumed ambient dream — used verbatim by both
    the static greeting and the LLM-greeting append, so a claimed dream is
    always voiced identically and deterministically."""
    return f'I dreamt about something recently — "{dream}".'


@dataclass(frozen=True)
class AmbientDreamClaim:
    """A dream marked surfaced for THIS greeting. Carries its id so the
    claim can be released if the greeting ends up not voicing it (PR #130
    review): releasing is only safe when the server knows for certain the
    narrative never reached the response — otherwise silence is preferred
    (see IL-015)."""

    dream_id: int
    narrative: str


def _release_ambient_dream_claim(db: Session, *, dream_id: int) -> None:
    """Un-surface a dream this request claimed but will NOT voice."""
    from sqlalchemy import update

    from anima_server.models import DreamJournal

    try:
        db.execute(
            update(DreamJournal)
            .where(DreamJournal.id == dream_id)
            .values(surfaced=False)
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.warning(
            "Failed to release ambient dream claim %s", dream_id, exc_info=True
        )


def _resolve_ambient_dream(db: Session, *, user_id: int) -> AmbientDreamClaim | None:
    """IL-010: claim the dream an "ambient" greeting will voice, or None.

    Gives ``presence_config.dream_sharing == "ambient"`` its real behavior
    (the IL-007 PRD's "the companion may weave a dream into greetings").
    Grounded like the held thought — every condition is a persisted signal:

    1. Consent: the Presence master switch is on AND dream_sharing is
       exactly ``"ambient"`` — ``on_ask`` stays ask-or-IL3-fire only and
       ``off`` stays fully suppressed.
    2. An active memories DEK (``df`` fails open; without one the read
       would hand ciphertext to the greeting).
    3. A real dream: the most recent share-worthy, not-yet-surfaced
       ``dream_journal`` row.

    Consume-once, atomically (PR #130 review): the claim is a CONDITIONAL
    update (``... WHERE surfaced = 0``) — under concurrent greeting
    requests only the transaction whose update actually flips the flag
    returns the dream; the loser sees rowcount 0 and stays silent. The
    claim commits here because greeting sessions never commit otherwise.

    Callers must GUARANTEE the claimed dream is voiced: this is called
    ONLY from ``generate_greeting`` (never from the shared
    ``gather_greeting_context``, which non-greeting paths like agent-state
    and reflection also use — resolving there burned dreams invisibly),
    and the greeting message always includes
    ``_ambient_dream_sentence(dream)`` deterministically — appended to the
    LLM output rather than entrusted to the model's discretion.
    """
    from sqlalchemy import update
    from sqlalchemy.exc import OperationalError

    from anima_server.models import DreamJournal
    from anima_server.services.crypto import ENCRYPTED_PREFIX
    from anima_server.services.data_crypto import DOMAIN_MEMORIES
    from anima_server.services.presence_config import presence_consent_lock
    from anima_server.services.sessions import get_active_dek

    # Cheap pre-check outside the lock: the overwhelming majority of
    # greetings are not in ambient mode, and this avoids serializing them.
    values = get_presence_config_values(db, user_id)
    if not (values.enabled and values.dream_sharing == "ambient"):
        return None
    if get_active_dek(user_id, DOMAIN_MEMORIES) is None:
        return None

    # End the read transaction the consent check opened (PR #130 review):
    # under WAL, two real connections that both read before one commits a
    # write leave the loser unable to upgrade its stale snapshot — its
    # UPDATE raises SQLITE_BUSY_SNAPSHOT instead of reporting rowcount 0.
    # Greeting sessions are read-only apart from this claim, so ending the
    # transaction here loses nothing; the claim below is then a SINGLE
    # statement (candidate selection folded in as a scalar subquery) that
    # begins directly as a write.
    db.rollback()
    candidate_id = (
        select(DreamJournal.id)
        .where(
            DreamJournal.user_id == user_id,
            DreamJournal.share_worthy.is_(True),
            DreamJournal.surfaced.is_(False),
        )
        .order_by(DreamJournal.dreamt_at.desc())
        .limit(1)
        .scalar_subquery()
    )
    # Hold the SAME per-user consent lock the presence-config PUT holds
    # through its commit (PR #130 review, P1) — the pre-check above is
    # unlocked, so an opt-out committing between it and the claim would
    # otherwise consume and voice a dream after the user said stop. Inside
    # the lock we re-read consent on FRESH state (expire_all, mirroring
    # delivery._initiative_consent_allows) and only then claim, so an
    # opt-out either lands before the re-read (silence) or blocks until the
    # claim decision is made (the opt-out post-dates the voicing).
    with presence_consent_lock(user_id):
        db.expire_all()
        fresh = get_presence_config_values(db, user_id)
        if not (fresh.enabled and fresh.dream_sharing == "ambient"):
            db.rollback()
            return None
        db.rollback()  # end the re-read's snapshot before the write
        try:
            claimed = db.execute(
                update(DreamJournal)
                .where(
                    DreamJournal.id == candidate_id,
                    DreamJournal.surfaced.is_(False),
                )
                .values(surfaced=True)
                .returning(DreamJournal.id, DreamJournal.narrative)
            ).first()
            if claimed is None:
                db.rollback()
                return None
            # Decrypt and validate BEFORE the claim becomes durable (PR #130
            # review): a corrupt ciphertext/AAD or a DEK revoked since the
            # check above would otherwise burn the dream on a claim nothing
            # can voice — df() either raises or fails open to ciphertext.
            # RETURNING gives us the value pre-commit, so an unusable
            # narrative rolls the claim back and the entry stays retriable.
            narrative = (
                df(user_id, claimed.narrative, table="dream_journal", field="narrative")
                or ""
            ).strip()
            # df() fails OPEN (returns the stored value) when the DEK went
            # away, so an intact ciphertext envelope means "not decrypted".
            still_ciphertext = narrative.startswith(f"{ENCRYPTED_PREFIX}:")
            if not narrative or still_ciphertext:
                if still_ciphertext:
                    logger.warning(
                        "Ambient dream narrative did not decrypt for user %s; "
                        "leaving the dream unsurfaced",
                        user_id,
                    )
                db.rollback()
                return None
            db.commit()
        except OperationalError:
            # Lost a genuine lock race despite the busy timeout: silence.
            db.rollback()
            return None
        except Exception:
            # Decryption raised (corrupt AAD, revoked DEK, ...): never burn
            # the claim on a dream we cannot read.
            logger.warning(
                "Ambient dream claim aborted for user %s", user_id, exc_info=True
            )
            db.rollback()
            return None

    return AmbientDreamClaim(dream_id=claimed.id, narrative=narrative[:240])


def _reset_dream_residue_after_surfacing(
    runtime_db: Session | None, *, user_id: int
) -> None:
    """IL-010 (PR #130 review): surfacing a dream through the ambient
    greeting must drain the runtime ``dream_residue`` pressure exactly like
    the initiative-delivery path does (``reset_drive`` + starvation-history
    clear at fire time). Otherwise pressure accumulated FOR the voiced dream
    lingers, leaks for days, and transfers to the next unrelated dream —
    letting it hit the initiative threshold prematurely. Best-effort: the
    greeting must never fail on runtime-store trouble."""
    if runtime_db is None:
        return
    try:
        from anima_server.models.runtime_consciousness import DriveStateRow

        row = runtime_db.scalar(
            select(DriveStateRow).where(DriveStateRow.user_id == user_id)
        )
        if row is None:
            return
        row.dream_residue = 0.0
        losses = dict(row.starvation_losses or {})
        if losses.pop("dream_residue", None) is not None:
            row.starvation_losses = losses
        runtime_db.commit()
    except Exception:
        logger.warning(
            "Failed to reset dream_residue after ambient surfacing for user %s",
            user_id,
            exc_info=True,
        )


def _resolve_held_thought(
    db: Session,
    runtime_db: Session | None,
    *,
    user_id: int,
    last_message_at: datetime | None,
    now: datetime,
    tz: tzinfo | None = None,
) -> str | None:
    """IL-011: the one open thread that genuinely stayed with the agent over
    the absence, or None. Grounded, never confabulated — every condition is a
    real persisted signal, and if any fails there is no held thought:

    1. Consent: the Presence master switch AND home-greeting context are on.
    2. A real absence: the gap since the user's last message is at least
       ``greeting_held_thought_min_gap_hours``.
    3. Accumulated pressure: the runtime ``unresolved_thread`` drive is at or
       above ``greeting_held_thought_min_pressure`` — i.e. the thread
       measurably built up while they were away, we are not inventing
       preoccupation after the fact. The stored pressure counts only if the
       drive tick has already processed the user's latest message (PR #128
       review): a user turn hard-resets this drive, so pressure whose
       ``last_user_turn_at`` predates ``last_message_at`` is a pre-turn
       leftover the reset simply hasn't reached yet — never ground on it.
    4. The material exists AND spans the gap: an open, in-horizon
       ForesightSignal — the SAME definition (and same soonest-first pick)
       IL3 uses for the drive's grow signal — that was ALREADY PERSISTED
       before the user's last message (PR #128 review). The drive's grow
       condition is aggregate ("any open in-horizon signal"), so a signal
       created mid-gap could otherwise be voiced on pressure a different,
       since-resolved thread accumulated; requiring the voiced row to
       predate the absence means it was itself an open contributor for the
       whole gap, making "this stayed with me while you were away" true of
       exactly this thread. If the row IL3 would voice fails that check, we
       stay silent rather than voice a different row than IL3's own
       material query would pick. The horizon is evaluated on the LOCAL
       calendar date (PR #128 review), matching the tick's local-time
       discipline — a UTC date would disagree with the query that
       accumulated the pressure for part of every local day.

    Requires an active memories DEK (``df`` fails open, so without one the
    decrypted read would return ciphertext into the greeting prompt).
    ``tz`` is a test seam for the local zone; it defaults to the real system
    zone, same as the drive tick."""
    from anima_server.services.presence_config import get_presence_config_values

    values = get_presence_config_values(db, user_id)
    if not (values.enabled and values.home_greeting_context_enabled):
        return None

    if last_message_at is None:
        return None  # first meeting: nothing was ever left open
    last_message_utc = _normalize_utc(last_message_at)
    gap_hours = (now - last_message_utc).total_seconds() / 3600.0
    if gap_hours < settings.greeting_held_thought_min_gap_hours:
        return None

    if runtime_db is None:
        return None
    from anima_server.models.runtime_consciousness import DriveStateRow

    drive_row = runtime_db.scalar(
        select(DriveStateRow).where(DriveStateRow.user_id == user_id)
    )
    if (
        drive_row is None
        or drive_row.unresolved_thread < settings.greeting_held_thought_min_pressure
    ):
        return None
    # Stale-pressure guard (condition 3 above): the tick must have seen the
    # latest user message, or the pressure predates a reset it still owes.
    if drive_row.last_user_turn_at is None or (
        _normalize_utc(drive_row.last_user_turn_at) < last_message_utc
    ):
        return None

    from anima_server.services.agent.inner_life.initiative import (
        _open_in_horizon_foresight,
    )
    from anima_server.services.agent.inner_life.presence import system_zoneinfo
    from anima_server.services.data_crypto import DOMAIN_MEMORIES
    from anima_server.services.sessions import get_active_dek

    if get_active_dek(user_id, DOMAIN_MEMORIES) is None:
        return None
    local_zone = tz or system_zoneinfo()
    local_today = now.astimezone(local_zone).date()
    horizon_days = settings.initiative_unresolved_thread_horizon_days
    row = db.scalar(
        _open_in_horizon_foresight(user_id, local_today, horizon_days).limit(1)
    )
    if row is None:
        return None
    # Gap-spanning check (condition 4 above), in Python rather than SQL so
    # SQLite's string-typed datetime comparison can't mis-order aware vs
    # naive values. Compared on ``observed_at`` — the source MESSAGE's
    # timestamp, recorded by the consolidation write path — not the row's
    # insertion time (PR #128 review round 5): extraction runs after the
    # conversation, so a thread from the user's final pre-gap turn gets
    # ``created_at`` minutes INTO the gap and an insertion-time check would
    # reject the primary held-thought scenario. ``created_at`` remains the
    # conservative fallback for provenance-less rows; no timestamp at all
    # means no grounding, so silence.
    anchored_at = row.observed_at or row.created_at
    if anchored_at is None or _normalize_utc(anchored_at) > last_message_utc:
        return None
    # In-horizon at gap start too (PR #128 review round 6): offline gaps are
    # backfilled by the first tick against the CURRENT horizon, so a signal
    # whose start_date only entered the sliding window mid-gap can inherit
    # the whole gap's aggregate growth despite being ineligible to grow it
    # when the user left. Require eligibility on the last-message local date
    # as well — the same `start_date <= date + horizon` predicate the query
    # applies to the return date.
    gap_start_local_date = last_message_utc.astimezone(local_zone).date()
    if row.start_date is None or row.start_date > gap_start_local_date + timedelta(
        days=horizon_days
    ):
        return None
    content = df(user_id, row.content, table="foresight_signals", field="content")
    content = (content or "").strip()
    return content[:200] or None


def gather_greeting_context(
    db: Session,
    user_id: int,
    runtime_db: Session | None = None,
) -> GreetingContext:
    """Collect context for greeting generation."""
    # Get tasks info
    now = datetime.now(UTC)
    tasks = db.scalars(
        select(Task).where(Task.user_id == user_id,
                           Task.completed_at.is_(None))
    ).all()

    open_count = 0
    overdue_count = 0
    deadlines: list[str] = []

    for task in tasks:
        open_count += 1
        if task.due_date:
            if task.due_date < now:
                overdue_count += 1
            elif (task.due_date - now).days <= 3:
                deadlines.append(task.title)

    # Get last conversation time
    last_message = (
        _latest_runtime_user_message(runtime_db, user_id)
        if runtime_db is not None
        else None
    )
    if last_message is None:
        last_message = db.scalar(
            select(AgentMessage)
            .join(AgentThread, AgentMessage.thread_id == AgentThread.id)
            .where(AgentThread.user_id == user_id, AgentMessage.role == "user")
            .order_by(AgentMessage.created_at.desc())
        )

    days_since = None
    if last_message and last_message.created_at:
        delta = now - _normalize_utc(last_message.created_at)
        days_since = delta.days

    held_thought = _resolve_held_thought(
        db,
        runtime_db,
        user_id=user_id,
        last_message_at=last_message.created_at if last_message else None,
        now=now,
    )
    # NB: the ambient dream is deliberately NOT resolved here (PR #130
    # review): this gatherer is shared with non-greeting paths (agent state,
    # reflection) that never render it — resolving here would consume dreams
    # invisibly. generate_greeting resolves it and guarantees the voicing.

    # Get recent episode summary
    recent_episode = db.scalar(
        select(MemoryEpisode)
        .where(MemoryEpisode.user_id == user_id)
        .order_by(MemoryEpisode.created_at.desc())
    )

    episode_summary = None
    if recent_episode:
        episode_summary = df(
            user_id, recent_episode.summary, table="memory_episodes", field="summary"
        )

    # Get self-model sections for context
    from anima_server.services.agent.self_model import (
        get_identity_block,
        get_self_model_block,
        get_working_context,
        render_self_model_section,
    )

    identity_block = get_identity_block(db, user_id=user_id)
    identity_summary = (
        render_self_model_section(identity_block, user_id=user_id)
        if identity_block
        else None
    )

    inner_state_block = None
    working_memory_block = None
    try:
        from anima_server.db.runtime import get_runtime_session_factory

        with get_runtime_session_factory()() as runtime_db:
            working_context = get_working_context(runtime_db, user_id=user_id)
            inner_state_block = working_context.get("inner_state")
            working_memory_block = working_context.get("working_memory")
    except Exception:
        inner_state_block = get_self_model_block(
            db, user_id=user_id, section="inner_state")
        working_memory_block = get_self_model_block(
            db, user_id=user_id, section="working_memory")

    inner_state_summary = (
        render_self_model_section(inner_state_block, user_id=user_id)
        if inner_state_block
        else None
    )
    working_memory_summary = (
        render_self_model_section(working_memory_block, user_id=user_id)
        if working_memory_block
        else None
    )

    # Get emotional context (read from runtime PG where signals now live)
    from anima_server.services.agent.emotional_intelligence import (
        get_recent_signals,
    )

    emotion_db = db
    _own_emotion_session = None
    try:
        from anima_server.db.runtime import get_runtime_session_factory

        _own_emotion_session = get_runtime_session_factory()()
        emotion_db = _own_emotion_session
    except Exception:
        pass  # fall back to soul DB

    try:
        signals = get_recent_signals(emotion_db, user_id=user_id, limit=1)
    finally:
        if _own_emotion_session is not None:
            _own_emotion_session.close()
    emotional_summary = None
    if signals:
        s = signals[0]
        emotional_summary = f"{s.emotion} ({s.trajectory})"

    # Get birthday context from user profile
    is_birthday = False
    days_until_birthday: int | None = None
    try:
        from sqlalchemy import text as _text

        birthday_row = db.execute(
            _text("SELECT birthday FROM users WHERE id = :uid"),
            {"uid": user_id},
        ).fetchone()
        birthday_str = birthday_row[0] if birthday_row else None
        is_birthday, days_until_birthday = _birthday_context(birthday_str, now.date())
    except Exception:
        pass

    return GreetingContext(
        current_focus=None,  # Could fetch from intentions
        open_task_count=open_count,
        overdue_task_count=overdue_count,
        upcoming_deadlines=deadlines,
        days_since_last_chat=days_since,
        identity_summary=identity_summary,
        emotional_summary=emotional_summary,
        inner_state_summary=inner_state_summary,
        working_memory_summary=working_memory_summary,
        recent_episode_summary=episode_summary,
        is_birthday=is_birthday,
        days_until_birthday=days_until_birthday,
        held_thought=held_thought,
    )


def build_static_greeting(ctx: GreetingContext) -> str:
    """Build a simple static greeting when LLM is unavailable."""
    parts: list[str] = []

    if ctx.is_birthday:
        parts.append("Happy birthday!")
    elif ctx.days_since_last_chat is None:
        parts.append("Hello! I'm glad to meet you.")
    elif ctx.days_since_last_chat == 0:
        parts.append("Hello again!")
    elif ctx.days_since_last_chat == 1:
        parts.append("Good to see you today.")
    else:
        parts.append(
            f"It's been {ctx.days_since_last_chat} days. Welcome back.")

    if ctx.held_thought:
        parts.append(f'Something you mentioned stayed with me — "{ctx.held_thought}".')

    if ctx.ambient_dream:
        parts.append(_ambient_dream_sentence(ctx.ambient_dream))

    if ctx.overdue_task_count:
        s = "s" if ctx.overdue_task_count != 1 else ""
        parts.append(f"You have {ctx.overdue_task_count} overdue task{s}.")

    return " ".join(parts)


def build_static_proactive_notice(
    ctx: GreetingContext,
    *,
    instruction: str | None = None,
    config: PresenceConfigValues | None = None,
) -> str | None:
    """Build a quiet in-chat proactive notice without requiring an LLM."""
    allow_tasks = config.task_nudges_enabled if config is not None else True
    allow_memory = config.memory_nudges_enabled if config is not None else True
    allow_checkins = config.checkin_nudges_enabled if config is not None else True

    if ctx.is_birthday:
        return "It's your birthday today. Hope it's a good one."

    if ctx.days_until_birthday is not None and 1 <= ctx.days_until_birthday <= 7:
        return f"Your birthday is in {ctx.days_until_birthday} day{'s' if ctx.days_until_birthday != 1 else ''}."

    custom = (instruction or "").strip()
    if custom:
        return f"{custom[:1].upper()}{custom[1:]}. Want to start there?"

    if allow_tasks and ctx.overdue_task_count:
        s = "s" if ctx.overdue_task_count != 1 else ""
        return f"You have {ctx.overdue_task_count} overdue task{s}. Want to sort them together?"

    if allow_tasks and ctx.upcoming_deadlines:
        return f"{ctx.upcoming_deadlines[0]} is coming up soon. Want to look at it together?"

    if allow_checkins and ctx.days_since_last_chat and ctx.days_since_last_chat > 0:
        return "There is a thread from last time we can pick back up if you want."

    if allow_memory and ctx.working_memory_summary:
        return "I am holding a few open threads for you. Want to choose one?"

    return None


def select_proactive_image_candidate(
    runtime_db: Session,
    *,
    user_id: int,
) -> ProactiveImageCandidate | None:
    """Select one indexed image that has not already been proactively surfaced."""
    from anima_server.models.runtime import (
        RuntimeImageAnnotation,
        RuntimeImageAsset,
        RuntimeImageMessageLink,
        RuntimeMessage,
    )
    from anima_server.models.runtime_embedding import RuntimeEmbedding

    rows = runtime_db.execute(
        select(RuntimeImageAsset, RuntimeImageAnnotation)
        .join(
            RuntimeImageAnnotation,
            RuntimeImageAnnotation.image_asset_id == RuntimeImageAsset.id,
        )
        .where(
            RuntimeImageAsset.user_id == user_id,
            RuntimeImageAsset.status == "indexed",
            RuntimeImageAnnotation.user_id == user_id,
            RuntimeImageAnnotation.status == "active",
            select(RuntimeEmbedding.id)
            .where(
                RuntimeEmbedding.user_id == user_id,
                RuntimeEmbedding.source_type == "image_annotation",
                RuntimeEmbedding.source_id == RuntimeImageAnnotation.id,
                RuntimeEmbedding.content_hash == RuntimeImageAnnotation.content_hash,
            )
            .exists(),
        )
        .order_by(RuntimeImageAsset.created_at.desc(), RuntimeImageAnnotation.created_at.desc())
        .limit(50)
    ).all()

    seen_assets: set[int] = set()
    for asset, annotation in rows:
        if asset.id in seen_assets:
            continue
        seen_assets.add(asset.id)
        metadata = asset.metadata_json if isinstance(asset.metadata_json, dict) else {}
        if metadata.get("proactivePromptedAt"):
            continue

        source = runtime_db.execute(
            select(RuntimeImageMessageLink, RuntimeMessage)
            .join(RuntimeMessage, RuntimeImageMessageLink.message_id == RuntimeMessage.id)
            .where(
                RuntimeImageMessageLink.user_id == user_id,
                RuntimeImageMessageLink.image_asset_id == asset.id,
                RuntimeMessage.user_id == user_id,
            )
            .order_by(RuntimeImageMessageLink.created_at.desc())
            .limit(1)
        ).first()
        source_message_id: int | None = None
        source_thread_id: int | None = None
        attachment_id: str | None = None
        attachment_url: str | None = None
        if source is not None:
            link, message = source
            source_message_id = message.id
            source_thread_id = message.thread_id
            attachment_id = link.attachment_id
            if attachment_id:
                attachment_url = f"/api/chat/messages/{message.id}/attachments/{attachment_id}"

        label = asset.filename or f"image-{asset.id}"
        return ProactiveImageCandidate(
            image_asset_id=asset.id,
            filename=asset.filename,
            source_message_id=source_message_id,
            source_thread_id=source_thread_id,
            attachment_id=attachment_id,
            attachment_url=attachment_url,
            snippet=_compact_notice_snippet(annotation.content_text),
            pills=[
                {
                    "kind": "image_source",
                    "label": label[:_MAX_GREETING_PILL_LABEL_CHARS],
                    "ref": f"image:{asset.id}",
                }
            ],
        )
    return None


def mark_proactive_image_prompted(
    runtime_db: Session,
    *,
    user_id: int,
    image_asset_id: int,
    now: datetime | None = None,
) -> None:
    from anima_server.models.runtime import RuntimeImageAsset

    asset = runtime_db.scalar(
        select(RuntimeImageAsset).where(
            RuntimeImageAsset.id == image_asset_id,
            RuntimeImageAsset.user_id == user_id,
        )
    )
    if asset is None:
        return
    metadata = dict(asset.metadata_json) if isinstance(asset.metadata_json, dict) else {}
    metadata["proactivePromptedAt"] = (now or datetime.now(UTC)).isoformat()
    seal_runtime_fields(
        runtime_db,
        row=asset,
        row_type="runtime_image_asset",
        owner_id=user_id,
        payload={
            "filename": asset.filename,
            "mime_type": asset.mime_type,
            "storage_path": asset.storage_path,
            "metadata_json": metadata,
        },
        placeholders={
            "filename": None,
            "mime_type": "",
            "storage_path": "",
            "metadata_json": None,
        },
    )


def _compact_notice_snippet(text: str, *, limit: int = 120) -> str:
    snippet = " ".join(text.split())
    if len(snippet) <= limit:
        return snippet
    return snippet[: limit - 1].rstrip() + "..."


def _build_image_proactive_message(candidate: ProactiveImageCandidate) -> str:
    label = candidate.filename or f"image {candidate.image_asset_id}"
    return f"You shared {label}. Want to look at it together?"


def build_agent_state(
    db: Session,
    *,
    user_id: int,
    runtime_db: Session | None = None,
) -> AgentStateResult:
    """Build a compact, backend-grounded state line for ambient UI."""
    ctx = gather_greeting_context(db, user_id=user_id, runtime_db=runtime_db)
    dominant_emotion = _dominant_emotion_from_summary(ctx.emotional_summary)

    thought_source = "fallback"
    thought = "listening"
    for source, summary in (
        ("working_memory", ctx.working_memory_summary),
        ("inner_state", ctx.inner_state_summary),
        ("recent_episode", ctx.recent_episode_summary),
    ):
        compact = _compact_state_line(summary)
        if compact:
            thought = compact
            thought_source = source
            break
    else:
        if dominant_emotion:
            thought = _EMOTION_STATE_LINES.get(dominant_emotion, f"feeling {dominant_emotion}")
            thought_source = "emotion"

    affect_hint = _render_affect_hint(runtime_db, user_id=user_id)

    return AgentStateResult(
        user_id=user_id,
        dominant_emotion=dominant_emotion,
        thought=thought,
        thought_source=thought_source,
        chat_prompt="What's behind that thought?",
        context_messages=[
            {
                "role": "assistant",
                "content": _state_context_message(
                    thought=thought,
                    dominant_emotion=dominant_emotion,
                    affect_hint=affect_hint,
                ),
                "source": "agent_state",
            },
        ],
        affect_hint=affect_hint,
    )


def _render_affect_hint(runtime_db: Session | None, *, user_id: int) -> str | None:
    """Relax the stored IL1 affect vector to now and render it as adjectives.

    Best-effort: this is ambient flavor for the UI, never a hard dependency.
    """
    try:
        from anima_server.services.agent.inner_life.affect import relax, render_affect
        from anima_server.services.agent.inner_life.store import (
            get_affect_config,
            get_affect_state,
        )

        config = get_affect_config()
        stored = get_affect_state(runtime_db, user_id=user_id, config=config)
        current = relax(stored, datetime.now(UTC), config)
        return render_affect(current, previous=stored)
    except Exception:
        logger.debug("Affect hint unavailable for user %s", user_id, exc_info=True)
        return None


async def generate_greeting(
    db: Session,
    *,
    user_id: int,
    runtime_db: Session | None = None,
) -> GreetingResult:
    """Generate a personalized greeting, falling back to static if LLM unavailable."""
    from anima_server.services.agent.prompt_loader import get_prompt_loader

    prompt_loader = get_prompt_loader(db, user_id)

    ctx = gather_greeting_context(db, user_id=user_id, runtime_db=runtime_db)
    # IL-010: claim the ambient dream HERE, in the one path that guarantees
    # voicing (every return below renders it — static paths via
    # build_static_greeting, the LLM path via the deterministic append).
    # Never in gather_greeting_context: that gatherer is shared with
    # non-greeting paths that would consume dreams invisibly (PR #130).
    dream_claim = _resolve_ambient_dream(db, user_id=user_id)
    if dream_claim:
        ctx = dataclasses.replace(ctx, ambient_dream=dream_claim.narrative)
        _reset_dream_residue_after_surfacing(runtime_db, user_id=user_id)

    if settings.agent_provider == "scaffold":
        base = build_static_greeting(dataclasses.replace(ctx, ambient_dream=None))
        message, ctx, handoff = _finalize_ambient_dream(
            db, ctx, dream_claim, user_id=user_id, message=base
        )
        return GreetingResult(message=message, context=ctx, handoff_message=handoff)

    # Build the LLM prompt with available context
    identity_context = ""
    if ctx.identity_summary:
        identity_context = f"Your self-understanding:\n{ctx.identity_summary}"
    else:
        identity_context = "You're still getting to know this person."

    emotional_context = ""
    if ctx.emotional_summary:
        emotional_context = f"Last emotional read:\n{ctx.emotional_summary}"
    affect_hint = _render_affect_hint(runtime_db, user_id=user_id)
    if affect_hint:
        emotional_context = (
            f"{emotional_context}\nYour own current mood: {affect_hint}."
        ).strip()

    time_context = ""
    if ctx.days_since_last_chat is not None:
        if ctx.days_since_last_chat == 0:
            time_context = "You chatted earlier today."
        elif ctx.days_since_last_chat == 1:
            time_context = "You last chatted yesterday."
        else:
            time_context = (
                f"It's been {ctx.days_since_last_chat} days since your last conversation."
            )
    else:
        time_context = "This is your first time meeting."

    task_context = ""
    task_parts: list[str] = []
    if ctx.overdue_task_count:
        s = "s" if ctx.overdue_task_count != 1 else ""
        task_parts.append(f"{ctx.overdue_task_count} overdue task{s}")
    if ctx.upcoming_deadlines:
        task_parts.append(
            f"Upcoming deadlines: {', '.join(ctx.upcoming_deadlines[:3])}")
    if ctx.open_task_count:
        task_parts.append(f"{ctx.open_task_count} open tasks total")
    if ctx.current_focus:
        task_parts.append(f"Current focus: {ctx.current_focus}")
    if task_parts:
        task_context = "Task context:\n" + \
            "\n".join(f"- {p}" for p in task_parts)

    memory_context_parts: list[str] = []
    if ctx.inner_state_summary:
        memory_context_parts.append(
            f"Your inner state:\n{ctx.inner_state_summary}")
    if ctx.working_memory_summary:
        memory_context_parts.append(
            f"Things you're holding in mind:\n{ctx.working_memory_summary}")
    if ctx.recent_episode_summary:
        memory_context_parts.append(
            f"Recent conversations:\n{ctx.recent_episode_summary}")
    memory_context = "\n\n".join(memory_context_parts)

    if ctx.is_birthday:
        time_context = (time_context + "\nToday is the user's birthday.").strip()
    elif ctx.days_until_birthday is not None and ctx.days_until_birthday <= 7:
        days = ctx.days_until_birthday
        time_context = (
            time_context
            + f"\nThe user's birthday is in {days} day{'s' if days != 1 else ''}."
        ).strip()

    if ctx.held_thought:
        # IL-011: grounded in a real open thread whose pressure accumulated
        # over the gap — the instruction pins the model to that material.
        time_context = (
            time_context
            + "\nWhile they were away, one open thread genuinely stayed with "
            + f'you: "{ctx.held_thought}". You may mention it briefly and '
            + "naturally, but only what is stated here — do not invent "
            + "details, outcomes, or feelings about it."
        ).strip()

    # NB (PR #130 review): the ambient dream is deliberately NOT put in the
    # LLM prompt. The claim already committed, so voicing must be guaranteed
    # deterministically — the sentence is appended to whatever message this
    # function returns (see the append below and the static fallback), never
    # entrusted to the model's discretion.

    # Use templated greeting prompt
    prompt = prompt_loader.greeting(
        identity_context=identity_context,
        emotional_context=emotional_context,
        time_context=time_context,
        task_context=task_context,
        memory_context=memory_context,
    )

    errors: list[str] = []
    try:
        from anima_server.services.agent.llm import create_llm
        from anima_server.services.agent.messages import HumanMessage, SystemMessage

        system_content = (
            f"You are {prompt_loader.agent_name}, generating a brief greeting. "
            "Respond with ONLY the greeting text."
        )
        if settings.agent_provider == "ollama":
            content = await asyncio.wait_for(
                _invoke_ollama_native_chat(
                    [
                        {"role": "system", "content": system_content},
                        {"role": "user", "content": prompt},
                    ],
                    timeout=_GREETING_LLM_TIMEOUT_SECONDS,
                    max_tokens=_GREETING_LLM_MAX_TOKENS,
                ),
                timeout=_GREETING_LLM_TIMEOUT_SECONDS,
            )
        else:
            llm = create_llm()
            response = await asyncio.wait_for(
                llm.ainvoke(
                    [
                        SystemMessage(content=system_content),
                        HumanMessage(content=prompt),
                    ]
                ),
                timeout=_GREETING_LLM_TIMEOUT_SECONDS,
            )
            content = getattr(response, "content", "")
        if isinstance(content, str) and content.strip():
            message = content.strip()
            # Pills are generated from the model's OWN greeting, BEFORE the
            # dream is appended (PR #130 review, P1): generate_thought_pills
            # makes a second LLM request, so appending first would ship the
            # decrypted dream narrative to a cloud provider — breaking both
            # the on-device promise in AiSettings and this feature's own
            # invariant that the dream never enters any LLM prompt.
            pills = await generate_thought_pills(
                prompt_loader, greeting_message=message, ctx=ctx
            )
            # Voicing is OUR responsibility, not the model's — and the
            # decision runs under the consent lock, appending the same
            # sentence the static greeting uses while keeping the pre-append
            # text as the LLM-safe handoff copy (PR #130 review).
            message, ctx, handoff_message = _finalize_ambient_dream(
                db, ctx, dream_claim, user_id=user_id, message=message
            )
            return GreetingResult(
                message=message,
                context=ctx,
                llm_generated=True,
                pills=pills,
                handoff_message=handoff_message,
            )
    except Exception as e:
        logger.debug("LLM greeting generation failed: %s", e)
        errors.append(str(e))

    # Fallback to static
    base = build_static_greeting(dataclasses.replace(ctx, ambient_dream=None))
    message, ctx, handoff = _finalize_ambient_dream(
        db, ctx, dream_claim, user_id=user_id, message=base
    )
    return GreetingResult(
        message=message,
        context=ctx,
        errors=errors,
        handoff_message=handoff,
    )


def _normalize_greeting_pills(raw: object) -> list[dict[str, str]]:
    """Coerce the LLM's tag output into validated pill dicts.

    Accepts either a bare JSON array or an object with a ``tags``/``pills``
    key. Drops anything malformed, out-of-vocabulary, or empty, and caps the
    count — so a misbehaving model degrades to fewer/no pills, never garbage.
    """
    if isinstance(raw, dict):
        raw = raw.get("tags") or raw.get("pills") or []
    if not isinstance(raw, list):
        return []

    pills: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind", "")).strip().lower()
        label = str(entry.get("label", "")).strip().upper()
        if kind not in _GREETING_PILL_KINDS or not label:
            continue
        label = label[:_MAX_GREETING_PILL_LABEL_CHARS]
        if label in seen:
            continue
        seen.add(label)
        pills.append({"kind": kind, "label": label})
        if len(pills) >= _MAX_GREETING_PILLS:
            break
    return pills


async def generate_thought_pills(
    prompt_loader: object,
    *,
    greeting_message: str,
    ctx: GreetingContext,
) -> list[dict[str, str]]:
    """Ask the LLM for a few short tags describing what the greeting is about.

    Best-effort and self-contained: any failure (provider down, timeout, bad
    JSON) returns an empty list so the greeting itself is never affected.
    """
    if settings.agent_provider == "scaffold":
        return []

    context_lines: list[str] = []
    if ctx.current_focus:
        context_lines.append(f"Current focus: {ctx.current_focus}")
    if ctx.emotional_summary:
        context_lines.append(f"Emotional read: {ctx.emotional_summary}")
    if ctx.recent_episode_summary:
        context_lines.append(f"Recent conversations: {ctx.recent_episode_summary}")
    if ctx.working_memory_summary:
        context_lines.append(f"Holding in mind: {ctx.working_memory_summary}")
    context_block = "\n".join(context_lines) if context_lines else "(no extra context)"

    prompt = prompt_loader.greeting_pills(  # type: ignore[attr-defined]
        greeting_message=greeting_message,
        context=context_block,
    )
    system_content = (
        "You label a greeting with 2-4 short tags. "
        "Respond with ONLY a JSON array, no prose."
    )

    try:
        from anima_server.services.agent.json_utils import parse_json_array, parse_json_object

        if settings.agent_provider == "ollama":
            content = await asyncio.wait_for(
                _invoke_ollama_native_chat(
                    [
                        {"role": "system", "content": system_content},
                        {"role": "user", "content": prompt},
                    ],
                    timeout=_GREETING_PILLS_LLM_TIMEOUT_SECONDS,
                    max_tokens=_GREETING_PILLS_LLM_MAX_TOKENS,
                ),
                timeout=_GREETING_PILLS_LLM_TIMEOUT_SECONDS,
            )
        else:
            from anima_server.services.agent.llm import create_llm
            from anima_server.services.agent.messages import HumanMessage, SystemMessage

            llm = create_llm()
            response = await asyncio.wait_for(
                llm.ainvoke(
                    [
                        SystemMessage(content=system_content),
                        HumanMessage(content=prompt),
                    ]
                ),
                timeout=_GREETING_PILLS_LLM_TIMEOUT_SECONDS,
            )
            content = getattr(response, "content", "")

        if not isinstance(content, str) or not content.strip():
            return []
        parsed: object = parse_json_array(content)
        if not parsed:
            parsed = parse_json_object(content) or []
        return _normalize_greeting_pills(parsed)
    except Exception as e:
        logger.debug("LLM greeting pill generation failed: %s", e)
        return []


async def generate_proactive_notice(
    db: Session,
    *,
    user_id: int,
    instruction: str | None = None,
    runtime_db: Session | None = None,
) -> ProactiveNoticeResult | None:
    """Generate a quiet proactive notice for the main chat surface."""
    from anima_server.services.agent.prompt_loader import get_prompt_loader

    config = get_presence_config_values(db, user_id)
    if not config.enabled or not config.main_chat_enabled:
        return None

    effective_instruction = (instruction or "").strip() or config.custom_instruction
    ctx = gather_greeting_context(db, user_id=user_id, runtime_db=runtime_db)
    if (
        runtime_db is not None
        and config.memory_nudges_enabled
        and not effective_instruction
    ):
        image_candidate = select_proactive_image_candidate(runtime_db, user_id=user_id)
        if image_candidate is not None:
            mark_proactive_image_prompted(
                runtime_db,
                user_id=user_id,
                image_asset_id=image_candidate.image_asset_id,
            )
            return ProactiveNoticeResult(
                id=f"proactive_image_{image_candidate.image_asset_id}",
                message=_build_image_proactive_message(image_candidate),
                context=ctx,
                source="proactive_image",
                pills=image_candidate.pills,
            )

    fallback = build_static_proactive_notice(
        ctx,
        instruction=effective_instruction,
        config=config,
    )
    if fallback is None:
        return None

    if settings.agent_provider == "scaffold":
        return ProactiveNoticeResult(id="proactive_notice", message=fallback, context=ctx)

    prompt_loader = get_prompt_loader(db, user_id)
    prompt_parts = [
        "Write one short, quiet proactive notice for the main chat.",
        "It should feel like an optional thread, not a command or interruption.",
        "Do not mention that you are generating a notification.",
    ]
    if ctx.is_birthday:
        prompt_parts.append("Today is the user's birthday.")
    elif ctx.days_until_birthday is not None and ctx.days_until_birthday <= 7:
        days = ctx.days_until_birthday
        prompt_parts.append(
            f"The user's birthday is in {days} day{'s' if days != 1 else ''}."
        )
    if effective_instruction:
        prompt_parts.append(f"User customization: {effective_instruction}")
    if config.task_nudges_enabled and ctx.overdue_task_count:
        prompt_parts.append(f"Overdue tasks: {ctx.overdue_task_count}")
    if config.task_nudges_enabled and ctx.upcoming_deadlines:
        prompt_parts.append(f"Upcoming deadlines: {', '.join(ctx.upcoming_deadlines[:3])}")
    if config.checkin_nudges_enabled and ctx.days_since_last_chat is not None:
        prompt_parts.append(f"Days since last chat: {ctx.days_since_last_chat}")
    if config.memory_nudges_enabled and ctx.recent_episode_summary:
        prompt_parts.append(f"Recent conversation: {ctx.recent_episode_summary}")
    if config.memory_nudges_enabled and ctx.working_memory_summary:
        prompt_parts.append(f"Working memory: {ctx.working_memory_summary}")

    errors: list[str] = []
    try:
        from anima_server.services.agent.llm import create_llm
        from anima_server.services.agent.messages import HumanMessage, SystemMessage

        system_content = (
            f"You are {prompt_loader.agent_name}. Respond with ONLY the proactive "
            "notice text, one sentence."
        )
        prompt = "\n".join(prompt_parts)
        if settings.agent_provider == "ollama":
            content = await asyncio.wait_for(
                _invoke_ollama_native_chat(
                    [
                        {"role": "system", "content": system_content},
                        {"role": "user", "content": prompt},
                    ],
                    timeout=_PROACTIVE_NOTICE_LLM_TIMEOUT_SECONDS,
                    max_tokens=_PROACTIVE_NOTICE_LLM_MAX_TOKENS,
                ),
                timeout=_PROACTIVE_NOTICE_LLM_TIMEOUT_SECONDS,
            )
        else:
            llm = create_llm()
            response = await asyncio.wait_for(
                llm.ainvoke(
                    [
                        SystemMessage(content=system_content),
                        HumanMessage(content=prompt),
                    ]
                ),
                timeout=_PROACTIVE_NOTICE_LLM_TIMEOUT_SECONDS,
            )
            content = getattr(response, "content", "")
        if isinstance(content, str) and content.strip():
            return ProactiveNoticeResult(
                id="proactive_notice",
                message=content.strip(),
                context=ctx,
                llm_generated=True,
            )
    except Exception as e:
        logger.debug("LLM proactive notice generation failed: %s", e)
        errors.append(str(e))

    return ProactiveNoticeResult(
        id="proactive_notice",
        message=fallback,
        context=ctx,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Reflection question
# ---------------------------------------------------------------------------

_REFLECTION_LLM_TIMEOUT_SECONDS = 8.0
_REFLECTION_LLM_MAX_TOKENS = 80

@dataclass(frozen=True)
class ReflectionResult:
    question: str | None = None
    llm_generated: bool = False
    curiosity_type: str = "question"  # "question" | "memory"
    source_episode_id: int | None = None
    source_episode_date: str | None = None
    errors: list[str] = field(default_factory=list)


def _find_curiosity_anchor(db: Session, user_id: int) -> MemoryEpisode | None:
    """Find a past episode to anchor a memory-curiosity reflection.

    Priority:
    1. Episodes within ±45 days of this date last year (anniversary window).
    2. High-significance episodes (score >= 4) from more than 3 months ago.
    """
    today = date.today()
    try:
        anniversary = date(today.year - 1, today.month, today.day)
    except ValueError:
        anniversary = date(today.year - 1, today.month, today.day - 1)

    window_start = (anniversary - timedelta(days=45)).isoformat()
    window_end = (anniversary + timedelta(days=45)).isoformat()

    anniversary_ep = db.scalar(
        select(MemoryEpisode)
        .where(
            MemoryEpisode.user_id == user_id,
            MemoryEpisode.date >= window_start,
            MemoryEpisode.date <= window_end,
        )
        .order_by(MemoryEpisode.significance_score.desc(), MemoryEpisode.date.desc())
    )
    if anniversary_ep:
        return anniversary_ep

    cutoff = (today - timedelta(days=90)).isoformat()
    return db.scalar(
        select(MemoryEpisode)
        .where(
            MemoryEpisode.user_id == user_id,
            MemoryEpisode.date < cutoff,
            MemoryEpisode.significance_score >= 4,
        )
        .order_by(MemoryEpisode.significance_score.desc(), MemoryEpisode.date.desc())
    )


async def _invoke_reflection_llm(system_content: str, prompt: str) -> str | None:
    """Invoke the LLM for a short reflection-type prompt, return stripped text or None."""
    from anima_server.services.agent.llm import create_llm
    from anima_server.services.agent.messages import HumanMessage, SystemMessage

    if settings.agent_provider == "ollama":
        content = await asyncio.wait_for(
            _invoke_ollama_native_chat(
                [
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": prompt},
                ],
                timeout=_REFLECTION_LLM_TIMEOUT_SECONDS,
                max_tokens=_REFLECTION_LLM_MAX_TOKENS,
            ),
            timeout=_REFLECTION_LLM_TIMEOUT_SECONDS,
        )
    else:
        llm = create_llm()
        response = await asyncio.wait_for(
            llm.ainvoke(
                [
                    SystemMessage(content=system_content),
                    HumanMessage(content=prompt),
                ]
            ),
            timeout=_REFLECTION_LLM_TIMEOUT_SECONDS,
        )
        content = getattr(response, "content", "")
    return content.strip() if isinstance(content, str) and content.strip() else None


async def generate_reflection(
    db: Session,
    *,
    user_id: int,
    runtime_db: Session | None = None,
) -> ReflectionResult:
    """Generate a personalised reflection or memory-curiosity. Returns empty result if LLM unavailable."""
    from anima_server.services.agent.prompt_loader import get_prompt_loader

    prompt_loader = get_prompt_loader(db, user_id)
    ctx = gather_greeting_context(db, user_id=user_id, runtime_db=runtime_db)

    if settings.agent_provider == "scaffold":
        return ReflectionResult()

    identity_context = ctx.identity_summary or "You're still getting to know this person."

    # --- Attempt memory-curiosity anchor first ---
    anchor = _find_curiosity_anchor(db, user_id)
    if anchor:
        anchor_summary = df(
            user_id, anchor.summary, table="memory_episodes", field="summary"
        )
        try:
            prompt = prompt_loader.memory_curiosity(
                identity_context=identity_context,
                episode_summary=anchor_summary or "",
                episode_date=anchor.date,
            )
            system_content = (
                f"You are {prompt_loader.agent_name}, expressing genuine curiosity "
                "about a specific memory. Respond with ONLY the statement (1-2 sentences)."
            )
            content = await _invoke_reflection_llm(system_content, prompt)
            if content:
                return ReflectionResult(
                    question=content,
                    llm_generated=True,
                    curiosity_type="memory",
                    source_episode_id=anchor.id,
                    source_episode_date=anchor.date,
                )
        except Exception as e:
            logger.debug("LLM memory curiosity generation failed: %s", e)

    # --- Generic reflection question ---
    emotional_context = (
        f"Last emotional read:\n{ctx.emotional_summary}" if ctx.emotional_summary else ""
    )
    memory_context_parts: list[str] = []
    if ctx.inner_state_summary:
        memory_context_parts.append(f"Your inner state:\n{ctx.inner_state_summary}")
    if ctx.recent_episode_summary:
        memory_context_parts.append(f"Recent conversations:\n{ctx.recent_episode_summary}")
    memory_context = "\n\n".join(memory_context_parts)

    prompt = prompt_loader.reflection(
        identity_context=identity_context,
        emotional_context=emotional_context,
        memory_context=memory_context,
    )
    system_content = (
        f"You are {prompt_loader.agent_name}, generating a single reflection question. "
        "Respond with ONLY the question text."
    )

    errors: list[str] = []
    try:
        content = await _invoke_reflection_llm(system_content, prompt)
        if content:
            return ReflectionResult(question=content, llm_generated=True)
    except Exception as e:
        logger.debug("LLM reflection generation failed: %s", e)
        errors.append(str(e))

    return ReflectionResult(errors=errors)
