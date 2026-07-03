from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta

from anima_server.db.base import Base
from anima_server.models import ForesightSignal, User
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


def _make_user(db: Session) -> User:
    user = User(username="foresight-user", display_name="Foresight User", password_hash="x")
    db.add(user)
    db.flush()
    return user


def test_relative_foresight_extraction_uses_conversation_timestamp() -> None:
    from anima_server.services.agent.foresight import (
        extract_regex_foresight_signals,
        upsert_foresight_signal,
    )

    observed_at = datetime(2026, 7, 3, 9, 0, tzinfo=UTC)
    signals = extract_regex_foresight_signals(
        "I have a product review next Tuesday.",
        observed_at=observed_at,
    )

    assert len(signals) == 1
    assert signals[0].content == "User has a product review"
    assert signals[0].relative_text == "next Tuesday"
    assert signals[0].start_date == date(2026, 7, 7)
    assert signals[0].end_date == date(2026, 7, 7)

    with _db_session() as db:
        user = _make_user(db)
        stored = upsert_foresight_signal(
            db,
            user_id=user.id,
            signal=signals[0],
            source_thread_id=42,
            source_message_ids=[101],
            observed_at=observed_at,
        )
        db.flush()

        assert df(user.id, stored.content, table="foresight_signals", field="content") == (
            "User has a product review"
        )
        assert df(user.id, stored.evidence, table="foresight_signals", field="evidence") == (
            "I have a product review next Tuesday."
        )
        assert stored.status == "active"
        assert stored.source_thread_id == 42
        assert stored.source_message_ids_json == [101]


def test_foresight_extraction_does_not_cross_sentence_boundaries() -> None:
    from anima_server.services.agent.foresight import extract_regex_foresight_signals

    observed_at = datetime(2026, 7, 3, 9, 0, tzinfo=UTC)
    signals = extract_regex_foresight_signals(
        "I have a headache. My product review is next Tuesday.",
        observed_at=observed_at,
    )

    assert [signal.content for signal in signals] == ["User has a product review"]


def test_relative_foresight_extraction_uses_user_timezone_for_local_dates() -> None:
    from anima_server.services.agent.foresight import extract_regex_foresight_signals

    observed_at = datetime(2026, 7, 4, 6, 30, tzinfo=UTC)
    signals = extract_regex_foresight_signals(
        "I have a product review tomorrow.",
        observed_at=observed_at,
        timezone_name="America/Los_Angeles",
    )

    assert len(signals) == 1
    assert signals[0].start_date == date(2026, 7, 4)
    assert signals[0].end_date == date(2026, 7, 4)


def test_foresight_upsert_deduplicates_overlapping_events() -> None:
    from anima_server.services.agent.foresight import ForesightCandidate, upsert_foresight_signal

    observed_at = datetime(2026, 7, 3, 9, 0, tzinfo=UTC)
    with _db_session() as db:
        user = _make_user(db)
        first = upsert_foresight_signal(
            db,
            user_id=user.id,
            signal=ForesightCandidate(
                content="User has a product review",
                evidence="I have a product review next Tuesday.",
                relative_text="next Tuesday",
                start_date=date(2026, 7, 7),
                end_date=date(2026, 7, 7),
                duration_days=1,
            ),
            source_thread_id=1,
            source_message_ids=[11],
            observed_at=observed_at,
        )
        second = upsert_foresight_signal(
            db,
            user_id=user.id,
            signal=ForesightCandidate(
                content="User has the product review",
                evidence="Reminder: my product review is next Tuesday.",
                relative_text="next Tuesday",
                start_date=date(2026, 7, 7),
                end_date=date(2026, 7, 7),
                duration_days=1,
            ),
            source_thread_id=1,
            source_message_ids=[12],
            observed_at=observed_at + timedelta(hours=1),
        )
        rows = list(db.scalars(select(ForesightSignal)).all())

    assert first.id == second.id
    assert len(rows) == 1
    assert rows[0].source_message_ids_json == [11, 12]
    assert df(user.id, rows[0].evidence, table="foresight_signals", field="evidence") == (
        "I have a product review next Tuesday.\nReminder: my product review is next Tuesday."
    )


def test_foresight_lifecycle_marks_due_occurred_stale_and_cancelled() -> None:
    from anima_server.services.agent.foresight import (
        ForesightCandidate,
        mark_cancelled_from_text,
        sweep_foresight_lifecycle,
        upsert_foresight_signal,
    )

    with _db_session() as db:
        user = _make_user(db)
        due = upsert_foresight_signal(
            db,
            user_id=user.id,
            signal=ForesightCandidate(
                content="User has a demo",
                evidence="I have a demo tomorrow.",
                relative_text="tomorrow",
                start_date=date(2026, 7, 4),
                end_date=date(2026, 7, 4),
                duration_days=1,
            ),
            observed_at=datetime(2026, 7, 3, tzinfo=UTC),
        )
        old = upsert_foresight_signal(
            db,
            user_id=user.id,
            signal=ForesightCandidate(
                content="User has a dentist appointment",
                evidence="Dentist next week.",
                relative_text="next week",
                start_date=date(2026, 6, 20),
                end_date=date(2026, 6, 20),
                duration_days=1,
            ),
            observed_at=datetime(2026, 6, 10, tzinfo=UTC),
        )
        cancelled = upsert_foresight_signal(
            db,
            user_id=user.id,
            signal=ForesightCandidate(
                content="User has a budget review",
                evidence="Budget review next Friday.",
                relative_text="next Friday",
                start_date=date(2026, 7, 10),
                end_date=date(2026, 7, 10),
                duration_days=1,
            ),
            observed_at=datetime(2026, 7, 3, tzinfo=UTC),
        )
        due.status = "active"
        old.status = "occurred"
        db.flush()

        transitions = sweep_foresight_lifecycle(
            db,
            user_id=user.id,
            today=date(2026, 7, 4),
            stale_after_days=7,
        )
        cancelled_count = mark_cancelled_from_text(
            db,
            user_id=user.id,
            text="That budget review got cancelled.",
            observed_at=datetime(2026, 7, 4, tzinfo=UTC),
        )
        db.flush()

    assert transitions == {"due": 1, "occurred": 0, "stale": 1}
    assert cancelled_count == 1
    assert due.status == "due"
    assert old.status == "stale"
    assert cancelled.status == "cancelled"


