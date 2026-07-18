"""Tests for anima_server.db.helpers — shared session lifecycle (audit A-6)."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from anima_server.db.helpers import dual_session_scope, session_scope
from sqlalchemy import Column, Integer, String, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class _Base(DeclarativeBase):
    pass


class _Row(_Base):
    __tablename__ = "helper_test_rows"
    id = Column(Integer, primary_key=True)
    value = Column(String, nullable=False)


def _make_factory() -> tuple[Engine, sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _Base.metadata.create_all(bind=engine)
    factory = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    return engine, factory


@pytest.fixture()
def soul() -> Generator[tuple[Engine, sessionmaker[Session]], None, None]:
    engine, factory = _make_factory()
    yield engine, factory
    engine.dispose()


@pytest.fixture()
def runtime() -> Generator[tuple[Engine, sessionmaker[Session]], None, None]:
    engine, factory = _make_factory()
    yield engine, factory
    engine.dispose()


def _count(factory: sessionmaker[Session]) -> int:
    with factory() as s:
        return len(s.scalars(select(_Row)).all())


class TestSessionScope:
    def test_commits_on_clean_exit(self, soul) -> None:
        _, factory = soul
        with session_scope(factory) as db:
            db.add(_Row(value="kept"))
        assert _count(factory) == 1

    def test_rolls_back_and_reraises_on_exception(self, soul) -> None:
        _, factory = soul
        with pytest.raises(RuntimeError, match="boom"), session_scope(factory) as db:
            db.add(_Row(value="discarded"))
            raise RuntimeError("boom")
        assert _count(factory) == 0

    def test_session_closed_after_exit(self, soul) -> None:
        _, factory = soul
        with session_scope(factory) as db:
            held = db
        # A closed session re-opens a new connection transparently; assert
        # the transaction from the scope is gone instead.
        assert not held.in_transaction()


class TestDualSessionScope:
    def test_commits_both_on_clean_exit(self, soul, runtime) -> None:
        _, soul_factory = soul
        _, runtime_factory = runtime
        with dual_session_scope(soul_factory, runtime_factory) as (s, r):
            s.add(_Row(value="soul"))
            r.add(_Row(value="runtime"))
        assert _count(soul_factory) == 1
        assert _count(runtime_factory) == 1

    def test_body_exception_rolls_back_both(self, soul, runtime) -> None:
        _, soul_factory = soul
        _, runtime_factory = runtime
        with (
            pytest.raises(RuntimeError, match="boom"),
            dual_session_scope(soul_factory, runtime_factory) as (s, r),
        ):
            s.add(_Row(value="soul"))
            r.add(_Row(value="runtime"))
            raise RuntimeError("boom")
        assert _count(soul_factory) == 0
        assert _count(runtime_factory) == 0

    def test_soul_commit_failure_leaves_runtime_uncommitted(
        self, soul, runtime, monkeypatch
    ) -> None:
        """Ordering rule: soul commits first. If it fails, runtime must not commit."""
        _, soul_factory = soul
        _, runtime_factory = runtime
        with (
            pytest.raises(RuntimeError, match="soul-commit-fail"),
            dual_session_scope(soul_factory, runtime_factory) as (s, r),
        ):
            s.add(_Row(value="soul"))
            r.add(_Row(value="runtime"))
            monkeypatch.setattr(
                s,
                "commit",
                lambda: (_ for _ in ()).throw(RuntimeError("soul-commit-fail")),
            )
        assert _count(soul_factory) == 0
        assert _count(runtime_factory) == 0

    def test_runtime_commit_failure_preserves_soul_commit(
        self, soul, runtime, monkeypatch
    ) -> None:
        """At-least-once semantics: soul data is durable; runtime re-stages on retry."""
        _, soul_factory = soul
        _, runtime_factory = runtime
        with (
            pytest.raises(RuntimeError, match="runtime-commit-fail"),
            dual_session_scope(soul_factory, runtime_factory) as (s, r),
        ):
            s.add(_Row(value="soul"))
            r.add(_Row(value="runtime"))
            monkeypatch.setattr(
                r,
                "commit",
                lambda: (_ for _ in ()).throw(RuntimeError("runtime-commit-fail")),
            )
        assert _count(soul_factory) == 1  # soul committed before runtime failed
        assert _count(runtime_factory) == 0

    def test_both_sessions_closed_after_exit(self, soul, runtime) -> None:
        _, soul_factory = soul
        _, runtime_factory = runtime
        with dual_session_scope(soul_factory, runtime_factory) as (s, r):
            held = (s, r)
        assert not held[0].in_transaction()
        assert not held[1].in_transaction()
