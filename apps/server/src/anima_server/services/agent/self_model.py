"""Self-model management across soul and runtime stores."""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterator

from sqlalchemy import inspect as sa_inspect, select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models import SelfModelBlock
from anima_server.models.runtime_consciousness import ActiveIntention, WorkingContext
from anima_server.models.soul_consciousness import GrowthLogEntry, IdentityBlock
from anima_server.services.data_crypto import df, ef

logger = logging.getLogger(__name__)

SOUL_SECTIONS = ("soul", "persona", "human", "user_directive")
IDENTITY_SECTIONS = ("identity", "growth_log")
RUNTIME_SECTIONS = ("inner_state", "working_memory", "intentions")
ALL_SECTIONS = SOUL_SECTIONS + IDENTITY_SECTIONS + RUNTIME_SECTIONS

_SEED_IDENTITY = """# Who I Am
<!-- certainty: low -->
I'm still getting to know this person. My understanding will deepen over time.

# My Relationship With This User
<!-- certainty: low -->
This is a new relationship. I don't have enough context yet to characterize it.

# How I Communicate With Them
<!-- certainty: low -->
Using my default communication style until I learn their preferences.

# What I'm Uncertain About
<!-- certainty: low -->
Everything - we're just getting started."""

_SEED_INNER_STATE = """# Current Sense of the User
No strong signals yet - too early to form impressions.

# Active Threads
No ongoing threads yet.

# Things I'm Curious About
- What matters most to this person
- How they prefer to communicate
- What they need from me

# Recent Observations
None yet."""

_SEED_WORKING_MEMORY = """# Things I'm Holding in Mind
No items yet."""

_SEED_GROWTH_LOG = ""

_SEED_INTENTIONS = """# Active Intentions

## Ongoing
- **Learn this person's communication preferences**
  - Evidence: New relationship - no data yet
  - Status: Active - observing
  - Strategy: Pay attention to how they respond to different styles

# Behavioral Rules I've Learned
No rules yet - still observing."""

_BUDGET: dict[str, int] = {
    "identity": settings.agent_self_model_identity_budget,
    "inner_state": settings.agent_self_model_inner_state_budget,
    "working_memory": settings.agent_self_model_working_memory_budget,
    "growth_log": settings.agent_self_model_growth_log_budget,
    "intentions": settings.agent_self_model_intentions_budget,
}

_TRUSTED_WRITERS = frozenset({"user", "system", "api"})
_IDENTITY_STABILITY_THRESHOLD = 5


@dataclass(slots=True)
class LegacySelfModelBlockView:
    """Compatibility view for callers still expecting a SelfModelBlock shape."""

    user_id: int
    section: str
    content: str
    version: int
    updated_by: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    metadata_json: dict | None = None
    needs_regeneration: bool = False
    id: int | None = None
    _plaintext: str = ""


def get_identity_block(
    db: Session,
    *,
    user_id: int,
) -> IdentityBlock | None:
    """Get the identity block for a user from the soul store."""
    block = db.scalar(select(IdentityBlock).where(IdentityBlock.user_id == user_id))
    if block is not None:
        block.content = _maybe_decrypt_migrated(
            user_id, block.content, table="self_model_blocks", field="content"
        )
    return block


def set_identity_block(
    db: Session,
    *,
    user_id: int,
    content: str,
    updated_by: str = "system",
) -> IdentityBlock:
    """Create or update the identity block with stability governance."""
    existing = get_identity_block(db, user_id=user_id)

    if (
        existing is not None
        and existing.version < _IDENTITY_STABILITY_THRESHOLD
        and updated_by not in _TRUSTED_WRITERS
        and existing.content.strip()
    ):
        existing_words = set(existing.content.lower().split())
        new_words = set(content.lower().split())
        if existing_words and new_words and max(len(existing_words), len(new_words)) >= 3:
            overlap = len(existing_words & new_words) / max(len(existing_words), len(new_words))
            if overlap < 0.5:
                logger.info(
                    "Blocked identity rewrite by %s (version %d < %d, overlap %.2f).",
                    updated_by,
                    existing.version,
                    _IDENTITY_STABILITY_THRESHOLD,
                    overlap,
                )
                append_growth_log_entry_row(
                    db,
                    user_id=user_id,
                    entry=f"Identity update proposed by {updated_by} (blocked): {content[:200]}",
                )
                return existing

    if existing is not None:
        existing.content = content
        existing.version += 1
        existing.updated_by = updated_by
        existing.updated_at = datetime.now(UTC)
        db.flush()
        return existing

    block = IdentityBlock(
        user_id=user_id,
        content=content,
        version=1,
        updated_by=updated_by,
    )
    db.add(block)
    db.flush()
    return block


