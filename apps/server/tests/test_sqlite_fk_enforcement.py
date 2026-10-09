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

from uuid import uuid4

import pytest
from anima_server.db.base import Base
from anima_server.db.session import _make_engine
from anima_server.models import MemoryClaim, MemoryClaimEvidence, SoulOwner
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
        user = SoulOwner(core_owner_id=str(uuid4()))
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
            MemoryClaimEvidence(claim_id=99_999, source_text="orphan", source_kind="user_message")
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
        user = SoulOwner(core_owner_id=str(uuid4()))
        db.add(user)
        db.flush()
        old = MemoryItem(
            user_id=user.id, content="works as a designer", category="fact", importance=3
        )
        db.add(old)
        db.flush()
        new = MemoryItem(user_id=user.id, content="works as a PM", category="fact", importance=3)
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


def test_supported_encrypted_soul_upgrade_preserves_retained_fk_graph_and_memory(
    managed_tmp_path,
) -> None:
    """Current native-keyslot owner upgrade preserves retained parent/child records.

    The supported old head is independently backed up before table rebuilds;
    foreign keys must be restored after the committed, verified encrypted upgrade.
    """
    from alembic import command
    from alembic.config import Config
    from anima_server.config import settings
    from anima_server.db import dispose_cached_engines
    from anima_server.db import session as soul_session
    from anima_server.db.user_store import authenticate_account
    from anima_server.services.corefs.soul_store import _hash_table
    from anima_server.services.data_crypto import df, ef
    from anima_server.services.sessions import clear_sqlcipher_key, unlock_session_store
    from conftest import managed_test_client

    with managed_test_client("supported-retained-fk-upgrade-") as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": "fk-owner", "password": "current-password", "name": "FK owner"},
        )
        assert registered.status_code == 201, registered.text
        owner_id = registered.json()["id"]
        canonical = soul_session.get_user_database_path(owner_id)
        with soul_session.get_engine(
            soul_session.get_user_database_url(owner_id)
        ).connect() as connection:
            slots = list(connection.exec_driver_sql("SELECT * FROM soul_keyslots").mappings())
        old_path = managed_tmp_path / "supported-old-fk-soul.db"
        engine = soul_session.get_engine(f"sqlite:///{old_path.as_posix()}")
        config = Config(str(soul_session._ALEMBIC_INI))
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "20260812_0001")
            _seed_row(
                connection,
                "users",
                {
                    "id": owner_id,
                    "username": "fk-owner",
                    "password_hash": "obsolete-verifier",
                    "display_name": "FK owner",
                },
            )
            for slot in slots:
                names = ",".join(slot)
                marks = ",".join("?" for _ in slot)
                connection.exec_driver_sql(
                    f"INSERT INTO soul_keyslots ({names}) VALUES ({marks})", tuple(slot.values())
                )
            _seed_row(
                connection,
                "memory_items",
                {
                    "id": 11,
                    "user_id": owner_id,
                    "content": ef(
                        owner_id, "Original retained fact", table="memory_items", field="content"
                    ),
                    "category": "fact",
                    "source": "test",
                },
            )
            _seed_row(
                connection,
                "memory_items",
                {
                    "id": 12,
                    "user_id": owner_id,
                    "content": ef(
                        owner_id, "Current retained fact", table="memory_items", field="content"
                    ),
                    "category": "fact",
                    "source": "test",
                },
            )
            connection.exec_driver_sql("UPDATE memory_items SET superseded_by=12 WHERE id=11")
            _seed_row(
                connection,
                "memory_claims",
                {
                    "id": 21,
                    "user_id": owner_id,
                    "namespace": "fact",
                    "slot": "occupation",
                    "canonical_key": "fk:occupation",
                    "value_text": ef(
                        owner_id, "Engineer", table="memory_claims", field="value_text"
                    ),
                    "source_kind": "extraction",
                },
            )
            _seed_row(
                connection,
                "memory_claim_evidence",
                {
                    "id": 31,
                    "claim_id": 21,
                    "source_text": ef(
                        owner_id,
                        "I work as an engineer",
                        table="memory_claim_evidence",
                        field="source_text",
                    ),
                    "source_kind": "user_message",
                },
            )
            _seed_row(
                connection,
                "memory_item_evidence",
                {
                    "id": 41,
                    "user_id": owner_id,
                    "memory_item_id": 12,
                    "source_kind": "extraction",
                    "source_text": ef(
                        owner_id,
                        "Source retained evidence",
                        table="memory_item_evidence",
                        field="source_text",
                    ),
                },
            )
            _seed_row(
                connection,
                "kg_entities",
                {
                    "id": 51,
                    "user_id": owner_id,
                    "name": "Owner",
                    "name_normalized": "owner",
                    "entity_type": "person",
                    "description": "Retained owner entity",
                },
            )
            _seed_row(
                connection,
                "kg_entities",
                {
                    "id": 52,
                    "user_id": owner_id,
                    "name": "Engineering",
                    "name_normalized": "engineering",
                    "entity_type": "discipline",
                    "description": "Retained discipline entity",
                },
            )
            _seed_row(
                connection,
                "kg_relations",
                {
                    "id": 61,
                    "user_id": owner_id,
                    "source_id": 51,
                    "destination_id": 52,
                    "relation_type": "works_in",
                    "source_memory_id": 12,
                    "evidence_id": 41,
                    "status": "active",
                },
            )
            _seed_row(
                connection,
                "user_profile_fields",
                {
                    "id": 71,
                    "user_id": owner_id,
                    "category": "work",
                    "key": "role",
                    "value": ef(owner_id, "Engineer", table="user_profile_fields", field="value"),
                    "source_memory_id": 12,
                    "source_evidence_id": 41,
                    "source_claim_evidence_id": 31,
                    "status": "active",
                },
            )
            tables = (
                "memory_items",
                "memory_claims",
                "memory_claim_evidence",
                "memory_item_evidence",
                "kg_entities",
                "kg_relations",
                "user_profile_fields",
            )
            original_counts = {
                name: connection.exec_driver_sql(f"SELECT COUNT(*) FROM {name}").scalar_one()
                for name in tables
            }
            stable_tables = (
                "memory_claims",
                "memory_claim_evidence",
                "kg_relations",
                "user_profile_fields",
            )
            original_hashes = {name: _hash_table(connection, name) for name in stable_tables}
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
        unlock_session_store.clear()
        clear_sqlcipher_key()
        dispose_cached_engines()
        canonical.unlink()
        for suffix in ("-wal", "-shm"):
            canonical.with_name(canonical.name + suffix).unlink(missing_ok=True)
        old_path.replace(canonical)
        assert canonical.read_bytes()[:16] != b"SQLite format 3\x00"
        profile, deks, _keys = authenticate_account("fk-owner", "current-password")
        assert profile["id"] == owner_id and deks
        token = unlock_session_store.create(owner_id, deks)
        try:
            upgraded = soul_session.get_engine(soul_session.get_user_database_url(owner_id))
            with upgraded.connect() as connection:
                assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
                assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
                assert {
                    name: connection.exec_driver_sql(f"SELECT COUNT(*) FROM {name}").scalar_one()
                    for name in tables
                } == original_counts
                assert {
                    name: _hash_table(connection, name) for name in stable_tables
                } == original_hashes
                rows = connection.exec_driver_sql(
                    "SELECT id,content,superseded_by FROM memory_items ORDER BY id"
                ).all()
                assert [
                    (
                        row.id,
                        df(owner_id, row.content, table="memory_items", field="content"),
                        row.superseded_by,
                    )
                    for row in rows
                ] == [(11, "Original retained fact", 12), (12, "Current retained fact", None)]
                receipt = connection.exec_driver_sql(
                    "SELECT id,vacuum_completed FROM soul_schema_archives"
                ).one()
                assert receipt.vacuum_completed == 1
            archive = (
                settings.data_dir.with_name(settings.data_dir.name + ".schema-recovery")
                / f"{receipt.id}.db"
            )
            assert archive.read_bytes()[:16] != b"SQLite format 3\x00"
            with soul_session.get_engine(f"sqlite:///{archive.as_posix()}").connect() as original:
                assert {
                    name: _hash_table(original, name) for name in stable_tables
                } == original_hashes
                assert {
                    name: original.exec_driver_sql(f"SELECT COUNT(*) FROM {name}").scalar_one()
                    for name in tables
                } == original_counts
            with upgraded.begin() as connection:
                connection.exec_driver_sql("DELETE FROM memory_claims WHERE id=21")
                assert (
                    connection.exec_driver_sql(
                        "SELECT COUNT(*) FROM memory_claim_evidence"
                    ).scalar_one()
                    == 0
                )
                assert (
                    connection.exec_driver_sql(
                        "SELECT source_claim_evidence_id FROM user_profile_fields WHERE id=71"
                    ).scalar_one()
                    is None
                )
                assert (
                    connection.exec_driver_sql("SELECT COUNT(*) FROM memory_items").scalar_one()
                    == 2
                )
                assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        finally:
            unlock_session_store.revoke(token)
