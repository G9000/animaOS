"""Tests for memory pipeline reliability fixes."""

from __future__ import annotations

import hashlib

import pytest
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models.runtime_memory import MemoryCandidate
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def _content_hash(user_id: int, category: str, importance_source: str, content: str) -> str:
    normalized = content.strip().lower()
    return hashlib.sha256(
        f"{user_id}:{category}:{importance_source}:{normalized}".encode()
    ).hexdigest()


@pytest.fixture()
def runtime_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    RuntimeBase.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    RuntimeBase.metadata.drop_all(bind=engine)


def test_search_candidates_keyword(runtime_session: Session) -> None:
    """Candidate search should find items by keyword overlap."""
    from anima_server.services.agent.tools import _search_candidates

    candidate = MemoryCandidate(
        user_id=1,
        content="User has three cats named Muffin, Tappy, and Whiskers",
        category="fact",
        importance=3,
        importance_source="llm",
        source="llm",
        content_hash=_content_hash(
            1, "fact", "llm", "User has three cats named Muffin, Tappy, and Whiskers"
        ),
        status="extracted",
    )
    runtime_session.add(candidate)
    runtime_session.flush()

    results = _search_candidates(runtime_session, user_id=1, query="cats")
    assert len(results) >= 1
    assert "cats" in results[0][1].lower()


def test_search_candidates_excludes_promoted(runtime_session: Session) -> None:
    """Candidates already promoted should not appear in fallback search."""
    from anima_server.services.agent.tools import _search_candidates

    candidate = MemoryCandidate(
        user_id=1,
        content="User has a dog named Rex",
        category="fact",
        importance=3,
        importance_source="llm",
        source="llm",
        content_hash=_content_hash(1, "fact", "llm", "User has a dog named Rex"),
        status="promoted",
    )
    runtime_session.add(candidate)
    runtime_session.flush()

    results = _search_candidates(runtime_session, user_id=1, query="dog")
    assert len(results) == 0


def test_search_candidates_word_overlap(runtime_session: Session) -> None:
    """Candidate search should find items by word overlap when no exact match."""
    from anima_server.services.agent.tools import _search_candidates

    candidate = MemoryCandidate(
        user_id=1,
        content="User enjoys playing guitar and piano",
        category="preference",
        importance=3,
        importance_source="llm",
        source="llm",
        content_hash=_content_hash(1, "preference", "llm", "User enjoys playing guitar and piano"),
        status="extracted",
    )
    runtime_session.add(candidate)
    runtime_session.flush()

    results = _search_candidates(runtime_session, user_id=1, query="guitar music")
    assert len(results) >= 1


def test_search_candidates_category_filter(runtime_session: Session) -> None:
    """Candidate search should respect category filter."""
    from anima_server.services.agent.tools import _search_candidates

    candidate = MemoryCandidate(
        user_id=1,
        content="User likes pizza",
        category="preference",
        importance=3,
        importance_source="llm",
        source="llm",
        content_hash=_content_hash(1, "preference", "llm", "User likes pizza"),
        status="extracted",
    )
    runtime_session.add(candidate)
    runtime_session.flush()

    # Should not find it when filtering by 'fact'
    results = _search_candidates(runtime_session, user_id=1, query="pizza", category="fact")
    assert len(results) == 0

    # Should find it when filtering by 'preference'
    results = _search_candidates(runtime_session, user_id=1, query="pizza", category="preference")
    assert len(results) >= 1