def get_growth_log_entries(
    db: Session,
    *,
    user_id: int,
    limit: int = 20,
) -> list[GrowthLogEntry]:
    """Get growth log entries, most recent first."""
    entries = list(
        db.scalars(
            select(GrowthLogEntry)
            .where(GrowthLogEntry.user_id == user_id)
            .order_by(GrowthLogEntry.created_at.desc(), GrowthLogEntry.id.desc())
            .limit(limit)
        ).all()
    )
    for entry in entries:
        entry.entry = _maybe_decrypt_migrated(
            user_id, entry.entry, table="self_model_blocks", field="content"
        )
    return entries


def get_growth_log_text(
    db: Session,
    *,
    user_id: int,
    limit: int = 20,
) -> str:
    """Render growth log entries as the legacy markdown block."""
    entries = get_growth_log_entries(db, user_id=user_id, limit=limit)
    if not entries:
        return ""

    lines: list[str] = []
    for entry in reversed(entries):
        date_str = entry.created_at.strftime("%Y-%m-%d") if entry.created_at else "unknown"
        lines.append(f"### {date_str} - {entry.entry}")
    return "\n\n".join(lines)


def append_growth_log_entry_row(
    db: Session,
    *,
    user_id: int,
    entry: str,
    source: str = "sleep_time",
    max_entries: int = 20,
) -> GrowthLogEntry | None:
    """Append a growth log row, deduplicating and trimming older entries."""
    cleaned = entry.strip()
    if not cleaned:
        return None

    for existing in get_growth_log_entries(db, user_id=user_id, limit=max_entries):
        if _is_duplicate_growth_entry_text(existing.entry, cleaned):
            return None

    row = GrowthLogEntry(user_id=user_id, entry=cleaned, source=source)
    db.add(row)
    db.flush()

    entries = list(
        db.scalars(
            select(GrowthLogEntry)
            .where(GrowthLogEntry.user_id == user_id)
            .order_by(GrowthLogEntry.created_at.asc(), GrowthLogEntry.id.asc())
        ).all()
    )
    if len(entries) > max_entries:
        for stale in entries[: len(entries) - max_entries]:
            db.delete(stale)
        db.flush()

    return row


def get_working_context(
    pg_db: Session,
    *,
    user_id: int,
) -> dict[str, WorkingContext | LegacySelfModelBlockView]:
    """Get all working-context rows for a user keyed by section."""
    if not _has_table(pg_db, "working_context"):
        rows = pg_db.scalars(
            select(SelfModelBlock).where(
                SelfModelBlock.user_id == user_id,
                SelfModelBlock.section.in_(("inner_state", "working_memory")),
            )
        ).all()
        return {
            row.section: _make_legacy_view(
                user_id=user_id,
                section=row.section,
                plaintext=df(user_id, row.content, table="self_model_blocks", field="content"),
                version=row.version,
                updated_by=row.updated_by,
                created_at=row.created_at,
                updated_at=row.updated_at,
                row_id=row.id,
            )
            for row in rows
        }

    rows = pg_db.scalars(
        select(WorkingContext).where(WorkingContext.user_id == user_id)
    ).all()
    return {row.section: row for row in rows}


