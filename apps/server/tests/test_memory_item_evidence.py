from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime

from anima_server.db.base import Base
from anima_server.models import MemoryItem, MemoryItemEvidence, User
from anima_server.models.runtime import RuntimeMessage, RuntimeThread
from anima_server.models.runtime_memory import MemoryCandidate
from anima_server.services.agent.tool_context import (
    ToolContext,
    clear_tool_context,
    set_tool_context,
)
from anima_server.services.agent.tools import save_to_memory
from anima_server.services.data_crypto import DOMAIN_MEMORIES, resolve_domain
from conftest_runtime import runtime_db_session
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@contextmanager
def _db_session() -> Generator[Session, None, None]:
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


def test_memory_item_evidence_persists_source_metadata() -> None:
    observed_at = datetime(2026, 5, 1, 12, 30, tzinfo=UTC)

    with _db_session() as db:
        user = User(username="evidence-user", password_hash="x", display_name="Evidence")
        db.add(user)
        db.flush()
        item = MemoryItem(
            user_id=user.id,
            content="Rachel moved to the north side.",
            category="fact",
            importance=4,
            source="extraction",
        )
        db.add(item)
        db.flush()

        evidence = MemoryItemEvidence(
            user_id=user.id,
            memory_item_id=item.id,
            source_kind="llm_extraction",
            runtime_thread_id=12,
            runtime_message_id=34,
            runtime_message_ids_json=[33, 34],
            transcript_ref="thread-12.jsonl.enc",
            sequence_id=5,
            speaker="user",
            observed_at=observed_at,
            source_created_at=observed_at,
            confidence=0.82,
            extractor="test-model",
            evidence_text="User: Rachel moved to the north side.",
            metadata_json={"batch": "test"},
        )
        db.add(evidence)
        db.commit()

        stored = db.query(MemoryItemEvidence).one()

    assert stored.memory_item_id == item.id
    assert stored.runtime_message_ids_json == [33, 34]
    assert stored.transcript_ref == "thread-12.jsonl.enc"
    assert stored.speaker == "user"
    assert stored.observed_at == observed_at
    assert stored.confidence == 0.82
    assert stored.metadata_json == {"batch": "test"}


def test_memory_item_evidence_metadata_and_crypto_domain() -> None:
    table = Base.metadata.tables["memory_item_evidence"]
    column_names = {column.name for column in table.columns}

    assert {
        "user_id",
        "memory_item_id",
        "source_kind",
        "runtime_message_id",
        "runtime_message_ids_json",
        "transcript_ref",
        "speaker",
        "observed_at",
        "confidence",
        "evidence_text",
    } <= column_names
    assert resolve_domain("memory_item_evidence") == DOMAIN_MEMORIES


def test_memory_item_evidence_indexes_exist() -> None:
    with _db_session() as db:
        index_names = {
            index["name"]
            for index in inspect(db.get_bind()).get_indexes("memory_item_evidence")
        }

    assert "ix_memory_item_evidence_user_item" in index_names
    assert "ix_memory_item_evidence_user_observed" in index_names
    assert "ix_memory_item_evidence_source_observed" in index_names


def test_explicit_save_candidate_records_source_message_id() -> None:
    with _db_session() as db, runtime_db_session() as runtime_db:
        user = User(username="explicit-evidence", password_hash="x", display_name="Explicit")
        db.add(user)
        db.flush()
        thread = RuntimeThread(user_id=user.id, status="active", next_message_sequence=2)
        runtime_db.add(thread)
        runtime_db.flush()
        message = RuntimeMessage(
            thread_id=thread.id,
            user_id=user.id,
            sequence_id=1,
            role="user",
            content_text="Please remember that I collect fountain pens.",
        )
        runtime_db.add(message)
        runtime_db.commit()

        set_tool_context(
            ToolContext(
                db=db,
                runtime_db=runtime_db,
                user_id=user.id,
                thread_id=thread.id,
            )
        )
        try:
            save_to_memory(
                "I collect fountain pens.",
                category="preference",
                importance="5",
            )
        finally:
            clear_tool_context()

        candidate = runtime_db.scalar(
            select(MemoryCandidate).where(MemoryCandidate.user_id == user.id)
        )

    assert candidate is not None
    assert candidate.source_message_ids == [message.id]
