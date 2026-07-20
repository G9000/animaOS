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


def list_and_mark_delivered(
    runtime_db: Session, *, user_id: int
) -> list[PendingInitiative]:
    """Every not-yet-acknowledged pending initiative for ``user_id``,
    oldest first — and marks each ``delivered`` (the client has now been
    handed the row) as a side effect."""
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

            log_row = soul_db.get(InitiativeLog, row.initiative_log_id)
            if log_row is not None and log_row.user_id == user_id:
                log_row.answered = True
                soul_db.flush()
        except Exception:
            logger.warning(
                "Failed to mark InitiativeLog answered for pending initiative %s",
                pending_id,
                exc_info=True,
            )

    return row