def set_working_context(
    pg_db: Session,
    *,
    user_id: int,
    section: str,
    content: str,
    updated_by: str = "system",
) -> WorkingContext:
    """Create or update a working-context section in runtime storage."""
    if not _has_table(pg_db, "working_context"):
        existing = pg_db.scalar(
            select(SelfModelBlock).where(
                SelfModelBlock.user_id == user_id,
                SelfModelBlock.section == section,
            )
        )
        if existing is not None:
            existing.content = ef(user_id, content, table="self_model_blocks", field="content")
            existing.version += 1
            existing.updated_by = updated_by
            existing.updated_at = datetime.now(UTC)
            pg_db.flush()
            return _make_legacy_view(
                user_id=user_id,
                section=section,
                plaintext=content,
                version=existing.version,
                updated_by=existing.updated_by,
                created_at=existing.created_at,
                updated_at=existing.updated_at,
                row_id=existing.id,
            )

        legacy = SelfModelBlock(
            user_id=user_id,
            section=section,
            content=ef(user_id, content, table="self_model_blocks", field="content"),
            version=1,
            updated_by=updated_by,
        )
        pg_db.add(legacy)
        pg_db.flush()
        return _make_legacy_view(
            user_id=user_id,
            section=section,
            plaintext=content,
            version=legacy.version,
            updated_by=legacy.updated_by,
            created_at=legacy.created_at,
            updated_at=legacy.updated_at,
            row_id=legacy.id,
        )

    existing = pg_db.scalar(
        select(WorkingContext).where(
            WorkingContext.user_id == user_id,
            WorkingContext.section == section,
        )
    )

    if existing is not None:
        existing.content = content
        existing.version += 1
        existing.updated_by = updated_by
        existing.updated_at = datetime.now(UTC)
        pg_db.flush()
        return existing

    row = WorkingContext(
        user_id=user_id,
        section=section,
        content=content,
        version=1,
        updated_by=updated_by,
    )
    pg_db.add(row)
    pg_db.flush()
    return row


def get_active_intentions(
    pg_db: Session,
    *,
    user_id: int,
) -> ActiveIntention | LegacySelfModelBlockView | None:
    """Get the active-intentions block for a user."""
    if not _has_table(pg_db, "active_intentions"):
        row = pg_db.scalar(
            select(SelfModelBlock).where(
                SelfModelBlock.user_id == user_id,
                SelfModelBlock.section == "intentions",
            )
        )
        if row is None:
            return None
        return _make_legacy_view(
            user_id=user_id,
            section="intentions",
            plaintext=df(user_id, row.content, table="self_model_blocks", field="content"),
            version=row.version,
            updated_by=row.updated_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
            row_id=row.id,
        )

    return pg_db.scalar(select(ActiveIntention).where(ActiveIntention.user_id == user_id))


def set_active_intentions(
    pg_db: Session,
    *,
    user_id: int,
    content: str,
    updated_by: str = "system",
) -> ActiveIntention:
    """Create or update active intentions in runtime storage."""
    if not _has_table(pg_db, "active_intentions"):
        existing = pg_db.scalar(
            select(SelfModelBlock).where(
                SelfModelBlock.user_id == user_id,
                SelfModelBlock.section == "intentions",
            )
        )
        if existing is not None:
            existing.content = ef(user_id, content, table="self_model_blocks", field="content")
            existing.version += 1
            existing.updated_by = updated_by
            existing.updated_at = datetime.now(UTC)
            pg_db.flush()
            return _make_legacy_view(
                user_id=user_id,
                section="intentions",
                plaintext=content,
                version=existing.version,
                updated_by=existing.updated_by,
                created_at=existing.created_at,
                updated_at=existing.updated_at,
                row_id=existing.id,
            )

        legacy = SelfModelBlock(
            user_id=user_id,
            section="intentions",
            content=ef(user_id, content, table="self_model_blocks", field="content"),
            version=1,
            updated_by=updated_by,
        )
        pg_db.add(legacy)
        pg_db.flush()
        return _make_legacy_view(
            user_id=user_id,
            section="intentions",
            plaintext=content,
            version=legacy.version,
            updated_by=legacy.updated_by,
            created_at=legacy.created_at,
            updated_at=legacy.updated_at,
            row_id=legacy.id,
        )

    existing = get_active_intentions(pg_db, user_id=user_id)
    if existing is not None:
        existing.content = content
        existing.version += 1
        existing.updated_by = updated_by
        existing.updated_at = datetime.now(UTC)
        pg_db.flush()
        return existing

    row = ActiveIntention(
        user_id=user_id,
        content=content,
        version=1,
        updated_by=updated_by,
    )
    pg_db.add(row)
    pg_db.flush()
    return row