def test_foresight_memory_block_renders_active_and_due_signals() -> None:
    from anima_server.services.agent.foresight import ForesightCandidate, upsert_foresight_signal
    from anima_server.services.agent.memory_blocks import build_foresight_memory_block

    with _db_session() as db:
        user = _make_user(db)
        active = upsert_foresight_signal(
            db,
            user_id=user.id,
            signal=ForesightCandidate(
                content="User has a product review",
                evidence="I have a product review next Tuesday.",
                relative_text="next Tuesday",
                start_date=date(2026, 7, 7),
                end_date=date(2026, 7, 7),
                duration_days=1,
            ),
            observed_at=datetime(2026, 7, 3, tzinfo=UTC),
        )
        stale = upsert_foresight_signal(
            db,
            user_id=user.id,
            signal=ForesightCandidate(
                content="User has a stale event",
                evidence="Old event.",
                relative_text="last week",
                start_date=date(2026, 6, 20),
                end_date=date(2026, 6, 20),
                duration_days=1,
            ),
            observed_at=datetime(2026, 6, 1, tzinfo=UTC),
        )
        stale.status = "stale"
        active.status = "due"
        db.flush()

        block = build_foresight_memory_block(db, user_id=user.id, today=date(2026, 7, 7))

    assert block is not None
    assert block.label == "foresight"
    assert "User has a product review" in block.value
    assert "due" in block.value
    assert "2026-07-07" in block.value
    assert "stale event" not in block.value


def test_prompt_foresight_skips_overdue_active_rows_before_limiting() -> None:
    from anima_server.services.agent.foresight import (
        ForesightCandidate,
        get_prompt_foresight_signals,
        upsert_foresight_signal,
    )

    with _db_session() as db:
        user = _make_user(db)
        for idx in range(20):
            upsert_foresight_signal(
                db,
                user_id=user.id,
                signal=ForesightCandidate(
                    content=f"User has old event {idx}",
                    evidence=f"Old event {idx}.",
                    relative_text=None,
                    start_date=date(2026, 6, 1),
                    end_date=date(2026, 6, 1),
                    duration_days=1,
                ),
                observed_at=datetime(2026, 5, 1, tzinfo=UTC),
            )
        upcoming = upsert_foresight_signal(
            db,
            user_id=user.id,
            signal=ForesightCandidate(
                content="User has an upcoming product review",
                evidence="I have a product review next Tuesday.",
                relative_text="next Tuesday",
                start_date=date(2026, 7, 7),
                end_date=date(2026, 7, 7),
                duration_days=1,
            ),
            observed_at=datetime(2026, 7, 3, tzinfo=UTC),
        )
        db.flush()

        signals = get_prompt_foresight_signals(
            db,
            user_id=user.id,
            today=date(2026, 7, 3),
            limit=1,
        )

    assert [signal.id for signal in signals] == [upcoming.id]


def test_prompt_foresight_prioritizes_dated_rows_over_undated_rows() -> None:
    from anima_server.services.agent.foresight import (
        ForesightCandidate,
        get_prompt_foresight_signals,
        upsert_foresight_signal,
    )

    with _db_session() as db:
        user = _make_user(db)
        for idx in range(20):
            upsert_foresight_signal(
                db,
                user_id=user.id,
                signal=ForesightCandidate(
                    content=f"User expects undated future outcome {idx}",
                    evidence=f"Undated future outcome {idx}.",
                    relative_text=None,
                    start_date=None,
                    end_date=None,
                    duration_days=None,
                ),
                observed_at=datetime(2026, 7, 3, tzinfo=UTC),
            )
        upcoming = upsert_foresight_signal(
            db,
            user_id=user.id,
            signal=ForesightCandidate(
                content="User has a product review next week",
                evidence="I have a product review next Tuesday.",
                relative_text="next Tuesday",
                start_date=date(2026, 7, 7),
                end_date=date(2026, 7, 7),
                duration_days=1,
            ),
            observed_at=datetime(2026, 7, 3, tzinfo=UTC),
        )
        db.flush()

        signals = get_prompt_foresight_signals(
            db,
            user_id=user.id,
            today=date(2026, 7, 3),
            limit=1,
        )

    assert [signal.id for signal in signals] == [upcoming.id]
