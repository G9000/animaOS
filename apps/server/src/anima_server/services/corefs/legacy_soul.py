"""Crash-safe migration for legacy plaintext ``users/<id>/soul.md`` files."""

from __future__ import annotations

import binascii
import hashlib
from base64 import b64decode

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models import SelfModelBlock
from anima_server.services.agent.soul_blocks import set_soul_block
from anima_server.services.data_crypto import df
from anima_server.services.storage import get_user_data_dir

_MAX_LEGACY_SOUL_BYTES = 1024 * 1024
_IMPORT_MARKER = "--- Legacy soul.md import ---"


class LegacySoulMigrationError(RuntimeError):
    pass


def _content(user_id: int, block: SelfModelBlock | None) -> str:
    if block is None:
        return ""
    if not block.content.startswith("enc2:"):
        return block.content
    parts = block.content.split(":", 3)
    if len(parts) != 4:
        return block.content
    try:
        for segment in parts[1:]:
            b64decode(segment, validate=True)
    except (binascii.Error, ValueError):
        return block.content
    try:
        return df(
            user_id,
            block.content,
            table="self_model_blocks",
            field="content",
        )
    except Exception as exc:
        raise LegacySoulMigrationError(
            "Existing Soul content could not be authenticated."
        ) from exc


def migrate_legacy_soul_file(db: Session, *, user_id: int) -> bool:
    path = get_user_data_dir(user_id) / "soul.md"
    if path.is_symlink():
        raise LegacySoulMigrationError("Legacy Soul source must not be a symbolic link.")
    if not path.is_file():
        return False
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise LegacySoulMigrationError("Legacy Soul source is unreadable.") from exc
    if len(raw) > _MAX_LEGACY_SOUL_BYTES:
        raise LegacySoulMigrationError("Legacy Soul source exceeds 1 MiB.")
    try:
        legacy = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LegacySoulMigrationError("Legacy Soul source is not UTF-8.") from exc
    digest = hashlib.sha256(raw).hexdigest()

    def represents(content: str) -> bool:
        offset = content.find(legacy)
        if offset < 0:
            return False
        exact = content[offset : offset + len(legacy)].encode("utf-8")
        return hashlib.sha256(exact).hexdigest() == digest

    blocks = {
        block.section: block
        for block in db.scalars(
            select(SelfModelBlock).where(
                SelfModelBlock.user_id == user_id,
                SelfModelBlock.section.in_(("user_directive", "persona")),
            )
        ).all()
    }
    directive = _content(user_id, blocks.get("user_directive"))
    persona = _content(user_id, blocks.get("persona"))
    represented = legacy == "" or represents(directive) or represents(persona)
    if not represented:
        combined = (
            f"{directive.rstrip()}\n\n{_IMPORT_MARKER}\n{legacy}"
            if directive.strip()
            else legacy
        )
        set_soul_block(
            db,
            user_id=user_id,
            section="user_directive",
            content=combined,
            updated_by="legacy_migration",
        )
        db.commit()

    verified = db.scalar(
        select(SelfModelBlock).where(
            SelfModelBlock.user_id == user_id,
            SelfModelBlock.section == "user_directive",
        )
    )
    verified_directive = _content(user_id, verified)
    if legacy and not represents(verified_directive) and not represents(persona):
        raise LegacySoulMigrationError("Legacy Soul verification failed.")
    try:
        path.unlink()
    except OSError as exc:
        raise LegacySoulMigrationError(
            "Legacy Soul was imported but plaintext cleanup failed."
        ) from exc
    return True