def get_self_model_block(
    db: Session,
    *,
    user_id: int,
    section: str,
) -> SelfModelBlock | LegacySelfModelBlockView | None:
    """Compatibility reader across soul and runtime stores."""
    if section not in ALL_SECTIONS:
        return None

    if section in SOUL_SECTIONS:
        return db.scalar(
            select(SelfModelBlock).where(
                SelfModelBlock.user_id == user_id,
                SelfModelBlock.section == section,
            )
        )

    if section == "identity":
        identity = get_identity_block(db, user_id=user_id)
        if identity is not None:
            return _make_legacy_view(
                user_id=user_id,
                section="identity",
                plaintext=identity.content,
                version=identity.version,
                updated_by=identity.updated_by,
                created_at=identity.created_at,
                updated_at=identity.updated_at,
                row_id=identity.id,
            )

    if section == "growth_log":
        entries = get_growth_log_entries(db, user_id=user_id)
        if entries:
            latest = entries[0]
            oldest = entries[-1]
            return _make_legacy_view(
                user_id=user_id,
                section="growth_log",
                plaintext=get_growth_log_text(db, user_id=user_id),
                version=len(entries) + 1,
                updated_by=latest.source,
                created_at=oldest.created_at,
                updated_at=latest.created_at,
                row_id=latest.id,
            )
        legacy_growth = db.scalar(
            select(SelfModelBlock).where(
                SelfModelBlock.user_id == user_id,
                SelfModelBlock.section == "growth_log",
            )
        )
        if legacy_growth is not None:
            return legacy_growth
        return _make_legacy_view(
            user_id=user_id,
            section="growth_log",
            plaintext=_SEED_GROWTH_LOG,
            version=1,
            updated_by="system",
        )

    if section in RUNTIME_SECTIONS:
        with _runtime_session() as pg_db:
            if pg_db is not None:
                if section == "intentions":
                    row = get_active_intentions(pg_db, user_id=user_id)
                else:
                    row = get_working_context(pg_db, user_id=user_id).get(section)
                if row is not None:
                    return _make_legacy_view(
                        user_id=user_id,
                        section=section,
                        plaintext=row.content,
                        version=row.version,
                        updated_by=row.updated_by,
                        created_at=getattr(row, "created_at", None),
                        updated_at=row.updated_at,
                        row_id=row.id,
                    )

    return db.scalar(
        select(SelfModelBlock).where(
            SelfModelBlock.user_id == user_id,
            SelfModelBlock.section == section,
        )
    )


def get_all_self_model_blocks(
    db: Session,
    *,
    user_id: int,
) -> dict[str, SelfModelBlock | LegacySelfModelBlockView]:
    """Get all known self-model sections for a user keyed by section name."""
    rows = db.scalars(select(SelfModelBlock).where(SelfModelBlock.user_id == user_id)).all()
    blocks: dict[str, SelfModelBlock | LegacySelfModelBlockView] = {row.section: row for row in rows}

    identity = get_self_model_block(db, user_id=user_id, section="identity")
    if identity is not None:
        blocks["identity"] = identity

    growth_log = get_self_model_block(db, user_id=user_id, section="growth_log")
    if growth_log is not None:
        blocks["growth_log"] = growth_log

    for section in ("inner_state", "working_memory", "intentions"):
        block = get_self_model_block(db, user_id=user_id, section=section)
        if block is not None:
            blocks[section] = block

    return blocks


