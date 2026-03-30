"""Tests for Soul Writer runtime models and candidate lifecycle."""
from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from anima_server.db.runtime_base import RuntimeBase
from anima_server.models.runtime_memory import (
    MemoryAccessLog,
    MemoryCandidate,
    PromotionJournal,
)


@pytest.fixture()
def pg_session():
    """In-memory PG-like SQLite session for model tests."""
    engine = create_engine("sqlite:///:memory:")
    RuntimeBase.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    RuntimeBase.metadata.drop_all(bind=engine)


def _content_hash(user_id: int, category: str, importance_source: str, content: str) -> str:
    normalized = content.strip().lower()
    return hashlib.sha256(f"{user_id}:{category}:{importance_source}:{normalized}".encode()).hexdigest()


def test_create_memory_candidate(pg_session: Session) -> None:
    candidate = MemoryCandidate(
        user_id=1,
        content="Has a dog named Biscuit",
        category="fact",
        importance=3,
        importance_source="llm",
        source="llm",
        content_hash=_content_hash(1, "fact", "llm", "Has a dog named Biscuit"),
        status="extracted",
    )
    pg_session.add(candidate)
    pg_session.flush()
    assert candidate.id is not None
    assert candidate.status == "extracted"
    assert candidate.retry_count == 0


def test_create_promotion_journal(pg_session: Session) -> None:
    entry = PromotionJournal(
        user_id=1,
        decision="promoted",
        reason="new memory",
        target_table="memory_items",
        target_record_id="42",
        content_hash="abc123",
        journal_status="confirmed",
    )
    pg_session.add(entry)
    pg_session.flush()
    assert entry.id is not None


def test_create_memory_access_log(pg_session: Session) -> None:
    log = MemoryAccessLog(
        user_id=1,
        memory_item_id=42,
        synced=False,
    )
    pg_session.add(log)
    pg_session.flush()
    assert log.id is not None
    assert log.synced is False


def test_candidate_status_lifecycle(pg_session: Session) -> None:
    candidate = MemoryCandidate(
        user_id=1, content="test", category="fact",
        importance=3, importance_source="llm", source="llm",
        content_hash=_content_hash(1, "fact", "llm", "test"),
    )
    pg_session.add(candidate)
    pg_session.flush()

    # extracted → promoted
    candidate.status = "promoted"
    candidate.processed_at = candidate.created_at
    pg_session.flush()
    assert candidate.status == "promoted"


def test_correction_and_extraction_get_distinct_hashes(pg_session: Session) -> None:
    """Correction and extraction candidates with same content get different hashes."""
    hash_llm = _content_hash(1, "fact", "llm", "likes cats")
    hash_correction = _content_hash(1, "fact", "correction", "likes cats")
    assert hash_llm != hash_correction


def test_pending_memory_op_has_content_hash() -> None:
    """PendingMemoryOp model has content_hash column."""
    from anima_server.models.pending_memory_op import PendingMemoryOp

    columns = {c.name for c in PendingMemoryOp.__table__.columns}
    assert "content_hash" in columns


def test_touch_memory_items_writes_to_pg_access_log(pg_session: Session) -> None:
    """touch_memory_items should create MemoryAccessLog rows in PG, not mutate SQLCipher."""
    from unittest.mock import MagicMock

    from anima_server.services.agent.memory_store import touch_memory_items

    mock_item = MagicMock()
    mock_item.id = 42
    mock_item.user_id = 1
    mock_item.reference_count = 5
    mock_item.last_referenced_at = None

    touch_memory_items(db=MagicMock(), items=[mock_item], runtime_db=pg_session)

    logs = pg_session.scalars(select(MemoryAccessLog)).all()
    assert len(logs) == 1
    assert logs[0].memory_item_id == 42
    assert logs[0].synced is False

    # Verify the soul db item was NOT mutated
    assert mock_item.reference_count == 5


@pytest.mark.asyncio
async def test_sync_access_metadata(pg_session: Session) -> None:
    """sync_access_metadata aggregates PG access logs into counts."""
    from anima_server.services.agent.access_sync import sync_access_metadata

    for _ in range(3):
        pg_session.add(MemoryAccessLog(user_id=1, memory_item_id=42, synced=False))
    pg_session.flush()

    result = await sync_access_metadata(
        user_id=1, runtime_db=pg_session, soul_db=None, dry_run=True,
    )
    assert result["items_synced"] == 1
    assert result["access_counts"] == {42: 3}
