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
    CoreFSRuntimeBinding,
    CoreFSSealedPayload,
)
from anima_server.services.corefs.runtime_sealing import (
    RuntimePayloadAAD,
    RuntimePayloadSealer,
    RuntimeSealingLocked,
)
from cryptography.exceptions import InvalidTag
from sqlalchemy import create_engine, event, inspect, select, text
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
        CoreFSRuntimeBinding.__tablename__,
        CoreFSSealedPayload.__tablename__,
    }

    assert expected_tables == {
        "corefs_index_entries",
        "corefs_index_checkpoints",
        "corefs_blind_tokens",
        "corefs_migration_journal",
        "corefs_runtime_binding",
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
        CoreFSRuntimeBinding.__table__,
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


def test_production_document_image_and_source_writers_seal_private_text(
    monkeypatch,
) -> None:
    from anima_server.models.runtime import (
        RuntimeDocumentChunk,
        RuntimeImageAnnotation,
        RuntimeImageAsset,
        RuntimeSource,
        RuntimeSourceArtifact,
        RuntimeSourceSpan,
    )
    from anima_server.services.corefs import sealed_runtime
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
    from anima_server.services.documents import contextual
    from anima_server.services.documents.models import (
        DocumentRegistration,
        ExtractedDocumentChunk,
    )
    from anima_server.services.documents.store import (
        register_document,
        replace_document_chunks,
    )
    from anima_server.services.images.indexing import _upsert_active_annotation
    from anima_server.services.ingestion.artifacts import (
        replace_source_artifacts_and_spans,
    )
    from anima_server.services.ingestion.models import (
        SourceArtifactInput,
        SourceSpanInput,
    )
    from conftest_runtime import runtime_db_session

    index = CoreFSProgressiveIndex("core-a")
    index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
    monkeypatch.setattr(
        sealed_runtime,
        "_active_runtime_index",
        lambda _user_id: index,
    )

    document_marker = "production document chunk marker"
    document_section_marker = "production private section marker"
    document_metadata_marker = "production document metadata marker"
    contextual_marker = "production contextual blurb marker"
    image_marker = "production OCR annotation marker"
    artifact_marker = "production source artifact marker"
    artifact_metadata_marker = "production artifact metadata marker"
    span_marker = "production source span marker"
    span_metadata_marker = "production span metadata marker"

    with runtime_db_session() as runtime_db:
        executed_parameters: list[object] = []

        def capture_parameters(
            _connection,
            _cursor,
            _statement,
            parameters,
            _context,
            _executemany,
        ) -> None:
            executed_parameters.append(parameters)

        event.listen(runtime_db.bind, "before_cursor_execute", capture_parameters)
        document = register_document(
            runtime_db,
            DocumentRegistration(
                user_id=1,
                filename="private.pdf",
                mime_type="application/pdf",
                storage_path="corefs://private.pdf",
                sha256="a" * 64,
                size_bytes=128,
            ),
        )
        document_chunks = replace_document_chunks(
            runtime_db,
            document_id=document.id,
            chunks=[
                ExtractedDocumentChunk(
                    chunk_index=0,
                    content_text=document_marker,
                    page_start=1,
                    page_end=1,
                    section_title=document_section_marker,
                    token_count=4,
                    metadata_json={"outline": document_metadata_marker},
                )
            ],
            parse_quality="native",
        )

        async def generate_blurb(**_kwargs):
            return [(document_chunks[0].id, contextual_marker)]

        monkeypatch.setattr(contextual.settings, "contextual_chunks", "on")
        monkeypatch.setattr(contextual, "_generate_blurbs", generate_blurb)
        assert (
            contextual.generate_document_chunk_blurbs(
                runtime_db,
                user_id=1,
                document_id=document.id,
            )
            == 1
        )

        image = RuntimeImageAsset(
            user_id=1,
            filename="private.png",
            mime_type="image/png",
            storage_path="corefs://private.png",
            sha256="b" * 64,
            size_bytes=64,
        )
        runtime_db.add(image)
        runtime_db.flush()
        _upsert_active_annotation(
            runtime_db,
            user_id=1,
            image_asset_id=image.id,
            annotation_kind="ocr_text",
            content_text=image_marker,
            source_model=None,
        )

        source = RuntimeSource(
            user_id=1,
            kind="document",
            source_uri="corefs://private-source",
            content_hash="c" * 64,
            title="Private source",
            status="registered",
        )
        runtime_db.add(source)
        runtime_db.flush()
        replace_source_artifacts_and_spans(
            runtime_db,
            source=source,
            artifacts=[
                SourceArtifactInput(
                    artifact_kind="plain_text",
                    content_text=artifact_marker,
                    content_hash="d" * 64,
                    metadata_json={"outline": artifact_metadata_marker},
                )
            ],
            spans=[
                SourceSpanInput(
                    artifact_kind="plain_text",
                    span_kind="paragraph",
                    locator_json={"paragraph_index": 0},
                    content_text=span_marker,
                    content_hash="e" * 64,
                    metadata_json={"section_path": span_metadata_marker},
                )
            ],
        )
        event.remove(runtime_db.bind, "before_cursor_execute", capture_parameters)

        raw_document = runtime_db.execute(
            text("SELECT content_text, section_title, metadata_json FROM runtime_document_chunks")
        ).one()
        raw_image = runtime_db.execute(
            text("SELECT content_text FROM runtime_image_annotations")
        ).scalar_one()
        raw_artifact = runtime_db.execute(
            text("SELECT content_text, metadata_json FROM runtime_source_artifacts")
        ).one()
        raw_span = runtime_db.execute(
            text("SELECT content_text, metadata_json FROM runtime_source_spans")
        ).one()
        row_types = set(runtime_db.scalars(select(CoreFSSealedPayload.row_type)).all())
        runtime_db.expunge_all()
        hydrated_document = runtime_db.scalar(select(RuntimeDocumentChunk))
        hydrated_image = runtime_db.scalar(select(RuntimeImageAnnotation))
        hydrated_artifact = runtime_db.scalar(select(RuntimeSourceArtifact))
        hydrated_span = runtime_db.scalar(select(RuntimeSourceSpan))

    captured = repr(executed_parameters)
    for marker in (
        document_marker,
        document_section_marker,
        document_metadata_marker,
        contextual_marker,
        image_marker,
        artifact_marker,
        artifact_metadata_marker,
        span_marker,
        span_metadata_marker,
    ):
        assert marker not in captured
    assert raw_document[0] == ""
    assert raw_document[1] is None
    assert raw_document[2] in (None, "null")
    assert raw_image == ""
    assert raw_artifact[0] is None
    assert raw_artifact[1] in (None, "null")
    assert raw_span[0] == ""
    assert raw_span[1] in (None, "null")
    assert hydrated_document is not None
    assert hydrated_document.content_text == document_marker
    assert hydrated_document.section_title == document_section_marker
    assert hydrated_document.metadata_json == {
        "outline": document_metadata_marker,
        contextual.CONTEXT_BLURB_METADATA_KEY: contextual_marker,
    }
    assert hydrated_image is not None
    assert hydrated_image.content_text == image_marker
    assert hydrated_artifact is not None
    assert hydrated_artifact.content_text == artifact_marker
    assert hydrated_artifact.metadata_json == {"outline": artifact_metadata_marker}
    assert hydrated_span is not None
    assert hydrated_span.content_text == span_marker
    assert hydrated_span.metadata_json == {"section_path": span_metadata_marker}
    assert {
        "runtime_document_chunk",
        "runtime_image_annotation",
        "runtime_source_artifact",
        "runtime_source_span",
    }.issubset(row_types)


def test_document_chunk_replacement_deletes_superseded_sealed_payload(
    monkeypatch,
) -> None:
    from anima_server.services.corefs import sealed_runtime
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
    from anima_server.services.documents.models import (
        DocumentRegistration,
        ExtractedDocumentChunk,
    )
    from anima_server.services.documents.store import (
        register_document,
        replace_document_chunks,
    )
    from conftest_runtime import runtime_db_session
    from sqlalchemy import func

    index = CoreFSProgressiveIndex("core-a")
    index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
    monkeypatch.setattr(
        sealed_runtime,
        "_active_runtime_index",
        lambda _user_id: index,
    )

    with runtime_db_session() as runtime_db:
        document = register_document(
            runtime_db,
            DocumentRegistration(
                user_id=1,
                filename="private.pdf",
                mime_type="application/pdf",
                storage_path="corefs://private.pdf",
                sha256="a" * 64,
                size_bytes=128,
            ),
        )
        replace_document_chunks(
            runtime_db,
            document_id=document.id,
            chunks=[ExtractedDocumentChunk(chunk_index=0, content_text="old private")],
            parse_quality="native",
        )
        sentinel = register_document(
            runtime_db,
            DocumentRegistration(
                user_id=1,
                filename="sentinel.pdf",
                mime_type="application/pdf",
                storage_path="corefs://sentinel.pdf",
                sha256="b" * 64,
                size_bytes=64,
            ),
        )
        replace_document_chunks(
            runtime_db,
            document_id=sentinel.id,
            chunks=[ExtractedDocumentChunk(chunk_index=0, content_text="sentinel")],
            parse_quality="native",
        )
        replace_document_chunks(
            runtime_db,
            document_id=document.id,
            chunks=[ExtractedDocumentChunk(chunk_index=0, content_text="new private")],
            parse_quality="native",
        )

        sealed_count = runtime_db.scalar(
            select(func.count(CoreFSSealedPayload.id)).where(
                CoreFSSealedPayload.row_type == "runtime_document_chunk"
            )
        )

    assert sealed_count == 2


def test_message_pruning_deletes_expired_sealed_payload(
    monkeypatch,
) -> None:
    import asyncio
    from datetime import UTC, datetime, timedelta

    from anima_server.models.runtime import RuntimeMessage
    from anima_server.services.agent.eager_consolidation import prune_expired_messages
    from anima_server.services.agent.persistence import append_message, get_or_create_thread
    from anima_server.services.corefs import sealed_runtime
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
    from conftest_runtime import runtime_db_session
    from sqlalchemy import func
    from sqlalchemy.orm import sessionmaker

    index = CoreFSProgressiveIndex("core-a")
    index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
    monkeypatch.setattr(
        sealed_runtime,
        "_active_runtime_index",
        lambda _user_id: index,
    )

    with runtime_db_session() as runtime_db:
        thread = get_or_create_thread(runtime_db, user_id=7)
        thread.status = "closed"
        thread.is_archived = True
        message = append_message(
            runtime_db,
            thread=thread,
            run_id=None,
            step_id=None,
            sequence_id=1,
            role="user",
            content_text="expired private message",
        )
        message.created_at = datetime.now(UTC) - timedelta(days=60)
        runtime_db.commit()
        factory = sessionmaker(
            bind=runtime_db.get_bind(),
            autoflush=False,
            expire_on_commit=False,
        )
        monkeypatch.setattr(
            "anima_server.services.agent.eager_consolidation.settings.message_ttl_days",
            30,
        )

        deleted = asyncio.run(prune_expired_messages(runtime_db_factory=factory))
        with factory() as verification_db:
            message_count = verification_db.scalar(select(func.count(RuntimeMessage.id)))
            sealed_count = verification_db.scalar(
                select(func.count(CoreFSSealedPayload.id)).where(
                    CoreFSSealedPayload.row_type == "runtime_message"
                )
            )

    assert deleted == 1
    assert message_count == 0
    assert sealed_count == 0


def test_source_replacement_and_image_forgetting_delete_sealed_payloads(
    monkeypatch,
) -> None:
    from anima_server.models.runtime import RuntimeImageAsset, RuntimeSource
    from anima_server.services.corefs import sealed_runtime
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
    from anima_server.services.images import deletion as image_deletion
    from anima_server.services.images.indexing import _upsert_active_annotation
    from anima_server.services.ingestion.artifacts import (
        replace_source_artifacts_and_spans,
    )
    from anima_server.services.ingestion.models import (
        SourceArtifactInput,
        SourceSpanInput,
    )
    from conftest_runtime import runtime_db_session
    from sqlalchemy import func

    index = CoreFSProgressiveIndex("core-a")
    index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
    monkeypatch.setattr(
        sealed_runtime,
        "_active_runtime_index",
        lambda _user_id: index,
    )
    monkeypatch.setattr(
        image_deletion,
        "delete_image_asset_file_if_safe",
        lambda _asset: False,
    )

    with runtime_db_session() as runtime_db:
        source = RuntimeSource(
            user_id=1,
            kind="document",
            source_uri="corefs://private-source",
            content_hash="a" * 64,
            title="Private source",
            status="registered",
        )
        runtime_db.add(source)
        runtime_db.flush()
        replace_source_artifacts_and_spans(
            runtime_db,
            source=source,
            artifacts=[
                SourceArtifactInput(
                    artifact_kind="plain_text",
                    content_text="old artifact",
                    content_hash="b" * 64,
                )
            ],
            spans=[
                SourceSpanInput(
                    artifact_kind="plain_text",
                    span_kind="paragraph",
                    locator_json={"paragraph_index": 0},
                    content_text="old span",
                    content_hash="c" * 64,
                )
            ],
        )
        replace_source_artifacts_and_spans(
            runtime_db,
            source=source,
            artifacts=[
                SourceArtifactInput(
                    artifact_kind="plain_text",
                    content_text="new artifact",
                    content_hash="d" * 64,
                )
            ],
            spans=[
                SourceSpanInput(
                    artifact_kind="plain_text",
                    span_kind="paragraph",
                    locator_json={"paragraph_index": 0},
                    content_text="new span",
                    content_hash="e" * 64,
                )
            ],
        )

        image = RuntimeImageAsset(
            user_id=1,
            filename="private.png",
            mime_type="image/png",
            storage_path="corefs://private.png",
            sha256="f" * 64,
            size_bytes=64,
        )
        runtime_db.add(image)
        runtime_db.flush()
        _upsert_active_annotation(
            runtime_db,
            user_id=1,
            image_asset_id=image.id,
            annotation_kind="ocr_text",
            content_text="private OCR",
            source_model=None,
        )
        assert image_deletion.forget_image_asset(
            runtime_db,
            user_id=1,
            image_asset_id=image.id,
        ).forgotten

        counts = {
            row_type: runtime_db.scalar(
                select(func.count(CoreFSSealedPayload.id)).where(
                    CoreFSSealedPayload.row_type == row_type
                )
            )
            for row_type in (
                "runtime_source_artifact",
                "runtime_source_span",
                "runtime_image_annotation",
            )
        }

    assert counts == {
        "runtime_source_artifact": 1,
        "runtime_source_span": 1,
        "runtime_image_annotation": 0,
    }


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


def test_memory_extraction_retry_previews_use_sealed_runtime_rows(
    monkeypatch,
) -> None:
    from anima_server.models.runtime_memory import MemoryExtractionFailure
    from anima_server.services.agent.consolidation import (
        record_memory_extraction_failure,
    )
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
        failure = record_memory_extraction_failure(
            runtime_db,
            user_id=7,
            source_message_ids=[101, 102],
            user_message="seeded extraction user preview",
            assistant_response="seeded extraction assistant preview",
            failure_reason="temporary provider failure",
            status="pending",
        )

        stored = runtime_db.execute(
            text(
                "SELECT user_message_preview, assistant_response_preview "
                "FROM memory_extraction_failures WHERE id = :id"
            ),
            {"id": failure.id},
        ).one()
        assert stored == (None, None)
        assert (
            runtime_db.scalar(
                select(CoreFSSealedPayload).where(
                    CoreFSSealedPayload.row_type == "memory_extraction_failure"
                )
            )
            is not None
        )

        runtime_db.expunge_all()
        loaded = runtime_db.scalar(
            select(MemoryExtractionFailure).where(MemoryExtractionFailure.id == failure.id)
        )
        assert loaded is not None
        assert loaded.user_message_preview == "seeded extraction user preview"
        assert loaded.assistant_response_preview == "seeded extraction assistant preview"

        index.clear_unlocked_state()
        runtime_db.expunge_all()
        with pytest.raises(RuntimeSealingLocked):
            runtime_db.scalar(
                select(MemoryExtractionFailure).where(MemoryExtractionFailure.id == failure.id)
            )


def test_profile_update_candidates_use_sealed_runtime_rows(
    monkeypatch,
) -> None:
    from anima_server.models.runtime_memory import ProfileUpdateCandidate
    from anima_server.services.agent.user_profile import (
        create_profile_update_candidate,
    )
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
        candidate = create_profile_update_candidate(
            runtime_db,
            user_id=7,
            category="identity",
            key="favorite_project",
            value="seeded private profile value",
            evidence_text="seeded private profile evidence",
            source_message_ids=[101],
        )

        assert candidate is not None
        stored = runtime_db.execute(
            text("SELECT value, evidence_text FROM profile_update_candidates WHERE id = :id"),
            {"id": candidate.id},
        ).one()
        assert stored == ("", None)
        assert (
            runtime_db.scalar(
                select(CoreFSSealedPayload).where(
                    CoreFSSealedPayload.row_type == "profile_update_candidate"
                )
            )
            is not None
        )

        runtime_db.expunge_all()
        loaded = runtime_db.scalar(
            select(ProfileUpdateCandidate).where(ProfileUpdateCandidate.id == candidate.id)
        )
        assert loaded is not None
        assert loaded.value == "seeded private profile value"
        assert loaded.evidence_text == "seeded private profile evidence"

        index.clear_unlocked_state()
        runtime_db.expunge_all()
        with pytest.raises(RuntimeSealingLocked):
            runtime_db.scalar(
                select(ProfileUpdateCandidate).where(ProfileUpdateCandidate.id == candidate.id)
            )


def test_runtime_session_notes_use_sealed_rows_and_reseal_updates(
    monkeypatch,
) -> None:
    from anima_server.models.runtime_memory import RuntimeSessionNote
    from anima_server.services.agent.persistence import get_or_create_thread
    from anima_server.services.agent.session_memory import (
        get_session_notes,
        write_session_note,
    )
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
        thread = get_or_create_thread(runtime_db, user_id=7)
        note = write_session_note(
            runtime_db,
            thread_id=thread.id,
            user_id=7,
            key="seeded private session key",
            value="seeded private session value",
            note_type="context",
        )
        updated = write_session_note(
            runtime_db,
            thread_id=thread.id,
            user_id=7,
            key="seeded private session key",
            value="seeded updated session value",
            note_type="plan",
        )

        assert updated.id == note.id
        stored = runtime_db.execute(
            text("SELECT key, value FROM runtime_session_notes WHERE id = :id"),
            {"id": note.id},
        ).one()
        assert stored == ("", "")
        assert (
            runtime_db.scalar(
                select(CoreFSSealedPayload).where(
                    CoreFSSealedPayload.row_type == "runtime_session_note"
                )
            )
            is not None
        )

        runtime_db.expunge_all()
        loaded = get_session_notes(
            runtime_db,
            thread_id=thread.id,
        )
        assert len(loaded) == 1
        assert loaded[0].key == "seeded private session key"
        assert loaded[0].value == "seeded updated session value"
        assert loaded[0].note_type == "plan"

        index.clear_unlocked_state()
        runtime_db.expunge_all()
        with pytest.raises(RuntimeSealingLocked):
            runtime_db.scalar(select(RuntimeSessionNote).where(RuntimeSessionNote.id == note.id))


def test_compaction_and_archive_runtime_message_writers_are_sealed(
    monkeypatch,
) -> None:
    from anima_server.models.runtime import RuntimeMessage
    from anima_server.services.agent.compaction import compact_thread_context
    from anima_server.services.agent.persistence import (
        append_message,
        get_or_create_thread,
    )
    from anima_server.services.agent.sequencing import reserve_message_sequences
    from anima_server.services.agent.thread_manager import (
        _bulk_insert_archived_history,
        _insert_summary_message,
    )
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
        thread = get_or_create_thread(runtime_db, user_id=7)
        for role, content in (
            ("user", "seeded compacted user plaintext"),
            ("assistant", "seeded compacted assistant plaintext"),
            ("user", "seeded retained user plaintext"),
        ):
            sequence_id = reserve_message_sequences(
                runtime_db,
                thread_id=thread.id,
                count=1,
            )
            append_message(
                runtime_db,
                thread=thread,
                run_id=None,
                step_id=None,
                sequence_id=sequence_id,
                role=role,
                content_text=content,
            )

        result = compact_thread_context(
            runtime_db,
            thread=thread,
            run_id=None,
            trigger_token_limit=1,
            keep_last_messages=1,
        )
        assert result is not None
        _bulk_insert_archived_history(
            runtime_db,
            thread=thread,
            user_id=7,
            messages=[
                {
                    "role": "user",
                    "content": "seeded archived message plaintext",
                }
            ],
        )
        _insert_summary_message(
            runtime_db,
            thread=thread,
            user_id=7,
            summary="seeded archived summary plaintext",
        )

        stored = runtime_db.execute(
            text("SELECT content_text, content_json, tool_args_json FROM runtime_messages")
        ).all()
        raw = " ".join(str(value) for row in stored for value in row)
        assert "seeded compacted user plaintext" not in raw
        assert "seeded compacted assistant plaintext" not in raw
        assert "seeded archived message plaintext" not in raw
        assert "seeded archived summary plaintext" not in raw

        runtime_db.expunge_all()
        loaded = runtime_db.scalars(
            select(RuntimeMessage).where(RuntimeMessage.thread_id == thread.id)
        ).all()
        loaded_text = "\n".join(message.content_text or "" for message in loaded)
        assert "seeded compacted user plaintext" in loaded_text
        assert "seeded archived message plaintext" in loaded_text
        assert "seeded archived summary plaintext" in loaded_text


def test_runtime_message_writer_seals_private_fields_in_corefs_runtime(
    monkeypatch,
) -> None:
    from anima_server.models.runtime import RuntimeMessage
    from anima_server.services.agent.persistence import (
        append_message,
        get_or_create_thread,
    )
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
        thread = get_or_create_thread(runtime_db, user_id=7)
        message = append_message(
            runtime_db,
            thread=thread,
            run_id=None,
            step_id=None,
            sequence_id=1,
            role="tool",
            content_text="seeded message plaintext",
            content_json={"private": "seeded message json"},
            tool_name="private-tool",
            tool_call_id="call-1",
            tool_args_json={"secret": "seeded tool arguments"},
        )
        runtime_db.flush()

        stored = runtime_db.execute(
            text(
                "SELECT content_text, content_json, tool_args_json "
                "FROM runtime_messages WHERE id = :id"
            ),
            {"id": message.id},
        ).one()
        assert stored == (None, "null", "null")
        assert (
            runtime_db.scalar(
                select(CoreFSSealedPayload).where(CoreFSSealedPayload.row_type == "runtime_message")
            )
            is not None
        )

        runtime_db.expunge_all()
        loaded = runtime_db.scalar(select(RuntimeMessage).where(RuntimeMessage.id == message.id))
        assert loaded is not None
        assert loaded.content_text == "seeded message plaintext"
        assert loaded.content_json == {"private": "seeded message json"}
        assert loaded.tool_args_json == {"secret": "seeded tool arguments"}

        index.clear_unlocked_state()
        runtime_db.expunge_all()
        with pytest.raises(RuntimeSealingLocked):
            runtime_db.scalar(select(RuntimeMessage).where(RuntimeMessage.id == message.id))


def test_sealed_runtime_messages_remain_queryable_and_reseal_mutations(
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    from anima_server.models.runtime import RuntimeMessage
    from anima_server.services.agent.conversation_search import _search_messages
    from anima_server.services.agent.persistence import (
        append_message,
        get_or_create_thread,
        list_transcript_messages,
    )
    from anima_server.services.agent.service import (
        _remove_failed_turn_attachment_metadata,
    )
    from anima_server.services.agent.tools import _latest_user_source_message_ids
    from anima_server.services.corefs import sealed_runtime
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
    from anima_server.services.images.deletion import _remove_image_source_pills
    from conftest_runtime import runtime_db_session

    index = CoreFSProgressiveIndex("core-a")
    index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
    monkeypatch.setattr(
        sealed_runtime,
        "_active_runtime_index",
        lambda _user_id: index,
    )

    with runtime_db_session() as runtime_db:
        thread = get_or_create_thread(runtime_db, user_id=7)
        message = append_message(
            runtime_db,
            thread=thread,
            run_id=None,
            step_id=None,
            sequence_id=1,
            role="user",
            content_text="sealed searchable message",
            content_json={
                "attachments": [
                    {
                        "id": "attachment-1",
                        "assetId": 11,
                        "kind": "image",
                    }
                ],
                "pills": [
                    {
                        "kind": "image_source",
                        "ref": "attachment-1",
                    }
                ],
                "private": "preserved",
            },
        )

        transcript = list_transcript_messages(
            runtime_db,
            user_id=7,
            limit=10,
        )
        assert [item.id for item in transcript] == [message.id]
        hits = _search_messages(
            runtime_db,
            user_id=7,
            query_lower="sealed searchable",
            role_filter="user",
            parsed_start=None,
            parsed_end=None,
        )
        assert [hit.content for hit in hits] == ["sealed searchable message"]
        assert _latest_user_source_message_ids(
            SimpleNamespace(
                runtime_db=runtime_db,
                thread_id=thread.id,
                user_id=7,
            )
        ) == [message.id]

        sealed_before = runtime_db.scalar(
            select(CoreFSSealedPayload).where(CoreFSSealedPayload.row_type == "runtime_message")
        )
        assert sealed_before is not None
        ciphertext_before = bytes(sealed_before.ciphertext)

        _remove_failed_turn_attachment_metadata(
            runtime_db,
            message,
            attachment_ids={"attachment-1"},
            image_asset_ids={11},
        )
        _remove_image_source_pills(
            runtime_db,
            user_id=7,
            image_asset_id=None,
            attachment_ids={"attachment-1"},
        )
        runtime_db.flush()

        stored = runtime_db.execute(
            text(
                "SELECT content_text, content_json, tool_args_json "
                "FROM runtime_messages WHERE id = :id"
            ),
            {"id": message.id},
        ).one()
        assert stored == (None, "null", "null")

        runtime_db.expunge_all()
        sealed_after = runtime_db.scalar(
            select(CoreFSSealedPayload).where(CoreFSSealedPayload.row_type == "runtime_message")
        )
        assert sealed_after is not None
        assert bytes(sealed_after.ciphertext) != ciphertext_before

        runtime_db.expunge_all()
        loaded = runtime_db.scalar(select(RuntimeMessage).where(RuntimeMessage.id == message.id))
        assert loaded is not None
        assert loaded.content_text == "sealed searchable message"
        assert loaded.content_json == {
            "pills": [],
            "private": "preserved",
        }


def test_runtime_step_writer_seals_duplicate_trace_payloads(
    monkeypatch,
) -> None:
    from anima_server.models.runtime import RuntimeStep
    from anima_server.services.agent.persistence import (
        create_run,
        create_step,
        get_or_create_thread,
    )
    from anima_server.services.agent.runtime_types import (
        MessageSnapshot,
        StepTrace,
        ToolCall,
        ToolExecutionResult,
    )
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
        thread = get_or_create_thread(runtime_db, user_id=7)
        run = create_run(
            runtime_db,
            thread_id=thread.id,
            user_id=7,
            provider="test",
            model="test",
            mode="chat",
        )
        step = create_step(
            runtime_db,
            thread_id=thread.id,
            run_id=run.id,
            trace=StepTrace(
                step_index=0,
                request_messages=(
                    MessageSnapshot(
                        role="user",
                        content="seeded step request plaintext",
                    ),
                ),
                assistant_text="seeded step response plaintext",
                tool_calls=(
                    ToolCall(
                        id="call-1",
                        name="private-tool",
                        arguments={"secret": "seeded step arguments"},
                    ),
                ),
                tool_results=(
                    ToolExecutionResult(
                        call_id="call-1",
                        name="private-tool",
                        output="seeded step tool output",
                    ),
                ),
            ),
        )
        runtime_db.flush()

        stored = runtime_db.execute(
            text(
                "SELECT request_json, response_json, tool_calls_json "
                "FROM runtime_steps WHERE id = :id"
            ),
            {"id": step.id},
        ).one()
        raw = " ".join(str(value) for value in stored)
        assert "seeded step request plaintext" not in raw
        assert "seeded step response plaintext" not in raw
        assert "seeded step arguments" not in raw
        assert "seeded step tool output" not in raw
        assert (
            runtime_db.scalar(
                select(CoreFSSealedPayload).where(CoreFSSealedPayload.row_type == "runtime_step")
            )
            is not None
        )

        runtime_db.expunge_all()
        loaded = runtime_db.scalar(select(RuntimeStep).where(RuntimeStep.id == step.id))
        assert loaded is not None
        assert loaded.request_json["messages"][0]["content"] == ("seeded step request plaintext")
        assert loaded.response_json["assistant_text"] == ("seeded step response plaintext")
        assert loaded.tool_calls_json is not None
        assert loaded.tool_calls_json[0]["arguments"] == {"secret": "seeded step arguments"}


def test_thread_deletion_removes_its_sealed_runtime_payloads(
    monkeypatch,
) -> None:
    from anima_server.services.agent.persistence import (
        append_message,
        get_or_create_thread,
    )
    from anima_server.services.agent.session_memory import write_session_note
    from anima_server.services.corefs import sealed_runtime
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
    from anima_server.services.images.deletion import delete_thread_with_image_cleanup
    from conftest_runtime import runtime_db_session

    index = CoreFSProgressiveIndex("core-a")
    index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
    monkeypatch.setattr(
        sealed_runtime,
        "_active_runtime_index",
        lambda _user_id: index,
    )

    with runtime_db_session() as runtime_db:
        thread = get_or_create_thread(runtime_db, user_id=7)
        append_message(
            runtime_db,
            thread=thread,
            run_id=None,
            step_id=None,
            sequence_id=1,
            role="user",
            content_text="delete me permanently",
        )
        write_session_note(
            runtime_db,
            thread_id=thread.id,
            user_id=7,
            key="delete-note-key",
            value="delete-note-value",
        )
        assert set(runtime_db.scalars(select(CoreFSSealedPayload.row_type)).all()) == {
            "runtime_message",
            "runtime_session_note",
        }

        result = delete_thread_with_image_cleanup(
            runtime_db,
            user_id=7,
            thread_id=thread.id,
        )

        assert result.deleted is True
        assert runtime_db.scalar(select(CoreFSSealedPayload.id)) is None


def test_duplicate_sealed_candidate_reseals_without_flushing_plaintext(
    monkeypatch,
) -> None:
    from anima_server.models.runtime_memory import MemoryCandidate
    from anima_server.services.agent.candidate_ops import create_memory_candidate
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
            content="repeat candidate plaintext",
            category="fact",
            importance=1,
            source_message_ids=[10],
            tags=["private-tag"],
        )
        assert candidate is not None
        sealed_before = runtime_db.scalar(
            select(CoreFSSealedPayload).where(CoreFSSealedPayload.row_type == "memory_candidate")
        )
        assert sealed_before is not None
        ciphertext_before = bytes(sealed_before.ciphertext)

        runtime_db.expunge_all()
        duplicate = create_memory_candidate(
            runtime_db,
            user_id=7,
            content="repeat candidate plaintext",
            category="fact",
            importance=1,
            source_message_ids=[20],
            tags=["private-tag"],
        )

        assert duplicate is None
        stored = runtime_db.execute(
            text("SELECT content, tags_json, salience_json FROM memory_candidates WHERE id = :id"),
            {"id": candidate.id},
        ).one()
        assert stored == ("", "null", "null")

        runtime_db.expunge_all()
        sealed_after = runtime_db.scalar(
            select(CoreFSSealedPayload).where(CoreFSSealedPayload.row_type == "memory_candidate")
        )
        assert sealed_after is not None
        assert bytes(sealed_after.ciphertext) != ciphertext_before

        runtime_db.expunge_all()
        loaded = runtime_db.scalar(
            select(MemoryCandidate).where(MemoryCandidate.id == candidate.id)
        )
        assert loaded is not None
        assert loaded.content == "repeat candidate plaintext"
        assert loaded.tags_json == ["private-tag"]
        assert loaded.salience_json is not None
        assert loaded.salience_json["repeat_count"] == 2
        assert loaded.source_message_ids == [10, 20]


def test_unlock_converter_seals_and_scrubs_legacy_runtime_rows(
    monkeypatch,
) -> None:
    from anima_server.models.pending_memory_op import PendingMemoryOp
    from anima_server.models.runtime import RuntimeDocumentChunk
    from anima_server.services.corefs import sealed_runtime
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
    from anima_server.services.documents.models import (
        DocumentRegistration,
        ExtractedDocumentChunk,
    )
    from anima_server.services.documents.store import (
        register_document,
        replace_document_chunks,
    )
    from conftest_runtime import runtime_db_session

    with runtime_db_session() as runtime_db:
        document = register_document(
            runtime_db,
            DocumentRegistration(
                user_id=7,
                filename="legacy.pdf",
                mime_type="application/pdf",
                storage_path=".anima/documents/7/legacy.pdf",
                sha256="7" * 64,
                size_bytes=128,
            ),
        )
        chunks = replace_document_chunks(
            runtime_db,
            document_id=document.id,
            chunks=[
                ExtractedDocumentChunk(
                    chunk_index=0,
                    content_text="legacy document plaintext",
                    section_title="legacy private section",
                    metadata_json={"outline": "legacy private outline"},
                )
            ],
            parse_quality="legacy",
        )
        pending = PendingMemoryOp(
            user_id=7,
            op_type="append",
            target_block="human",
            content="legacy pending plaintext",
            old_content="older plaintext",
        )
        runtime_db.add(pending)
        runtime_db.flush()
        runtime_db.add(
            CoreFSRuntimeBinding(
                binding_slot=1,
                core_id="core-a",
                local_instance_id="instance-a",
            )
        )
        runtime_db.flush()

        index = CoreFSProgressiveIndex("core-a")
        index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
        converted = sealed_runtime.convert_legacy_runtime_rows(
            runtime_db,
            index=index,
            user_id=7,
        )
        runtime_db.flush()

        raw_chunk = runtime_db.execute(
            select(
                RuntimeDocumentChunk.__table__.c.content_text,
                RuntimeDocumentChunk.__table__.c.content_char_count,
                RuntimeDocumentChunk.__table__.c.section_title,
                RuntimeDocumentChunk.__table__.c.metadata_json,
            ).where(RuntimeDocumentChunk.__table__.c.id == chunks[0].id)
        ).one()
        raw_pending = runtime_db.execute(
            select(
                PendingMemoryOp.__table__.c.content,
                PendingMemoryOp.__table__.c.old_content,
            ).where(PendingMemoryOp.__table__.c.id == pending.id)
        ).one()
        assert converted == 2
        assert raw_chunk[0:3] == ("", len("legacy document plaintext"), None)
        assert raw_chunk[3] is None
        assert raw_pending == ("", None)
        assert runtime_db.scalar(select(CoreFSSealedPayload.id).limit(1)) is not None

        monkeypatch.setattr(
            sealed_runtime,
            "_active_runtime_index",
            lambda _user_id: index,
        )
        runtime_db.expunge_all()
        loaded_chunk = runtime_db.get(RuntimeDocumentChunk, chunks[0].id)
        loaded_pending = runtime_db.get(PendingMemoryOp, pending.id)
        assert loaded_chunk is not None
        assert loaded_chunk.content_text == "legacy document plaintext"
        assert loaded_chunk.section_title == "legacy private section"
        assert loaded_chunk.metadata_json == {"outline": "legacy private outline"}
        assert loaded_pending is not None
        assert loaded_pending.content == "legacy pending plaintext"
        assert loaded_pending.old_content == "older plaintext"
        assert (
            sealed_runtime.convert_legacy_runtime_rows(
                runtime_db,
                index=index,
                user_id=7,
            )
            == 0
        )


def test_corefs_bound_runtime_refuses_sensitive_writes_after_lock(
    monkeypatch,
) -> None:
    from anima_server.models.pending_memory_op import PendingMemoryOp
    from anima_server.models.runtime_memory import MemoryCandidate
    from anima_server.services.agent.candidate_ops import create_memory_candidate
    from anima_server.services.agent.pending_ops import create_pending_op
    from anima_server.services.corefs import sealed_runtime
    from conftest_runtime import runtime_db_session

    monkeypatch.setattr(
        sealed_runtime,
        "_active_runtime_index",
        lambda _user_id: None,
    )

    with runtime_db_session() as runtime_db:
        runtime_db.add(
            CoreFSRuntimeBinding(
                binding_slot=1,
                core_id="core-a",
                local_instance_id="instance-a",
            )
        )
        runtime_db.flush()

        with pytest.raises(RuntimeSealingLocked):
            create_memory_candidate(
                runtime_db,
                user_id=7,
                content="must not reach plaintext candidate storage",
                category="fact",
            )
        with pytest.raises(RuntimeSealingLocked):
            create_pending_op(
                runtime_db,
                user_id=7,
                op_type="append",
                target_block="human",
                content="must not reach plaintext pending storage",
                old_content=None,
                source_run_id=None,
                source_tool_call_id=None,
            )

        assert runtime_db.scalar(select(MemoryCandidate.id)) is None
        assert runtime_db.scalar(select(PendingMemoryOp.id)) is None
