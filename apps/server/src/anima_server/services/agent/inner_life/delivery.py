"""IL3 — Outbound delivery adapter seam for fired initiatives.

PRD IL3 names the eventual channel as "OS notification via the Tauri shell,
or adapter-specific push." The Tauri desktop shell's notification bridge
does not exist in this repository yet (`apps/desktop` has no such layer), so
this module ships exactly what IL-003 needs to be usable today: an abstract
seam (``InitiativeDelivery``) plus the one implementation that requires no
platform bridge at all — persisting a ``PendingInitiative`` runtime row a
client can poll and acknowledge. ``OSNotificationDelivery`` is a documented
stub for the Tauri shell to implement later; it deliberately raises rather
than silently no-opping, so wiring it up without finishing the bridge fails
loudly instead of pretending to deliver.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models.runtime_consciousness import PendingInitiative

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    delivered: bool
    pending_initiative_id: int | None = None


class InitiativeDelivery(ABC):
    """Abstract outbound channel for a fired IL3 initiative."""

    @abstractmethod
    def deliver(
        self,
        runtime_db: Session,
        *,
        user_id: int,
        drive: str,
        text: str,
        initiative_log_id: int,
    ) -> DeliveryResult:
        """Hand ``text`` off to this channel. Must not raise on ordinary
        "channel unavailable" conditions — return ``DeliveryResult(delivered=False)``
        instead, so the caller can log a truthful gate-states/delivered
        record. Raising is reserved for adapters that are simply not wired
        up yet (see ``OSNotificationDelivery``)."""


class PendingInitiativeDelivery(InitiativeDelivery):
    """Default delivery: persist a pollable ``PendingInitiative`` row.

    This is the only delivery adapter that ships enabled in this repo — it
    requires no client-side bridge, just the fetch/ack API route
    (``api/routes/presence.py``).
    """

    def deliver(
        self,
        runtime_db: Session,
        *,
        user_id: int,
        drive: str,
        text: str,
        initiative_log_id: int,
    ) -> DeliveryResult:
        row = PendingInitiative(
            user_id=user_id,
            initiative_log_id=initiative_log_id,
            drive=drive,
            text=text,
            delivered=False,
            acknowledged=False,
        )
        runtime_db.add(row)
        runtime_db.flush()
        return DeliveryResult(delivered=True, pending_initiative_id=row.id)


class OSNotificationDelivery(InitiativeDelivery):
    """Documented stub for the Tauri desktop shell's OS-notification bridge.

    Not implemented in this repository: `apps/desktop` does not yet expose a
    notification API for the server to call. Wire this up once that bridge
    exists; until then, selecting this adapter fails loudly rather than
    silently dropping the message.
    """

    def deliver(
        self,
        runtime_db: Session,
        *,
        user_id: int,
        drive: str,
        text: str,
        initiative_log_id: int,
    ) -> DeliveryResult:
        raise NotImplementedError(
            "OSNotificationDelivery requires the Tauri desktop shell's "
            "notification bridge, which does not exist in this repository "
            "yet. IL-003 ships PendingInitiativeDelivery (the pollable "
            "default) only."
        )


def _reconcile_soul_delivered(
    soul_db: Session | None, *, user_id: int, log_ids: list[int]
) -> None:
    """Best-effort mark the given ``InitiativeLog`` rows ``delivered`` — the
    durable runtime side (a fetched/acked ``PendingInitiative``) is proof the
    user received the message, so reconcile the soul log if the two-phase
    ``delivered=True`` commit in ``tick_initiative_for_user`` had failed after
    the runtime commit. Without this, ``count_recent_fires`` (which filters
    ``delivered``) would keep undercounting and let a close user get another
    initiative inside the cap.

    Savepoint-isolated: a transient locked/corrupt per-user soul DB must not
    poison the caller's session (the API route commits it afterwards), so a
    failure here rolls back only the nested block and is swallowed.
    """
    if soul_db is None or not log_ids:
        return
    try:
        from anima_server.models import InitiativeLog

        with soul_db.begin_nested():
            for log_id in log_ids:
                log_row = soul_db.get(InitiativeLog, log_id)
                if log_row is not None and log_row.user_id == user_id:
                    log_row.delivered = True
            soul_db.flush()
    except Exception:
        logger.warning(
            "Failed to reconcile InitiativeLog.delivered for user %s", user_id, exc_info=True
        )


def list_and_mark_delivered(
    runtime_db: Session,
    *,
    user_id: int,
    soul_db: Session | None = None,
    now: datetime | None = None,
) -> list[PendingInitiative]:
    """Every not-yet-acknowledged pending initiative for ``user_id``,
    oldest first — and marks each ``delivered`` (the client has now been
    handed the row) as a side effect. When ``soul_db`` is supplied, also
    best-effort reconciles the soul-store ``InitiativeLog.delivered`` flag for
    the fetched rows — the poll is the first proof of delivery, so it must not
    rely on a later ack to make ``count_recent_fires`` count the message.

    ``soul_db`` is also the consent authority (PR #123 review, P1): the
    user's presence config is checked FIRST, and without an active opt-in
    (``enabled`` AND ``initiative_enabled``) nothing is listed and nothing is
    marked delivered. Doing the check inside the same operation as the
    delivery side effect closes the client-side race where consent is
    withdrawn between a client's own config check and its list call.
    ``soul_db=None`` callers (tests) skip the check; the API route always
    passes the soul session.

    Quiet hours are re-evaluated here too (PR #123 review, P1): firing checks
    them once, but an initiative fired just before the window would otherwise
    be served inside it, breaking the Presence UI promise ("no messages
    inside this window"). A row listed during quiet hours stays undelivered
    and is served after the window ends. ``now`` follows the same local-time
    discipline as the gate chain (``resolve_local_now``); the route omits it,
    resolving the real system-zone wall clock."""
    if soul_db is not None:
        from anima_server.services.agent.inner_life.initiative import _in_quiet_hours
        from anima_server.services.agent.inner_life.presence import resolve_local_now
        from anima_server.services.presence_config import get_presence_config_values

        values = get_presence_config_values(soul_db, user_id)
        if not (values.enabled and values.initiative_enabled):
            return []
        local_now = resolve_local_now(now, None)
        if _in_quiet_hours(
            local_now.hour, values.quiet_hours_start, values.quiet_hours_end
        ):
            return []
    rows = list(
        runtime_db.scalars(
            select(PendingInitiative)
            .where(
                PendingInitiative.user_id == user_id,
                PendingInitiative.acknowledged.is_(False),
            )
            .order_by(PendingInitiative.created_at.asc())
        ).all()
    )
    for row in rows:
        row.delivered = True
    if rows:
        runtime_db.flush()
        _reconcile_soul_delivered(
            soul_db, user_id=user_id, log_ids=[row.initiative_log_id for row in rows]
        )
    return rows


def acknowledge_pending_initiative(
    runtime_db: Session,
    *,
    soul_db: Session | None,
    user_id: int,
    pending_id: int,
) -> PendingInitiative | None:
    """Mark one pending initiative acknowledged, and best-effort mark the
    corresponding soul-store ``InitiativeLog`` row ``answered`` (feeds the
    gate chain's unanswered-initiative cooldown backoff). Returns ``None``
    if no matching row exists for this user."""
    row = runtime_db.scalar(
        select(PendingInitiative).where(
            PendingInitiative.id == pending_id,
            PendingInitiative.user_id == user_id,
        )
    )
    if row is None:
        return None

    row.acknowledged = True
    row.acknowledged_at = datetime.now(UTC)
    row.delivered = True
    runtime_db.flush()

    if soul_db is not None:
        try:
            from anima_server.models import InitiativeLog

            # Savepoint-isolated: if the soul flush raises (e.g. a transient
            # locked/corrupt per-user soul DB), roll back only this nested block
            # so the session stays usable — otherwise the API route's
            # subsequent db.commit() would raise and the runtime ack would roll
            # back too, defeating the best-effort intent.
            with soul_db.begin_nested():
                log_row = soul_db.get(InitiativeLog, row.initiative_log_id)
                if log_row is not None and log_row.user_id == user_id:
                    # An acknowledgement is definitive proof the user received
                    # the message, so reconcile `delivered` too — not just
                    # `answered`. If the two-phase `delivered=True` commit in
                    # tick_initiative_for_user failed after the runtime
                    # PendingInitiative was already durable, the log would
                    # otherwise stay delivered=False and count_recent_fires
                    # (which filters delivered) would undercount, letting a
                    # close user get another initiative inside the 24h cap.
                    log_row.delivered = True
                    log_row.answered = True
                    soul_db.flush()
        except Exception:
            logger.warning(
                "Failed to mark InitiativeLog answered for pending initiative %s",
                pending_id,
                exc_info=True,
            )

    return row
