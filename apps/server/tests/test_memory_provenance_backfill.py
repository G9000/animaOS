from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime

from anima_server.db.base import Base
from anima_server.models import MemoryItem, MemoryItemEvidence, User
from anima_server.services.data_crypto import df
from sqlalchemy import create_engine, select
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


def test_backfill_memory_item_evidence_creates_legacy_and_eval_rows() -> None:
    from anima_server.services.agent.provenance import backfill_memory_item_evidence

    created_at = datetime(2023, 5, 1, 12, 0, tzinfo=UTC)
    with _db_session() as db:
        user = User(username="backfill", password_hash="x", display_name="Backfill")
        db.add(user)
        db.flush()
        legacy_item = MemoryItem(
            user_id=user.id,
            content="Rachel moved to the city.",
            category="fact",
            source="extraction",
            created_at=created_at,
        )
        eval_item = MemoryItem(
            user_id=user.id,
            content=(
                "Session date: 2023/05/29 (Mon) 20:29\n"
                "User: I got a 1/72 scale B-29 bomber kit."
            ),
            category="fact",
            source="eval_import_raw",
            created_at=created_at,
        )
        covered_item = MemoryItem(
            user_id=user.id,
            content="Already has evidence.",
            category="fact",
            source="extraction",
            created_at=created_at,
        )
        db.add_all([legacy_item, eval_item, covered_item])
        db.flush()
        db.add(
            MemoryItemEvidence(
                user_id=user.id,
                memory_item_id=covered_item.id,
                source_kind="explicit_save",
                evidence_text="Already has evidence.",
            )
        )
        db.flush()

        result = backfill_memory_item_evidence(db, user_id=user.id)
        db.flush()
        second_result = backfill_memory_item_evidence(db, user_id=user.id)

        rows = list(
            db.scalars(
                select(MemoryItemEvidence)
                .where(MemoryItemEvidence.user_id == user.id)
                .order_by(MemoryItemEvidence.memory_item_id, MemoryItemEvidence.id)
            ).all()
        )

    assert result.created == 2
    assert result.skipped_existing == 1
    assert second_result.created == 0
    assert second_result.skipped_existing == 3

    by_item = {row.memory_item_id: row for row in rows}
    legacy = by_item[legacy_item.id]
    assert legacy.source_kind == "legacy_backfill"
    assert legacy.observed_at == created_at.replace(tzinfo=None)
    assert legacy.confidence == 0.5
    assert legacy.metadata_json == {
        "source": "legacy_backfill",
        "memory_source": "extraction",
    }
    assert (
        df(user.id, legacy.evidence_text, table="memory_item_evidence", field="evidence_text")
        == "Rachel moved to the city."
    )

    eval_evidence = by_item[eval_item.id]
    assert eval_evidence.source_kind == "eval_import"
    assert eval_evidence.observed_at == datetime(2023, 5, 29, 20, 29)
    assert eval_evidence.speaker == "user"
    assert eval_evidence.confidence == 0.7
    assert eval_evidence.metadata_json == {
        "source": "legacy_eval_raw_chunk",
        "session_date": "2023/05/29 (Mon) 20:29",
    }
