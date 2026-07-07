from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models import SelfModelBlock
from anima_server.services.data_crypto import df, ef


class SoulBlockConflict(Exception):
    """A version-checked block write lost a race with a concurrent writer.

    Raised instead of silently overwriting: a slow reflection that read a
    block before a user-driven write landed must not full-replace it with
    its stale snapshot.
    """

    def __init__(self, *, section: str, expected_version: int, actual_version: int) -> None:
        self.section = section
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            f"soul block '{section}' is at version {actual_version}, "
            f"writer expected {expected_version}"
        )


def _get_soul_block(
    soul_db: Session,
    *,
    user_id: int,
    section: str,
) -> SelfModelBlock | None:
    return soul_db.scalar(
        select(SelfModelBlock).where(
            SelfModelBlock.user_id == user_id,
            SelfModelBlock.section == section,
        )
    )


def _write_soul_block(
    soul_db: Session,
    *,
    user_id: int,
    section: str,
    content: str,
    updated_by: str,
    metadata: dict | None = None,
    expected_version: int | None = None,
) -> SelfModelBlock:
    """Write a block, optionally with optimistic locking.

    *expected_version* is the version the writer read its snapshot from
    (0 = the block did not exist yet).  On mismatch the write is refused
    with :class:`SoulBlockConflict` so the caller can re-read and decide;
    ``None`` skips the check (delta-shaped writers that read fresh state
    in the same session, like the soul writer's pending ops).
    """
    encrypted_content = ef(user_id, content, table="self_model_blocks", field="content")
    block = _get_soul_block(soul_db, user_id=user_id, section=section)
    if expected_version is not None:
        actual_version = block.version if block is not None else 0
        if actual_version != expected_version:
            raise SoulBlockConflict(
                section=section,
                expected_version=expected_version,
                actual_version=actual_version,
            )
    if block is None:
        block = SelfModelBlock(
            user_id=user_id,
            section=section,
            content=encrypted_content,
            version=1,
            updated_by=updated_by,
            metadata_json=metadata,
        )
        soul_db.add(block)
        soul_db.flush()
        return block

    block.content = encrypted_content
    block.version += 1
    block.updated_by = updated_by
    block.updated_at = datetime.now(UTC)
    if metadata is not None:
        block.metadata_json = metadata
    soul_db.flush()
    return block


def set_soul_block(
    soul_db: Session,
    *,
    user_id: int,
    section: str,
    content: str,
    updated_by: str,
    metadata: dict | None = None,
    expected_version: int | None = None,
) -> SelfModelBlock:
    """Create or overwrite a soul-tier block."""
    return _write_soul_block(
        soul_db,
        user_id=user_id,
        section=section,
        content=content.strip(),
        updated_by=updated_by,
        metadata=metadata,
        expected_version=expected_version,
    )


def append_to_soul_block(
    soul_db: Session,
    *,
    user_id: int,
    section: str,
    content: str,
    updated_by: str = "consolidation",
) -> SelfModelBlock:
    """Append plaintext content to a soul block, creating it if needed."""
    block = _get_soul_block(soul_db, user_id=user_id, section=section)
    existing = (
        df(user_id, block.content, table="self_model_blocks", field="content").strip()
        if block is not None
        else ""
    )
    appended = content.strip()
    next_content = appended if not existing else (existing.rstrip() + "\n" + appended).strip()
    return _write_soul_block(
        soul_db,
        user_id=user_id,
        section=section,
        content=next_content,
        updated_by=updated_by,
    )


def replace_in_soul_block(
    soul_db: Session,
    *,
    user_id: int,
    section: str,
    old_content: str,
    new_content: str,
    updated_by: str = "consolidation",
) -> SelfModelBlock | None:
    """Replace plaintext content in a soul block once; return None on mismatch."""
    block = _get_soul_block(soul_db, user_id=user_id, section=section)
    if block is None:
        return None

    existing = df(user_id, block.content, table="self_model_blocks", field="content")
    if old_content not in existing:
        return None

    replaced = existing.replace(old_content, new_content.strip(), 1)
    return _write_soul_block(
        soul_db,
        user_id=user_id,
        section=section,
        content=replaced,
        updated_by=updated_by,
    )


def full_replace_soul_block(
    soul_db: Session,
    *,
    user_id: int,
    section: str,
    content: str,
    updated_by: str = "consolidation",
    expected_version: int | None = None,
) -> SelfModelBlock:
    """Replace a soul block's plaintext content (version-checked if asked)."""
    return _write_soul_block(
        soul_db,
        user_id=user_id,
        section=section,
        content=content.strip(),
        updated_by=updated_by,
        expected_version=expected_version,
    )
