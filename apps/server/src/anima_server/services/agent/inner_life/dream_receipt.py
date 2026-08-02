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
- Because a claim can expire and be RE-taken by a different greeting, a
  client about to voice a dream first CONFIRMS its claim generation
  (``confirm_claim``) rather than trusting its own clock. Confirmation is
  the atomic form of "is this still my dream to speak?", and both it and
  the acknowledgement are scoped to the claim token the client holds.

The asymmetry is deliberate: a lost acknowledgement costs one repeat after
the TTL, while a lost claim-expiry would cost permanent silence. Repeating
once, minutes later, after the user demonstrably never saw it, is the
better failure — and unlike IL-010's version it is no longer silent.

Mirrors IL-003's ``PendingInitiative`` deliver/ack shape, which solved the
same problem for initiatives.
"""

from __future__ import annotations

from dataclasses import dataclass
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


def claim_token(claimed_at: datetime) -> str:
    """Opaque handle naming ONE claim generation on a dream row.

    A dream can be claimed, expire, and be claimed again by a different
    greeting, so "dream 42" does not identify whose turn it is to voice it
    (PR #135 review, P1). ``claimed_at`` does — every claim writes a fresh
    instant — so the timestamp itself is the generation marker. Normalised
    to UTC before serialising so the string a client returns compares equal
    to the stored value.
    """
    reference = (
        claimed_at if claimed_at.tzinfo is not None else claimed_at.replace(tzinfo=UTC)
    )
    return reference.astimezone(UTC).isoformat()


def _parse_claim_token(token: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(token)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True)
class ConfirmedClaim:
    """A claim re-asserted for the caller, with its refreshed deadline."""

    token: str
    expires_at: datetime


def confirm_claim(
    db: Session, *, user_id: int, dream_id: int, token: str, now: datetime | None = None
) -> ConfirmedClaim | None:
    """Re-assert a claim at the moment the client is about to VOICE it.

    The client's own expiry check is a check-then-act against a clock the
    server does not control: a skewed device, or a render delayed past the
    deadline, can decide "still mine" after the server has already re-offered
    the dream elsewhere — and the same narrative gets disclosed twice (PR
    #135 review, P1). This is the atomic version of that question. It
    succeeds only while the row still carries THIS claim generation and has
    not been acknowledged, and it renews the claim in the same statement, so
    the caller's render is covered by a fresh TTL.

    Returns the renewed claim, or None when it is stale — the caller must
    then voice the dream-free copy instead. Renewing rather than surfacing
    keeps IL-015's guarantee intact: a client that dies between confirming
    and painting loses nothing, because the renewed claim simply expires and
    the dream is offered again.
    """
    claimed_at = _parse_claim_token(token)
    if claimed_at is None:
        return None
    renewed_at = now or datetime.now(UTC)
    result = db.execute(
        update(DreamJournal)
        .where(
            DreamJournal.id == dream_id,
            DreamJournal.user_id == user_id,
            DreamJournal.surfaced.is_(False),
            DreamJournal.claimed_at == claimed_at,
        )
        .values(claimed_at=renewed_at)
    )
    if result.rowcount != 1:
        return None
    return ConfirmedClaim(
        token=claim_token(renewed_at), expires_at=claim_expires_at(renewed_at)
    )


def acknowledge_dream(
    db: Session, *, user_id: int, dream_id: int, token: str
) -> bool:
    """Record that the client actually received and rendered the dream.

    Idempotent, ownership-checked and CLAIM-scoped: acknowledging twice,
    acknowledging another user's dream, or acknowledging with a superseded
    claim token is a no-op returning False. The token matters (PR #135
    review): without it a stale client could mark a dream surfaced and clear
    a NEWER greeting's claim, hijacking a disclosure in flight. Sets
    ``surfaced`` (the durable marker every other consumer reads) and clears
    ``claimed_at`` so no expiry logic ever revisits the row.
    """
    claimed_at = _parse_claim_token(token)
    if claimed_at is None:
        return False
    result = db.execute(
        update(DreamJournal)
        .where(
            DreamJournal.id == dream_id,
            DreamJournal.user_id == user_id,
            DreamJournal.surfaced.is_(False),
            DreamJournal.claimed_at == claimed_at,
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
