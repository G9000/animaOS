from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from anima_server.db.base import Base
from anima_server.models import MemoryItem, User
from anima_server.services.agent.candidate_ops import create_memory_candidate
from anima_server.services.agent.evidence_retrieval import RetrievalMode, WideEvidenceResult
from anima_server.services.agent.tool_context import (
    ToolContext,
    clear_tool_context,
    set_tool_context,
)
from anima_server.services.agent.tools import (
    recall_memory,
    recall_transcript,
    save_to_memory,
    search_long_memory,
)
from conftest_runtime import runtime_db_session
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@contextmanager
def _soul_db_session() -> Generator[Session, None, None]:
    engine: Engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )
    Base.metadata.create_all(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _add_user(db: Session) -> User:
    user = User(username="recall-regression", password_hash="x", display_name="Recall")
    db.add(user)
    db.flush()
    return user


def _set_context(db: Session, runtime_db: Session, user_id: int) -> None:
    set_tool_context(
        ToolContext(
            db=db,
            runtime_db=runtime_db,
            user_id=user_id,
            thread_id=1,
        )
    )


async def _empty_hybrid_search(*args, **kwargs) -> SimpleNamespace:
    return SimpleNamespace(items=[], query_embedding=[0.0])


def test_explicit_save_is_recallable_before_promotion(monkeypatch) -> None:
    monkeypatch.setattr(
        "anima_server.services.agent.embeddings.hybrid_search",
        _empty_hybrid_search,
    )

    with _soul_db_session() as db, runtime_db_session() as runtime_db:
        user = _add_user(db)
        _set_context(db, runtime_db, user.id)
        try:
            saved = save_to_memory(
                "I collect brass bookmarks.",
                category="preference",
                importance="4",
            )
            recalled = recall_memory("brass bookmarks")
        finally:
            clear_tool_context()

    assert "Saved 'I collect brass bookmarks.'" in saved
    assert "[pending] I collect brass bookmarks." in recalled


def test_recall_memory_falls_back_to_extracted_candidates(monkeypatch) -> None:
    monkeypatch.setattr(
        "anima_server.services.agent.embeddings.hybrid_search",
        _empty_hybrid_search,
    )

    with _soul_db_session() as db, runtime_db_session() as runtime_db:
        user = _add_user(db)
        create_memory_candidate(
            runtime_db,
            user_id=user.id,
            content="The calibration phrase is violet compass.",
            category="fact",
            importance=3,
        )
        _set_context(db, runtime_db, user.id)
        try:
            recalled = recall_memory("violet compass")
        finally:
            clear_tool_context()

    assert "[pending] The calibration phrase is violet compass." in recalled


def test_recall_memory_keyword_fallback_finds_canonical_memory(monkeypatch) -> None:
    monkeypatch.setattr(
        "anima_server.services.agent.embeddings.hybrid_search",
        _empty_hybrid_search,
    )

    with _soul_db_session() as db, runtime_db_session() as runtime_db:
        user = _add_user(db)
        db.add(
            MemoryItem(
                user_id=user.id,
                content="The user keeps saffron tea in the studio.",
                category="fact",
                importance=3,
                source="test",
            )
        )
        db.flush()
        _set_context(db, runtime_db, user.id)
        try:
            recalled = recall_memory("saffron tea")
        finally:
            clear_tool_context()

    assert "The user keeps saffron tea in the studio." in recalled


def test_recall_memory_uses_hybrid_semantic_results(monkeypatch) -> None:
    with _soul_db_session() as db, runtime_db_session() as runtime_db:
        user = _add_user(db)
        item = MemoryItem(
            user_id=user.id,
            content="The user is learning carved linocut printing.",
            category="goal",
            importance=4,
            source="test",
        )
        db.add(item)
        db.flush()

        async def fake_hybrid_search(*args, **kwargs) -> SimpleNamespace:
            return SimpleNamespace(items=[(item, 0.93)], query_embedding=[0.1, 0.2])

        monkeypatch.setattr(
            "anima_server.services.agent.embeddings.hybrid_search",
            fake_hybrid_search,
        )
        _set_context(db, runtime_db, user.id)
        try:
            recalled = recall_memory("linocut printing")
        finally:
            clear_tool_context()

    assert "[goal] The user is learning carved linocut printing." in recalled


def test_search_long_memory_passes_latest_mode_and_returns_evidence(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    async def fake_retrieve_wide_evidence(**kwargs) -> WideEvidenceResult:
        calls.append(kwargs)
        return WideEvidenceResult(
            mode=RetrievalMode.LATEST_UPDATE,
            semantic_results=[
                (
                    1,
                    "Session date: 2026-01-04\nUser: Rachel moved to the north side.",
                    0.88,
                )
            ],
            total_considered=1,
        )

    monkeypatch.setattr(
        "anima_server.services.agent.evidence_retrieval.retrieve_wide_evidence",
        fake_retrieve_wide_evidence,
    )
    with _soul_db_session() as db, runtime_db_session() as runtime_db:
        user = _add_user(db)
        _set_context(db, runtime_db, user.id)
        try:
            result = search_long_memory(
                "Where did Rachel move most recently?",
                mode="latest_update",
            )
        finally:
            clear_tool_context()

    assert calls[0]["mode"] == RetrievalMode.LATEST_UPDATE
    assert "Rachel moved to the north side" in result


def test_recall_transcript_uses_all_time_default(managed_tmp_path) -> None:
    with (
        patch(
            "anima_server.services.agent.tool_context.get_tool_context",
            return_value=SimpleNamespace(user_id=1),
        ),
        patch(
            "anima_server.services.data_crypto.get_active_dek",
            return_value=None,
        ),
        patch(
            "anima_server.services.agent.transcript_search.search_transcripts",
            return_value=[],
        ) as mock_search,
        patch("anima_server.config.settings") as mock_settings,
    ):
        mock_settings.data_dir = managed_tmp_path
        recall_transcript("exact old passphrase")

    assert mock_search.call_args.kwargs["days_back"] == 0
