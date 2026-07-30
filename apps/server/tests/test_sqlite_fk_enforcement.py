"""Tests for MIH-001 — SQLite foreign-key enforcement on soul-store engines.

SQLite leaves FK constraints unenforced unless ``PRAGMA foreign_keys = ON``
is issued per connection. The codebase never issued it, so every
``ondelete="CASCADE"`` in the schema was decorative — the root cause behind
the whole family of orphaned-row findings hand-patched in PR #112 (vault
import claim/evidence, eval-reset tendency contributions, direct item-delete
orphans). These tests pin the pragma on every ``_make_engine`` SQLite path
and prove the cascades actually fire now.
"""

from __future__ import annotations

import pytest
from anima_server.db.base import Base
from anima_server.db.session import _make_engine
from anima_server.models import MemoryClaim, MemoryClaimEvidence, User
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def fk_engine(tmp_path):
    """A soul engine built by the REAL factory (plain-SQLite path in tests:
    conftest pins an empty passphrase), so the connect listener — including
    the MIH-001 pragma — is exactly what production connections run."""
    engine = _make_engine(f"sqlite:///{tmp_path / 'soul.db'}")
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


def test_make_engine_connections_enforce_foreign_keys(fk_engine) -> None:
    with fk_engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_ondelete_cascade_actually_fires(fk_engine) -> None:
    """The PR #112 family: deleting a MemoryClaim must cascade to its
    MemoryClaimEvidence rows instead of orphaning them."""
    factory = sessionmaker(bind=fk_engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        user = User(username="mih001", password_hash="x", display_name="MIH")
        db.add(user)
        db.flush()
        claim = MemoryClaim(
            user_id=user.id,
            namespace="fact",
            slot="occupation",
            value_text="sailor",
            canonical_key="user:fact:occupation",
            source_kind="extraction",
            extractor="test",
        )
        db.add(claim)
        db.flush()
        db.add(
            MemoryClaimEvidence(
                claim_id=claim.id, source_text="I work as a sailor", source_kind="user_message"
            )
        )
        db.commit()
        claim_id = claim.id

    with factory() as db:
        db.delete(db.get(MemoryClaim, claim_id))
        db.commit()
        assert db.scalars(select(MemoryClaimEvidence)).all() == []


def test_dangling_foreign_key_insert_is_rejected(fk_engine) -> None:
    """A child row referencing a missing parent now fails loudly instead of
    silently persisting as an orphan."""
    factory = sessionmaker(bind=fk_engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        db.add(
            MemoryClaimEvidence(
                claim_id=99_999, source_text="orphan", source_kind="user_message"
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()


def test_set_null_resurrection_is_why_delete_paths_walk_the_chain(fk_engine) -> None:
    """Characterization: with FKs enforced, deleting a superseding item fires
    superseded_by's ON DELETE SET NULL and the predecessor becomes an ACTIVE
    row again. This is exactly why both forget_memory and the direct-delete
    API route walk the supersession chain and delete predecessors too —
    deleting a memory deletes the fact, not 'roll back a version'."""
    from anima_server.models import MemoryItem

    factory = sessionmaker(bind=fk_engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        user = User(username="mih001b", password_hash="x", display_name="MIH")
        db.add(user)
        db.flush()
        old = MemoryItem(
            user_id=user.id, content="works as a designer", category="fact", importance=3
        )
        db.add(old)
        db.flush()
        new = MemoryItem(
            user_id=user.id, content="works as a PM", category="fact", importance=3
        )
        db.add(new)
        db.flush()
        old.superseded_by = new.id
        db.commit()
        old_id, new_id = old.id, new.id

    with factory() as db:
        db.delete(db.get(MemoryItem, new_id))
        db.commit()
        resurrected = db.get(MemoryItem, old_id)
        assert resurrected is not None
        assert resurrected.superseded_by is None  # SET NULL fired: active again


def _seed_row(conn, table: str, overrides: dict) -> None:
    """Insert one row supplying era-accurate values for every NOT NULL column
    of `table` AS THE CURRENT SCHEMA DEFINES IT — lets tests seed data at an
    intermediate Alembic revision without hardcoding historical schemas."""
    cols = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    vals = {}
    for _cid, name, ctype, notnull, default, pk in cols:
        if name in overrides:
            vals[name] = overrides[name]
            continue
        if pk:
            vals[name] = overrides.get(name, 1)
            continue
        if not notnull or default is not None:
            continue
        upper = (ctype or "").upper()
        if "INT" in upper:
            vals[name] = 1
        elif "REAL" in upper or "FLOA" in upper or "NUMER" in upper:
            vals[name] = 0.0
        elif "DATE" in upper or "TIME" in upper:
            vals[name] = "2026-01-01 00:00:00"
        else:
            vals[name] = "x"
    keys = ", ".join(vals)
    marks = ", ".join(["?"] * len(vals))
    conn.exec_driver_sql(f"INSERT INTO {table} ({keys}) VALUES ({marks})", tuple(vals.values()))


def test_batch_migrations_do_not_cascade_child_rows(tmp_path) -> None:
    """Regression (PR #132 review, P1): Alembic batch_alter rebuilds a table
    by copy-create-DROP-rename. With FK enforcement now ON at connect time,
    the DROP of the old parent fired ON DELETE CASCADE into its children —
    e.g. upgrading through 20260316_0002 (batch-rebuilds agent_runs)
    destroyed every agent_steps/agent_messages row. The migration runner
    must disable FKs for its transaction and re-enable them after."""
    from alembic import command
    from alembic.config import Config
    from anima_server.db.session import _ALEMBIC_INI, _run_alembic_upgrade

    engine = _make_engine(f"sqlite:///{tmp_path / 'mig.db'}")
    cfg = Config(str(_ALEMBIC_INI))

    # Build the schema as it existed BEFORE the batch rebuild...
    with engine.connect() as conn, conn.begin():
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "20260316_0001")
    # ...seed a full parent chain at that revision...
    with engine.begin() as conn:
        _seed_row(conn, "users", {"id": 1, "username": "mig", "password_hash": "x", "display_name": "M"})
        _seed_row(conn, "agent_threads", {"id": 1, "user_id": 1})
        _seed_row(conn, "agent_runs", {"id": 1, "user_id": 1, "thread_id": 1})
        _seed_row(conn, "agent_steps", {"id": 1, "run_id": 1})
        _seed_row(conn, "agent_messages", {"id": 1, "thread_id": 1, "run_id": 1})
    # ...and upgrade to head through the production runner.
    _run_alembic_upgrade(engine)

    with engine.connect() as conn:
        steps = conn.exec_driver_sql("SELECT COUNT(*) FROM agent_steps").scalar()
        messages = conn.exec_driver_sql("SELECT COUNT(*) FROM agent_messages").scalar()
        fk_state = conn.exec_driver_sql("PRAGMA foreign_keys").scalar()
    engine.dispose()
    assert steps == 1, "batch rebuild cascaded agent_steps away"
    assert messages == 1, "batch rebuild cascaded agent_messages away"
    assert fk_state == 1  # enforcement restored for the connection pool


def test_memories_scope_restore_preserves_user_owned_rows(fk_engine) -> None:
    """Regression (PR #132 review, P1): a memories-scope vault import
    bulk-deleted the users row; with FKs enforced its ON DELETE CASCADE
    executes IMMEDIATELY (defer_foreign_keys defers checks, not actions),
    destroying the very tables the scope promises to preserve. Scoped
    restores now merge user rows in place."""
    from anima_server.models import AgentThread, MemoryItem, Task
    from anima_server.services.vault import restore_database_snapshot

    factory = sessionmaker(bind=fk_engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        user = User(username="scoped", password_hash="x", display_name="Scoped")
        db.add(user)
        db.flush()
        db.add(AgentThread(user_id=user.id))
        db.add(Task(user_id=user.id, text="keep me"))
        db.add(
            MemoryItem(
                user_id=user.id, content="old memory", category="fact", importance=3
            )
        )
        db.commit()
        user_id = user.id

    snapshot = {
        "users": [
            {
                "id": user_id,
                "username": "scoped",
                "password_hash": "x",
                "display_name": "Scoped (restored)",
            }
        ],
        "userKeys": [],
        "memoryItems": [],
    }
    with factory() as db:
        restore_database_snapshot(db, snapshot, scope="memories")
        db.commit()

    with factory() as db:
        assert db.scalars(select(AgentThread)).all() != []  # preserved
        assert db.scalars(select(Task)).one().text == "keep me"  # preserved
        assert db.scalars(select(MemoryItem)).all() == []  # memories replaced
        restored = db.get(User, user_id)
        assert restored is not None
        assert restored.display_name == "Scoped (restored)"  # merged in place
