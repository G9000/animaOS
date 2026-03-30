from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models import PendingMemoryOp

_VALID_OP_TYPES = frozenset({"append", "replace", "full_replace"})


def create_pending_op(
    runtime_db: Session,
    *,
    user_id: int,
    op_type: str,
    target_block: str,
    content: str,
    old_content: str | None,
    source_run_id: int | None,
    source_tool_call_id: str | None,
) -> PendingMemoryOp:
    """Persist a pending identity write in the runtime store."""
    normalized_type = op_type.strip().lower()
    if normalized_type not in _VALID_OP_TYPES:
        raise ValueError(f"Invalid pending op type: {op_type}")

    content_hash = hashlib.sha256(
        f"{user_id}:{target_block.strip()}:{normalized_type}:{content.strip()}".encode()
    ).hexdigest()

    op = PendingMemoryOp(
        user_id=user_id,
        op_type=normalized_type,
        target_block=target_block.strip(),
        content=content.strip(),
        old_content=old_content,
        source_run_id=source_run_id,
        source_tool_call_id=source_tool_call_id,
        content_hash=content_hash,
    )
    runtime_db.add(op)
    runtime_db.flush()
    return op


def get_pending_ops(
    runtime_db: Session,
    *,
    user_id: int,
    target_block: str | None = None,
    limit: int = 50,
) -> list[PendingMemoryOp]:
    """Return unconsolidated, unfailed pending ops in causal order."""
    query = (
        select(PendingMemoryOp)
        .where(
            PendingMemoryOp.user_id == user_id,
            PendingMemoryOp.consolidated.is_(False),
            PendingMemoryOp.failed.is_(False),
        )
        .order_by(PendingMemoryOp.id.asc())
        .limit(limit)
    )
    if target_block is not None:
        query = query.where(PendingMemoryOp.target_block == target_block)
    return list(runtime_db.scalars(query).all())


def apply_pending_op(content: str, op: PendingMemoryOp) -> str:
    """Apply a single pending op to plaintext content without mutating storage."""
    current = content.strip()
    if op.op_type == "append":
        if not current:
            return op.content.strip()
        return (current.rstrip() + "\n" + op.content.strip()).strip()
    if op.op_type == "replace":
        old_content = op.old_content or ""
        if old_content and old_content in current:
            return current.replace(old_content, op.content.strip(), 1)
        return current
    if op.op_type == "full_replace":
        return op.content.strip()
    raise ValueError(f"Unsupported pending op type: {op.op_type}")


def apply_pending_ops(content: str, ops: Sequence[PendingMemoryOp] | Iterable[PendingMemoryOp]) -> str:
    """Apply multiple pending ops in order to plaintext content."""
    merged = content.strip()
    for op in ops:
        merged = apply_pending_op(merged, op)
    return merged
