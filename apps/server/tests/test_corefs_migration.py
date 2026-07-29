from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
from anima_server.services.corefs.indexer import (
    CoreFSProgressiveIndex,
    IndexCapability,
    ReadinessState,
)
from anima_server.services.corefs.migration import reconcile_authenticated_catalog
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

SERVER_ROOT = Path(__file__).resolve().parents[1]
PRE_KEYSLOT_REVISION = "20260704_0001"


def _migrate(engine: Engine, revision: str, *, downgrade: bool = False) -> None:
    config = Config(str(SERVER_ROOT / "alembic_core.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        if downgrade:
            command.downgrade(config, revision)
        else:
            command.upgrade(config, revision)


def test_soul_keyslot_migration_roundtrips_without_touching_legacy_keys(
    managed_tmp_path: Path,
) -> None:
    database = managed_tmp_path / "corefs-keyslots-migration.db"
    engine = create_engine(f"sqlite:///{database.as_posix()}", future=True)

    _migrate(engine, "head")
    head_inspector = inspect(engine)
    assert head_inspector.has_table("soul_keyslots")
    unique_constraints = {
        constraint["name"]: constraint["column_names"]
        for constraint in head_inspector.get_unique_constraints("soul_keyslots")
    }
    assert unique_constraints["uq_soul_keyslots_identity"] == [
        "owner_id",
        "domain",
        "wrapping_path",
        "key_version",
        "credential_generation",
    ]
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (username, password_hash, display_name) "
                "VALUES ('legacy', 'hash', 'Legacy')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO user_keys "
                "(user_id, domain, kdf_salt, kdf_time_cost, kdf_memory_cost_kib, "
                "kdf_parallelism, kdf_key_length, wrap_iv, wrap_tag, wrapped_dek) "
                "VALUES (1, 'memories', 'salt', 3, 65536, 4, 32, 'iv', 'tag', 'dek')"
            )
        )

    _migrate(engine, PRE_KEYSLOT_REVISION, downgrade=True)
    inspector = inspect(engine)
    assert not inspector.has_table("soul_keyslots")
    assert inspector.has_table("user_keys")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT wrapped_dek FROM user_keys WHERE id = 1")) == "dek"

    _migrate(engine, "head")
    assert inspect(engine).has_table("soul_keyslots")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT wrapped_dek FROM user_keys WHERE id = 1")) == "dek"

    engine.dispose()


def test_reconciliation_authenticates_catalog_before_publishing_navigation() -> None:
    class NativeSession:
        def validation_snapshot(self, keys):
            assert keys is corefs_keys
            return {"generation": 7, "catalogHash": "catalog-hash"}

    corefs_keys = object()
    index = CoreFSProgressiveIndex("core-index")
    index.unlock(sqlcipher_key=b"s" * 32, local_instance_id="instance-a")
    session = SimpleNamespace(
        runtime_index=index,
        corefs_session=NativeSession(),
        corefs_keys=corefs_keys,
    )

    selected = reconcile_authenticated_catalog(session)
    snapshot = index.snapshot()

    assert selected.generation == 7
    assert snapshot.state is ReadinessState.CATALOG_READY
    assert snapshot.catalog_generation == 7
    assert snapshot.capabilities == frozenset(
        {IndexCapability.NAVIGATION, IndexCapability.EXACT_SEARCH}
    )
