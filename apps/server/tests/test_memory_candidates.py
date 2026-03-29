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