def set_self_model_block(
    db: Session,
    *,
    user_id: int,
    section: str,
    content: str,
    updated_by: str = "system",
    metadata: dict | None = None,
) -> SelfModelBlock | LegacySelfModelBlockView:
    """Compatibility writer that routes moved sections to their new stores."""
    if section not in ALL_SECTIONS:
        raise ValueError(f"Invalid section: {section}")

    if section in SOUL_SECTIONS:
        from anima_server.services.agent.soul_writer import set_soul_block

        return set_soul_block(
            db,
            user_id=user_id,
            section=section,
            content=content,
            updated_by=updated_by,
            metadata=metadata,
        )

    if section == "identity":
        block = set_identity_block(db, user_id=user_id, content=content, updated_by=updated_by)
        return _make_legacy_view(
            user_id=user_id,
            section="identity",
            plaintext=block.content,
            version=block.version,
            updated_by=block.updated_by,
            created_at=block.created_at,
            updated_at=block.updated_at,
            row_id=block.id,
        )

    if section == "growth_log":
        _replace_growth_log_entries(db, user_id=user_id, content=content, source=updated_by)
        growth_log = get_self_model_block(db, user_id=user_id, section="growth_log")
        if growth_log is None:
            return _make_legacy_view(
                user_id=user_id,
                section="growth_log",
                plaintext="",
                version=0,
                updated_by=updated_by,
            )
        return growth_log

    with _runtime_session() as pg_db:
        if pg_db is None:
            # No runtime DB — fall back to writing in the soul DB
            # via the legacy SelfModelBlock table.
            existing = db.scalar(
                select(SelfModelBlock).where(
                    SelfModelBlock.user_id == user_id,
                    SelfModelBlock.section == section,
                )
            )
            if existing is not None:
                existing.content = ef(user_id, content, table="self_model_blocks", field="content")
                existing.version += 1
                existing.updated_by = updated_by
                existing.updated_at = datetime.now(UTC)
                db.flush()
                return existing
            block = SelfModelBlock(
                user_id=user_id,
                section=section,
                content=ef(user_id, content, table="self_model_blocks", field="content"),
                version=1,
                updated_by=updated_by,
            )
            db.add(block)
            db.flush()
            return block
        if section == "intentions":
            row = set_active_intentions(pg_db, user_id=user_id, content=content, updated_by=updated_by)
        else:
            row = set_working_context(
                pg_db,
                user_id=user_id,
                section=section,
                content=content,
                updated_by=updated_by,
            )
        return _make_legacy_view(
            user_id=user_id,
            section=section,
            plaintext=row.content,
            version=row.version,
            updated_by=row.updated_by,
            created_at=getattr(row, "created_at", None),
            updated_at=row.updated_at,
            row_id=row.id,
        )


def append_growth_log_entry(
    db: Session,
    *,
    user_id: int,
    entry: str,
    max_entries: int = 20,
) -> LegacySelfModelBlockView | None:
    """Backward-compatible growth-log append wrapper."""
    row = append_growth_log_entry_row(
        db,
        user_id=user_id,
        entry=entry,
        max_entries=max_entries,
    )
    if row is None:
        return None
    block = get_self_model_block(db, user_id=user_id, section="growth_log")
    if isinstance(block, LegacySelfModelBlockView):
        return block
    return None


def seed_self_model(
    db: Session,
    *,
    user_id: int,
) -> dict[str, SelfModelBlock | LegacySelfModelBlockView]:
    """Seed the split self-model stores for a new user."""
    created: dict[str, SelfModelBlock | LegacySelfModelBlockView] = {}

    if get_identity_block(db, user_id=user_id) is None:
        created["identity"] = _make_legacy_view_from_identity(
            user_id=user_id,
            block=set_identity_block(db, user_id=user_id, content=_SEED_IDENTITY, updated_by="system"),
        )
    else:
        existing = get_self_model_block(db, user_id=user_id, section="identity")
        if existing is not None:
            created["identity"] = existing

    growth = get_self_model_block(db, user_id=user_id, section="growth_log")
    if growth is not None:
        created["growth_log"] = growth

    with _runtime_session() as pg_db:
        if pg_db is not None:
            working = get_working_context(pg_db, user_id=user_id)
            if "inner_state" not in working:
                created["inner_state"] = _make_legacy_view_from_runtime(
                    user_id=user_id,
                    row=set_working_context(
                        pg_db,
                        user_id=user_id,
                        section="inner_state",
                        content=_SEED_INNER_STATE,
                        updated_by="system",
                    ),
                )
            else:
                created["inner_state"] = _make_legacy_view_from_runtime(
                    user_id=user_id,
                    row=working["inner_state"],
                )

            if "working_memory" not in working:
                created["working_memory"] = _make_legacy_view_from_runtime(
                    user_id=user_id,
                    row=set_working_context(
                        pg_db,
                        user_id=user_id,
                        section="working_memory",
                        content=_SEED_WORKING_MEMORY,
                        updated_by="system",
                    ),
                )
            else:
                created["working_memory"] = _make_legacy_view_from_runtime(
                    user_id=user_id,
                    row=working["working_memory"],
                )

            intentions = get_active_intentions(pg_db, user_id=user_id)
            if intentions is None:
                created["intentions"] = _make_legacy_view_from_runtime(
                    user_id=user_id,
                    row=set_active_intentions(
                        pg_db,
                        user_id=user_id,
                        content=_SEED_INTENTIONS,
                        updated_by="system",
                    ),
                )
            else:
                created["intentions"] = _make_legacy_view_from_runtime(
                    user_id=user_id,
                    row=intentions,
                )

    return created


