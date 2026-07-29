from __future__ import annotations

import hashlib
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
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session


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


def test_fresh_runtime_disk_contains_none_of_the_seeded_private_markers(
    managed_tmp_path: Path,
) -> None:
    markers = {
        "message": b"seeded message plaintext",
        "chunk": b"seeded chunk plaintext",
        "ocr": b"seeded OCR plaintext",
        "source_span": b"seeded source span plaintext",
        "memory_candidate": b"seeded candidate plaintext",
        "pending_memory_op": b"seeded pending-op plaintext",
        "preview": b"seeded preview plaintext",
        "vector": b"seeded vector plaintext",
    }
    runtime_file = managed_tmp_path / "fresh-runtime.db"
    engine = create_engine(f"sqlite+pysqlite:///{runtime_file.as_posix()}")
    RuntimeBase.metadata.create_all(engine, tables=[CoreFSSealedPayload.__table__])
    sealer = RuntimePayloadSealer()
    sealer.install(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")

    with Session(engine) as session:
        for row_id, (row_type, marker) in enumerate(markers.items(), start=1):
            aad = RuntimePayloadAAD(
                row_type=row_type,
                row_id=str(row_id),
                owner_id="owner-1",
            )
            sealed = sealer.seal(marker, aad=aad)
            session.add(
                CoreFSSealedPayload(
                    id=row_id,
                    core_id="core-a",
                    local_instance_id="instance-a",
                    row_type=row_type,
                    row_id_hash=hashlib.sha256(str(row_id).encode()).hexdigest(),
                    owner_id_hash=hashlib.sha256(b"owner-1").hexdigest(),
                    key_version=sealed.version,
                    nonce=sealed.nonce,
                    ciphertext=sealed.ciphertext,
                    aad_digest=hashlib.sha256(aad.encode()).hexdigest(),
                )
            )
        session.commit()
    engine.dispose()

    raw_runtime = runtime_file.read_bytes()
    for marker in markers.values():
        assert marker not in raw_runtime


def test_candidate_and_pending_operation_payloads_use_sealed_runtime_rows(
    monkeypatch,
) -> None:
    from anima_server.models.pending_memory_op import PendingMemoryOp
    from anima_server.models.runtime_memory import MemoryCandidate
    from anima_server.services.agent.candidate_ops import create_memory_candidate
    from anima_server.services.agent.pending_ops import create_pending_op
    from anima_server.services.corefs import sealed_runtime
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
    from conftest_runtime import runtime_db_session

    index = CoreFSProgressiveIndex("core-a")
    index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
    monkeypatch.setattr(
        sealed_runtime,
        "_active_runtime_index",
        lambda _user_id: index,
    )

    with runtime_db_session() as runtime_db:
        candidate = create_memory_candidate(
            runtime_db,
            user_id=7,
            content="seeded candidate plaintext",
            category="fact",
        )
        pending = create_pending_op(
            runtime_db,
            user_id=7,
            op_type="replace",
            target_block="human",
            content="seeded pending-op plaintext",
            old_content="seeded old pending-op plaintext",
            source_run_id=None,
            source_tool_call_id=None,
        )
        runtime_db.flush()

        assert candidate is not None
        assert (
            runtime_db.scalar(
                text("SELECT content FROM memory_candidates WHERE id = :id"),
                {"id": candidate.id},
            )
            == ""
        )
        stored_pending = runtime_db.execute(
            text("SELECT content, old_content FROM pending_memory_ops WHERE id = :id"),
            {"id": pending.id},
        ).one()
        assert stored_pending == ("", None)
        assert (
            runtime_db.scalar(
                select(CoreFSSealedPayload).where(
                    CoreFSSealedPayload.row_type == "memory_candidate"
                )
            )
            is not None
        )
        assert (
            runtime_db.scalar(
                select(CoreFSSealedPayload).where(
                    CoreFSSealedPayload.row_type == "pending_memory_op"
                )
            )
            is not None
        )

        runtime_db.expunge_all()
        loaded_candidate = runtime_db.scalar(
            select(MemoryCandidate).where(MemoryCandidate.id == candidate.id)
        )
        loaded_pending = runtime_db.scalar(
            select(PendingMemoryOp).where(PendingMemoryOp.id == pending.id)
        )
        assert loaded_candidate is not None
        assert loaded_candidate.content == "seeded candidate plaintext"
        assert loaded_pending is not None
        assert loaded_pending.content == "seeded pending-op plaintext"
        assert loaded_pending.old_content == "seeded old pending-op plaintext"

        index.clear_unlocked_state()
        runtime_db.expunge_all()
        with pytest.raises(RuntimeSealingLocked):
            runtime_db.scalar(select(PendingMemoryOp).where(PendingMemoryOp.id == pending.id))
