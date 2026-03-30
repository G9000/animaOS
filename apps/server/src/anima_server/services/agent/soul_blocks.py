from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models import SelfModelBlock
from anima_server.services.data_crypto import df, ef


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
) -> SelfModelBlock:
    encrypted_content = ef(user_id, content, table="self_model_blocks", field="content")
    block = _get_soul_block(soul_db, user_id=user_id, section=section)
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
) -> SelfModelBlock:
    """Create or overwrite a soul-tier block."""
    return _write_soul_block(
        soul_db,
        user_id=user_id,
        section=section,
        content=content.strip(),
        updated_by=updated_by,
        metadata=metadata,
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
) -> SelfModelBlock:
    """Unconditionally replace a soul block's plaintext content."""
    return _write_soul_block(
        soul_db,
        user_id=user_id,
        section=section,
        content=content.strip(),
        updated_by=updated_by,
    )