def ensure_self_model_exists(
    db: Session,
    *,
    user_id: int,
) -> None:
    """Ensure the split self-model exists for the user."""
    missing = False
    if get_identity_block(db, user_id=user_id) is None and get_self_model_block(
        db, user_id=user_id, section="identity"
    ) is None:
        missing = True

    with _runtime_session() as pg_db:
        if pg_db is None:
            runtime_missing = False
        else:
            working = get_working_context(pg_db, user_id=user_id)
            runtime_missing = (
                "inner_state" not in working
                or "working_memory" not in working
                or get_active_intentions(pg_db, user_id=user_id) is None
            )

    if missing or runtime_missing:
        seed_self_model(db, user_id=user_id)


def expire_working_memory_items(
    db: Session,
    *,
    user_id: int,
) -> int:
    """Remove expired items from runtime working memory."""
    with _runtime_session() as pg_db:
        if pg_db is not None:
            row = get_working_context(pg_db, user_id=user_id).get("working_memory")
            if row is not None:
                return _expire_working_memory_row(
                    set_row=lambda text: set_working_context(
                        pg_db,
                        user_id=user_id,
                        section="working_memory",
                        content=text,
                        updated_by="expiry_sweep",
                    ),
                    plaintext=row.content,
                )

    block = db.scalar(
        select(SelfModelBlock).where(
            SelfModelBlock.user_id == user_id,
            SelfModelBlock.section == "working_memory",
        )
    )
    if block is None:
        return 0
    plaintext = df(user_id, block.content, table="self_model_blocks", field="content")
    return _expire_working_memory_row(
        set_row=lambda text: set_self_model_block(
            db,
            user_id=user_id,
            section="working_memory",
            content=text,
            updated_by="expiry_sweep",
        ),
        plaintext=plaintext,
    )


def render_self_model_section(
    block: SelfModelBlock | LegacySelfModelBlockView | IdentityBlock | ActiveIntention | WorkingContext | None,
    *,
    budget: int | None = None,
    user_id: int = 0,
) -> str:
    """Render a self-model section, respecting character budget."""
    if block is None:
        return ""

    plaintext = getattr(block, "_plaintext", None)
    if plaintext is None:
        if isinstance(block, SelfModelBlock):
            plaintext = df(user_id, block.content, table="self_model_blocks", field="content").strip()
        else:
            plaintext = getattr(block, "content", "").strip()
    if not plaintext:
        return ""

    max_chars = budget or _BUDGET.get(getattr(block, "section", ""), 1000)
    if len(plaintext) > max_chars:
        return plaintext[:max_chars]
    return plaintext


def _is_duplicate_growth_entry_text(existing_entry: str, new_entry: str) -> bool:
    """Check whether two growth-log entries are substantially similar."""
    new_words = set(new_entry.lower().split())
    if len(new_words) < 3:
        return new_entry.lower().strip() in existing_entry.lower()

    existing_words = set(existing_entry.lower().split())
    if not existing_words:
        return False
    overlap = len(new_words & existing_words) / max(len(new_words), len(existing_words))
    return overlap > 0.7


def _replace_growth_log_entries(
    db: Session,
    *,
    user_id: int,
    content: str,
    source: str,
) -> None:
    existing = db.scalars(select(GrowthLogEntry).where(GrowthLogEntry.user_id == user_id)).all()
    for row in existing:
        db.delete(row)
    db.flush()

    chunks = [c.strip() for c in re.split(r"(?:^|\n)### ", content) if c.strip()]
    if not chunks and content.strip():
        append_growth_log_entry_row(db, user_id=user_id, entry=content.strip(), source=source)
        return

    for chunk in chunks:
        entry_text = chunk
        if " - " in chunk:
            maybe_date, remainder = chunk.split(" - ", 1)
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", maybe_date.strip()):
                entry_text = remainder.strip()
        append_growth_log_entry_row(db, user_id=user_id, entry=entry_text, source=source)


