"""IL-015 — claim/acknowledge protocol for ambient dream surfacing.

IL-010 marked a dream ``surfaced`` the moment a greeting claimed it, before
the response reached the browser. A reload, tab close, or dropped
connection in that window consumed the dream without ever voicing it (PR
#130 review). IL-010 accepted that deliberately, because the alternative —
re-offering a dream that WAS displayed — repeats intimate content at the
user. This module removes the trade instead of choosing a side:

- ``claimed_at`` records that a greeting took the dream. A claim suppresses
  re-offering, so a dream is never voiced twice concurrently.
- ``surfaced`` is set only by an explicit client acknowledgement — proof the
  greeting actually reached the user. That flag stays the durable "never say
  this again" marker every other consumer already reads.
- An unacknowledged claim EXPIRES after ``dream_claim_ttl_minutes``. Only
  then does the dream become offerable again, so the re-offer window is
  bounded and only opens for greetings that demonstrably never landed.

The asymmetry is deliberate: a lost acknowledgement costs one repeat after
the TTL, while a lost claim-expiry would cost permanent silence. Repeating
once, minutes later, after the user demonstrably never saw it, is the
better failure — and unlike IL-010's version it is no longer silent.

Mirrors IL-003's ``PendingInitiative`` deliver/ack shape, which solved the
same problem for initiatives.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models import DreamJournal


def claim_cutoff(now: datetime | None = None) -> datetime:
    """Claims older than this are stale and may be re-offered."""
    reference = now or datetime.now(UTC)
    return reference - timedelta(minutes=settings.dream_claim_ttl_minutes)


def claim_expires_at(claimed_at: datetime) -> datetime:
    """When a claim taken at ``claimed_at`` goes stale and may be re-offered.

    Handed to the client with the greeting (PR #135 review, P1): a
    dream-bearing response the browser stores for later — the Dashboard's
    one-shot handoff — must not outlive the claim behind it. Past this
    instant the server may offer the same narrative through an initiative
    or a fresh greeting, so replaying the stored copy would disclose it
    twice. The client cannot compute the deadline itself; the TTL is server
    configuration, so the server states it.
    """
    reference = (
        claimed_at if claimed_at.tzinfo is not None else claimed_at.replace(tzinfo=UTC)
    )
    return reference + timedelta(minutes=settings.dream_claim_ttl_minutes)


def offerable_dream_query(user_id: int, *, now: datetime | None = None):
    """Dreams this user may be offered: share-worthy, never acknowledged,
    and not held by a LIVE claim. Soonest-first ordering is applied by the
    caller so this stays composable as a scalar subquery."""
    cutoff = claim_cutoff(now)
    return select(DreamJournal).where(
        DreamJournal.user_id == user_id,
        DreamJournal.share_worthy.is_(True),
        DreamJournal.surfaced.is_(False),
        (DreamJournal.claimed_at.is_(None)) | (DreamJournal.claimed_at < cutoff),
    )


def acknowledge_dream(
    db: Session, *, user_id: int, dream_id: int, now: datetime | None = None
) -> bool:
    """Record that the client actually received and rendered the dream.

    Idempotent and ownership-checked: acknowledging twice, or acknowledging
    another user's dream, is a no-op returning False. Sets ``surfaced`` (the
    durable marker every other consumer reads) and clears ``claimed_at`` so
    no expiry logic ever revisits the row.
    """
    del now  # accepted for symmetry with the rest of the module
    result = db.execute(
        update(DreamJournal)
        .where(
            DreamJournal.id == dream_id,
            DreamJournal.user_id == user_id,
            DreamJournal.surfaced.is_(False),
        )
        .values(surfaced=True, claimed_at=None)
    )
    return result.rowcount == 1


def release_claim(db: Session, *, dream_id: int) -> None:
    """Drop a claim the server knows was never voiced (consent withdrawn
    mid-generation). Distinct from expiry: this is immediate and certain."""
    db.execute(
        update(DreamJournal)
        .where(DreamJournal.id == dream_id)
        .values(claimed_at=None)
    )
