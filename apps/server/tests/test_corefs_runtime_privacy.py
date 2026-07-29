from __future__ import annotations

from pathlib import Path

import pytest
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models.corefs_runtime import (
    CoreFSBlindToken,
    CoreFSIndexCheckpoint,
    CoreFSIndexEntry,
    CoreFSMigrationJournal,
    CoreFSSealedPayload,
)
from anima_server.services.corefs.runtime_sealing import (
    RuntimePayloadAAD,
    RuntimePayloadSealer,
    RuntimeSealingLocked,
)
from cryptography.exceptions import InvalidTag
from sqlalchemy import create_engine, inspect


def test_runtime_payload_sealing_is_instance_bound_and_contains_no_plaintext(
    managed_tmp_path: Path,
) -> None:
    marker = b"seeded pending operation plaintext"
    aad = RuntimePayloadAAD(
        row_type="pending_memory_op",
        row_id="op-1",
        owner_id="owner-1",
    )
    sealer = RuntimePayloadSealer()
    sealer.install(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")

    sealed = sealer.seal(marker, aad=aad)
    runtime_file = managed_tmp_path / "runtime" / "sealed-payload.bin"
    runtime_file.parent.mkdir()
    runtime_file.write_bytes(sealed.nonce + sealed.ciphertext)

    assert marker not in runtime_file.read_bytes()
    assert sealer.open(sealed, aad=aad) == marker

    another_instance = RuntimePayloadSealer()
    another_instance.install(
        sqlcipher_key=b"k" * 32,
        local_instance_id="instance-b",
    )
    with pytest.raises(InvalidTag):
        another_instance.open(sealed, aad=aad)


def test_runtime_payload_aad_prevents_row_or_owner_substitution() -> None:
    sealer = RuntimePayloadSealer()
    sealer.install(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
    sealed = sealer.seal(
        b"candidate payload",
        aad=RuntimePayloadAAD(
            row_type="memory_candidate",
            row_id="candidate-1",
            owner_id="owner-1",
        ),
    )

    with pytest.raises(InvalidTag):
        sealer.open(
            sealed,
            aad=RuntimePayloadAAD(
                row_type="memory_candidate",
                row_id="candidate-2",
                owner_id="owner-1",
            ),
        )
    with pytest.raises(InvalidTag):
        sealer.open(
            sealed,
            aad=RuntimePayloadAAD(
                row_type="memory_candidate",
                row_id="candidate-1",
                owner_id="owner-2",
            ),
        )


def test_runtime_sealing_key_is_unavailable_after_lock() -> None:
    sealer = RuntimePayloadSealer()
    sealer.install(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
    sealed = sealer.seal(
        b"pending operation",
        aad=RuntimePayloadAAD(
            row_type="pending_memory_op",
            row_id="op-1",
            owner_id="owner-1",
        ),
    )

    sealer.clear()

    assert sealer.installed is False
    with pytest.raises(RuntimeSealingLocked):
        sealer.open(
            sealed,
            aad=RuntimePayloadAAD(
                row_type="pending_memory_op",
                row_id="op-1",
                owner_id="owner-1",
            ),
        )


def test_corefs_runtime_tables_have_only_safe_or_sealed_payload_columns() -> None:
    forbidden_column_terms = {
        "body",
        "chunk",
        "content",
        "embedding",
        "ocr",
        "preview",
        "source_span",
        "text",
        "title",
        "vector",
    }
    expected_tables = {
        CoreFSIndexEntry.__tablename__,
        CoreFSIndexCheckpoint.__tablename__,
        CoreFSBlindToken.__tablename__,
        CoreFSMigrationJournal.__tablename__,
        CoreFSSealedPayload.__tablename__,
    }

    assert expected_tables == {
        "corefs_index_entries",
        "corefs_index_checkpoints",
        "corefs_blind_tokens",
        "corefs_migration_journal",
        "corefs_sealed_payloads",
    }
    for table_name in expected_tables:
        table = RuntimeBase.metadata.tables[table_name]
        for column in table.columns:
            normalized = column.name.casefold()
            if table_name == "corefs_sealed_payloads" and normalized == "ciphertext":
                continue
            assert not any(term in normalized for term in forbidden_column_terms)


def test_corefs_runtime_schema_builds_without_plaintext_search_columns() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    expected_tables = [
        CoreFSIndexEntry.__table__,
        CoreFSIndexCheckpoint.__table__,
        CoreFSBlindToken.__table__,
        CoreFSMigrationJournal.__table__,
        CoreFSSealedPayload.__table__,
    ]

    RuntimeBase.metadata.create_all(engine, tables=expected_tables)
    schema = inspect(engine)

    assert set(schema.get_table_names()) == {table.name for table in expected_tables}
    for table in expected_tables:
        columns = {column["name"] for column in schema.get_columns(table.name)}
        assert "content_text" not in columns
        assert "embedding" not in columns
        assert "preview" not in columns