def _expire_working_memory_row(*, set_row, plaintext: str) -> int:
    if not plaintext.strip():
        return 0

    today = datetime.now(UTC).date()
    kept: list[str] = []
    removed = 0

    for line in plaintext.split("\n"):
        match = re.search(r"\[expires:\s*(\d{4}-\d{2}-\d{2})\]", line)
        if match:
            try:
                expiry = datetime.strptime(match.group(1), "%Y-%m-%d").date()
            except ValueError:
                kept.append(line)
                continue
            if expiry < today:
                removed += 1
                continue
        kept.append(line)

    if removed > 0:
        set_row("\n".join(kept).strip())
    return removed


def _make_legacy_view(
    *,
    user_id: int,
    section: str,
    plaintext: str,
    version: int,
    updated_by: str,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    row_id: int | None = None,
) -> LegacySelfModelBlockView:
    encrypted = ef(user_id, plaintext, table="self_model_blocks", field="content")
    return LegacySelfModelBlockView(
        id=row_id,
        user_id=user_id,
        section=section,
        content=encrypted,
        version=version,
        updated_by=updated_by,
        created_at=created_at,
        updated_at=updated_at,
        _plaintext=plaintext,
    )


def _make_legacy_view_from_identity(
    *,
    user_id: int,
    block: IdentityBlock,
) -> LegacySelfModelBlockView:
    return _make_legacy_view(
        user_id=user_id,
        section="identity",
        plaintext=block.content,
        version=block.version,
        updated_by=block.updated_by,
        created_at=block.created_at,
        updated_at=block.updated_at,
        row_id=block.id,
    )


def _make_legacy_view_from_runtime(
    *,
    user_id: int,
    row: WorkingContext | ActiveIntention,
) -> LegacySelfModelBlockView:
    section = row.section if isinstance(row, WorkingContext) else "intentions"
    return _make_legacy_view(
        user_id=user_id,
        section=section,
        plaintext=row.content,
        version=row.version,
        updated_by=row.updated_by,
        created_at=getattr(row, "created_at", None),
        updated_at=row.updated_at,
        row_id=row.id,
    )


def _maybe_decrypt_migrated(
    user_id: int, value: str, *, table: str, field: str
) -> str:
    """Transparently decrypt content migrated from self_model_blocks.

    During the P3 migration, content is copied as-is from self_model_blocks
    (where it may have been encrypted with AAD "self_model_blocks:user_id:field")
    into new tables that store plaintext.  On first read, if the value looks
    like ciphertext (starts with "enc1:" or "enc2:"), we decrypt it using the
    original AAD and return plaintext.
    """
    if not value or not (value.startswith("enc1:") or value.startswith("enc2:")):
        return value
    try:
        return df(user_id, value, table=table, field=field)
    except Exception:
        return value


_has_table_cache: dict[int, dict[str, bool]] = {}


def _has_table(db: Session, table_name: str) -> bool:
    """Check if a table exists, caching per engine instance."""
    try:
        engine = db.get_bind()
        engine_key = id(engine)
        # Don't cache for in-memory SQLite (test fixtures reuse addresses)
        is_memory = str(engine.url) == "sqlite://"
    except Exception:
        return _has_table_uncached(db, table_name)

    if not is_memory:
        engine_cache = _has_table_cache.get(engine_key)
        if engine_cache is not None:
            cached = engine_cache.get(table_name)
            if cached is not None:
                return cached

    result = _has_table_uncached(db, table_name)

    if not is_memory:
        if engine_key not in _has_table_cache:
            _has_table_cache[engine_key] = {}
        _has_table_cache[engine_key][table_name] = result

    return result


def _has_table_uncached(db: Session, table_name: str) -> bool:
    try:
        return sa_inspect(db.connection()).has_table(table_name)
    except Exception:
        return False


@contextmanager
def _runtime_session() -> Iterator[Session | None]:
    try:
        from anima_server.db.runtime import get_runtime_session_factory

        factory = get_runtime_session_factory()
    except Exception:
        yield None
        return

    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
