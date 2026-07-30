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


def test_production_asset_and_source_writers_seal_private_descriptors(
    monkeypatch,
    managed_tmp_path: Path,
) -> None:
    from anima_server.config import settings
    from anima_server.models.runtime import (
        RuntimeDocument,
        RuntimeImageAsset,
        RuntimeSource,
    )
    from anima_server.services.corefs import sealed_runtime
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
    from anima_server.services.documents.models import DocumentRegistration
    from anima_server.services.documents.store import register_document
    from anima_server.services.images.store import register_image_asset
    from anima_server.services.ingestion.models import SourceIdentity
    from anima_server.services.ingestion.sources import register_source
    from conftest_runtime import runtime_db_session

    index = CoreFSProgressiveIndex("core-a")
    index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
    monkeypatch.setattr(
        sealed_runtime,
        "_active_runtime_index",
        lambda _user_id: index,
    )
    monkeypatch.setattr(settings, "data_dir", managed_tmp_path)

    document_filename = "private-document-name.pdf"
    document_path = ".anima/documents/7/private-document-name.pdf"
    image_filename = "private-image-name.png"
    source_uri = "https://private.example.test/users/7/captured"
    source_title = "Private captured source title"

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
                user_id=7,
                filename=document_filename,
                mime_type="application/pdf",
                storage_path=document_path,
                sha256="7" * 64,
                size_bytes=128,
                metadata_json={"private_note": "document descriptor metadata"},
            ),
        )
        image = register_image_asset(
            runtime_db,
            user_id=7,
            data=b"\x89PNG\r\n\x1a\n",
            mime_type="image/png",
            filename=image_filename,
            metadata_json={"private_note": "image descriptor metadata"},
        ).asset
        source = register_source(
            runtime_db,
            SourceIdentity(
                user_id=7,
                kind="web_capture",
                source_uri=source_uri,
                content_hash="8" * 64,
                title=source_title,
                media_type="text/html",
                metadata_json={"private_note": "source descriptor metadata"},
            ),
        )
        same_source = register_source(
            runtime_db,
            SourceIdentity(
                user_id=7,
                kind="web_capture",
                source_uri=source_uri,
                content_hash="8" * 64,
                title=source_title,
                media_type="text/html",
                metadata_json={"private_note": "source descriptor metadata"},
            ),
        )
        renamed_source = register_source(
            runtime_db,
            SourceIdentity(
                user_id=7,
                kind="web_capture",
                source_uri=f"{source_uri}/renamed",
                content_hash="8" * 64,
                title=source_title,
                media_type="text/html",
                metadata_json={"private_note": "source descriptor metadata"},
            ),
        )
        event.remove(runtime_db.bind, "before_cursor_execute", capture_parameters)

        raw_document = runtime_db.execute(
            select(
                RuntimeDocument.__table__.c.filename,
                RuntimeDocument.__table__.c.mime_type,
                RuntimeDocument.__table__.c.storage_path,
                RuntimeDocument.__table__.c.metadata_json,
            ).where(RuntimeDocument.__table__.c.id == document.id)
        ).one()
        raw_image = runtime_db.execute(
            select(
                RuntimeImageAsset.__table__.c.filename,
                RuntimeImageAsset.__table__.c.mime_type,
                RuntimeImageAsset.__table__.c.storage_path,
                RuntimeImageAsset.__table__.c.metadata_json,
            ).where(RuntimeImageAsset.__table__.c.id == image.id)
        ).one()
        raw_source = runtime_db.execute(
            select(
                RuntimeSource.__table__.c.source_uri,
                RuntimeSource.__table__.c.title,
                RuntimeSource.__table__.c.media_type,
                RuntimeSource.__table__.c.metadata_json,
            ).where(RuntimeSource.__table__.c.id == source.id)
        ).one()
        row_types = set(runtime_db.scalars(select(CoreFSSealedPayload.row_type)).all())
        runtime_db.expunge_all()
        hydrated_document = runtime_db.get(RuntimeDocument, document.id)
        hydrated_image = runtime_db.get(RuntimeImageAsset, image.id)
        hydrated_source = runtime_db.get(RuntimeSource, source.id)

    captured = repr(executed_parameters)
    for marker in (
        document_filename,
        document_path,
        "document descriptor metadata",
        image_filename,
        "image descriptor metadata",
        source_uri,
        source_title,
        "source descriptor metadata",
    ):
        assert marker not in captured
    assert raw_document == ("", "", "", None)
    assert raw_image == (None, "", "", None)
    assert raw_source[0] != source_uri
    assert raw_source[1:] == (None, None, None)
    assert same_source.id == source.id
    assert renamed_source.id != source.id
    assert hydrated_document is not None
    assert hydrated_document.filename == document_filename
    assert hydrated_document.mime_type == "application/pdf"
    assert hydrated_document.storage_path == document_path
    assert hydrated_document.metadata_json == {"private_note": "document descriptor metadata"}
    assert hydrated_image is not None
    assert hydrated_image.filename == image_filename
    assert hydrated_image.mime_type == "image/png"
    assert hydrated_image.metadata_json == {"private_note": "image descriptor metadata"}
    assert hydrated_source is not None
    assert hydrated_source.source_uri == source_uri
    assert hydrated_source.title == source_title
    assert hydrated_source.metadata_json == {"private_note": "source descriptor metadata"}
    assert {
        "runtime_document",
        "runtime_image_asset",
        "runtime_source",
    }.issubset(row_types)


def test_document_and_image_metadata_mutations_reseal_private_descriptors(
    monkeypatch,
    managed_tmp_path: Path,
) -> None:
    from datetime import UTC, datetime

    from anima_server.config import settings
    from anima_server.models.runtime import RuntimeDocument, RuntimeImageAsset
    from anima_server.services.agent.proactive import mark_proactive_image_prompted
    from anima_server.services.corefs import sealed_runtime
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
    from anima_server.services.documents.models import DocumentRegistration
    from anima_server.services.documents.reparse import mark_docling_reparse_failed
    from anima_server.services.documents.store import register_document
    from anima_server.services.images.store import register_image_asset
    from conftest_runtime import runtime_db_session

    index = CoreFSProgressiveIndex("core-a")
    index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
    monkeypatch.setattr(
        sealed_runtime,
        "_active_runtime_index",
        lambda _user_id: index,
    )
    monkeypatch.setattr(settings, "data_dir", managed_tmp_path)

    prompted_at = datetime(2026, 7, 30, 12, tzinfo=UTC)
    with runtime_db_session() as runtime_db:
        document = register_document(
            runtime_db,
            DocumentRegistration(
                user_id=7,
                filename="private-document.pdf",
                mime_type="application/pdf",
                storage_path=".anima/documents/7/private-document.pdf",
                sha256="7" * 64,
                size_bytes=128,
                metadata_json={"private_note": "document metadata"},
            ),
        )
        image = register_image_asset(
            runtime_db,
            user_id=7,
            data=b"\x89PNG\r\n\x1a\n",
            mime_type="image/png",
            filename="private-image.png",
            metadata_json={"private_note": "image metadata"},
        ).asset

        mark_docling_reparse_failed(
            runtime_db,
            user_id=7,
            document_id=document.id,
        )
        mark_proactive_image_prompted(
            runtime_db,
            user_id=7,
            image_asset_id=image.id,
            now=prompted_at,
        )

        raw_document_metadata = runtime_db.execute(
            select(RuntimeDocument.__table__.c.metadata_json).where(
                RuntimeDocument.__table__.c.id == document.id
            )
        ).scalar_one()
        raw_image_metadata = runtime_db.execute(
            select(RuntimeImageAsset.__table__.c.metadata_json).where(
                RuntimeImageAsset.__table__.c.id == image.id
            )
        ).scalar_one()
        runtime_db.expunge_all()
        hydrated_document = runtime_db.get(RuntimeDocument, document.id)
        hydrated_image = runtime_db.get(RuntimeImageAsset, image.id)

    assert raw_document_metadata is None
    assert raw_image_metadata is None
    assert hydrated_document is not None
    assert hydrated_document.metadata_json is not None
    assert hydrated_document.metadata_json["private_note"] == "document metadata"
    assert "docling_reparse_failed_at" in hydrated_document.metadata_json
    assert hydrated_image is not None
    assert hydrated_image.metadata_json == {
        "private_note": "image metadata",
        "proactivePromptedAt": prompted_at.isoformat(),
    }


def test_knowledge_bundle_run_writers_seal_private_payloads(
    monkeypatch,
) -> None:
    from anima_server.models.runtime import RuntimeKnowledgeBundleRun
    from anima_server.services.corefs import sealed_runtime
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
    from anima_server.services.ingestion.sources import (
        complete_bundle_run,
        fail_bundle_run,
        start_bundle_run,
    )
    from conftest_runtime import runtime_db_session

    index = CoreFSProgressiveIndex("core-a")
    index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
    monkeypatch.setattr(
        sealed_runtime,
        "_active_runtime_index",
        lambda _user_id: index,
    )

    with runtime_db_session() as runtime_db:
        completed = start_bundle_run(
            runtime_db,
            user_id=7,
            run_type="adapter:test",
            input_json={"source_uri": "https://private.example.test/input"},
        )
        complete_bundle_run(
            runtime_db,
            run=completed,
            result_json={"summary": "private successful result"},
        )
        failed = start_bundle_run(
            runtime_db,
            user_id=7,
            run_type="adapter:test",
            input_json={"source_uri": "https://private.example.test/failure"},
        )
        fail_bundle_run(
            runtime_db,
            run=failed,
            exc=RuntimeError("private adapter failure"),
        )

        raw_rows = runtime_db.execute(
            select(
                RuntimeKnowledgeBundleRun.__table__.c.input_json,
                RuntimeKnowledgeBundleRun.__table__.c.result_json,
                RuntimeKnowledgeBundleRun.__table__.c.error_json,
            ).order_by(RuntimeKnowledgeBundleRun.__table__.c.id)
        ).all()
        runtime_db.expunge_all()
        hydrated_completed = runtime_db.get(RuntimeKnowledgeBundleRun, completed.id)
        hydrated_failed = runtime_db.get(RuntimeKnowledgeBundleRun, failed.id)

    assert raw_rows == [(None, None, None), (None, None, None)]
    assert hydrated_completed is not None
    assert hydrated_completed.input_json == {
        "source_uri": "https://private.example.test/input"
    }
    assert hydrated_completed.result_json == {
        "summary": "private successful result"
    }
    assert hydrated_completed.error_json is None
    assert hydrated_failed is not None
    assert hydrated_failed.input_json == {
        "source_uri": "https://private.example.test/failure"
    }
    assert hydrated_failed.result_json is None
    assert hydrated_failed.error_json == {
        "message": "private adapter failure",
        "type": "RuntimeError",
    }


def test_unlock_converter_seals_legacy_knowledge_bundle_runs(
    monkeypatch,
) -> None:
    from anima_server.models.runtime import RuntimeKnowledgeBundleRun
    from anima_server.services.corefs import sealed_runtime
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
    from conftest_runtime import runtime_db_session

    with runtime_db_session() as runtime_db:
        legacy_run = RuntimeKnowledgeBundleRun(
            user_id=7,
            run_type="adapter:legacy",
            status="failed",
            input_json={"source_uri": "https://private.example.test/legacy"},
            result_json={"partial": "private partial result"},
            error_json={"message": "private legacy failure"},
        )
        runtime_db.add(legacy_run)
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
        raw_payloads = runtime_db.execute(
            select(
                RuntimeKnowledgeBundleRun.__table__.c.input_json,
                RuntimeKnowledgeBundleRun.__table__.c.result_json,
                RuntimeKnowledgeBundleRun.__table__.c.error_json,
            ).where(RuntimeKnowledgeBundleRun.__table__.c.id == legacy_run.id)
        ).one()

        monkeypatch.setattr(
            sealed_runtime,
            "_active_runtime_index",
            lambda _user_id: index,
        )
        runtime_db.expunge_all()
        hydrated = runtime_db.get(RuntimeKnowledgeBundleRun, legacy_run.id)

    assert converted == 1
    assert raw_payloads == (None, None, None)
    assert hydrated is not None
    assert hydrated.input_json == {
        "source_uri": "https://private.example.test/legacy"
    }
    assert hydrated.result_json == {"partial": "private partial result"}
    assert hydrated.error_json == {"message": "private legacy failure"}


def test_workflow_error_writers_reseal_run_and_checkpoint_payloads(
    monkeypatch,
) -> None:
    from anima_server.models.runtime import (
        RuntimeWorkflowCheckpoint,
        RuntimeWorkflowRun,
    )
    from anima_server.services.corefs import sealed_runtime
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
    from anima_server.services.workflows.checkpoints import (
        append_checkpoint,
        mark_workflow_failed,
        start_workflow,
    )
    from conftest_runtime import runtime_db_session

    index = CoreFSProgressiveIndex("core-a")
    index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
    monkeypatch.setattr(
        sealed_runtime,
        "_active_runtime_index",
        lambda _user_id: index,
    )

    with runtime_db_session() as runtime_db:
        checkpoint_run = start_workflow(
            runtime_db,
            user_id=7,
            workflow_type="checkpoint_rag",
            input_json={"filename": "private-checkpoint-input.pdf"},
        )
        checkpoint = append_checkpoint(
            runtime_db,
            workflow_run_id=checkpoint_run.id,
            state_name="extract",
            status="failed",
            idempotency_key="private-checkpoint-error",
            error_json={"message": "private checkpoint failure.pdf"},
        )
        direct_run = start_workflow(
            runtime_db,
            user_id=7,
            workflow_type="checkpoint_rag",
            input_json={"filename": "private-direct-input.pdf"},
        )
        mark_workflow_failed(
            runtime_db,
            direct_run,
            error_json={"message": "private direct workflow failure.pdf"},
        )

        raw_runs = runtime_db.execute(
            select(
                RuntimeWorkflowRun.__table__.c.input_json,
                RuntimeWorkflowRun.__table__.c.result_json,
                RuntimeWorkflowRun.__table__.c.error_json,
            ).order_by(RuntimeWorkflowRun.__table__.c.id)
        ).all()
        raw_checkpoint = runtime_db.execute(
            select(
                RuntimeWorkflowCheckpoint.__table__.c.input_json,
                RuntimeWorkflowCheckpoint.__table__.c.output_json,
                RuntimeWorkflowCheckpoint.__table__.c.error_json,
            ).where(RuntimeWorkflowCheckpoint.__table__.c.id == checkpoint.id)
        ).one()
        runtime_db.expunge_all()
        hydrated_checkpoint_run = runtime_db.get(
            RuntimeWorkflowRun,
            checkpoint_run.id,
        )
        hydrated_direct_run = runtime_db.get(RuntimeWorkflowRun, direct_run.id)
        hydrated_checkpoint = runtime_db.get(
            RuntimeWorkflowCheckpoint,
            checkpoint.id,
        )

    assert raw_runs == [(None, None, None), (None, None, None)]
    assert raw_checkpoint == (None, None, None)
    assert hydrated_checkpoint_run is not None
    assert hydrated_checkpoint_run.error_json == {
        "message": "private checkpoint failure.pdf"
    }
    assert hydrated_direct_run is not None
    assert hydrated_direct_run.error_json == {
        "message": "private direct workflow failure.pdf"
    }
    assert hydrated_checkpoint is not None
    assert hydrated_checkpoint.error_json == {
        "message": "private checkpoint failure.pdf"
    }


def test_unlock_converter_seals_legacy_workflow_errors(
    monkeypatch,
) -> None:
    from anima_server.models.runtime import (
        RuntimeWorkflowCheckpoint,
        RuntimeWorkflowRun,
    )
    from anima_server.services.corefs import sealed_runtime
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
    from conftest_runtime import runtime_db_session

    with runtime_db_session() as runtime_db:
        legacy_run = RuntimeWorkflowRun(
            user_id=7,
            workflow_type="checkpoint_rag",
            status="failed",
            current_state="extract",
            input_json={"filename": "private-legacy-input.pdf"},
            result_json={"partial": "private legacy result"},
            error_json={"message": "private legacy workflow failure.pdf"},
        )
        runtime_db.add(legacy_run)
        runtime_db.flush()
        legacy_checkpoint = RuntimeWorkflowCheckpoint(
            workflow_run_id=legacy_run.id,
            checkpoint_index=1,
            state_name="extract",
            status="failed",
            input_json={"path": "private/legacy/input.pdf"},
            output_json={"partial": "private checkpoint result"},
            error_json={"message": "private legacy checkpoint failure.pdf"},
            idempotency_key="private-legacy-error",
        )
        runtime_db.add(legacy_checkpoint)
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
        sealed_runtime.seal_runtime_record(
            runtime_db,
            index=index,
            row_type="runtime_workflow_run",
            row_id=legacy_run.id,
            owner_id=7,
            payload={
                "input_json": legacy_run.input_json,
                "result_json": legacy_run.result_json,
            },
        )
        sealed_runtime.seal_runtime_record(
            runtime_db,
            index=index,
            row_type="runtime_workflow_checkpoint",
            row_id=legacy_checkpoint.id,
            owner_id=7,
            payload={
                "input_json": legacy_checkpoint.input_json,
                "output_json": legacy_checkpoint.output_json,
            },
        )
        runtime_db.execute(
            RuntimeWorkflowRun.__table__.update()
            .where(RuntimeWorkflowRun.__table__.c.id == legacy_run.id)
            .values(input_json=None, result_json=None)
        )
        runtime_db.execute(
            RuntimeWorkflowCheckpoint.__table__.update()
            .where(
                RuntimeWorkflowCheckpoint.__table__.c.id
                == legacy_checkpoint.id
            )
            .values(input_json=None, output_json=None)
        )
        converted = sealed_runtime.convert_legacy_runtime_rows(
            runtime_db,
            index=index,
            user_id=7,
        )
        raw_run = runtime_db.execute(
            select(
                RuntimeWorkflowRun.__table__.c.input_json,
                RuntimeWorkflowRun.__table__.c.result_json,
                RuntimeWorkflowRun.__table__.c.error_json,
            ).where(RuntimeWorkflowRun.__table__.c.id == legacy_run.id)
        ).one()
        raw_checkpoint = runtime_db.execute(
            select(
                RuntimeWorkflowCheckpoint.__table__.c.input_json,
                RuntimeWorkflowCheckpoint.__table__.c.output_json,
                RuntimeWorkflowCheckpoint.__table__.c.error_json,
            ).where(RuntimeWorkflowCheckpoint.__table__.c.id == legacy_checkpoint.id)
        ).one()

        monkeypatch.setattr(
            sealed_runtime,
            "_active_runtime_index",
            lambda _user_id: index,
        )
        runtime_db.expunge_all()
        hydrated_run = runtime_db.get(RuntimeWorkflowRun, legacy_run.id)
        hydrated_checkpoint = runtime_db.get(
            RuntimeWorkflowCheckpoint,
            legacy_checkpoint.id,
        )

    assert converted == 2
    assert raw_run == (None, None, None)
    assert raw_checkpoint == (None, None, None)
    assert hydrated_run is not None
    assert hydrated_run.error_json == {
        "message": "private legacy workflow failure.pdf"
    }
    assert hydrated_checkpoint is not None
    assert hydrated_checkpoint.error_json == {
        "message": "private legacy checkpoint failure.pdf"
    }


def test_agent_run_failure_writer_seals_error_text(monkeypatch) -> None:
    from anima_server.models.runtime import RuntimeRun, RuntimeThread
    from anima_server.services.agent.persistence import mark_run_failed
    from anima_server.services.corefs import sealed_runtime
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
    from conftest_runtime import runtime_db_session

    index = CoreFSProgressiveIndex("core-a")
    index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
    monkeypatch.setattr(sealed_runtime, "_active_runtime_index", lambda _user_id: index)
    private_error = "Provider failed while reading /private/logical/manual.pdf"

    with runtime_db_session() as runtime_db:
        runtime_db.add(
            CoreFSRuntimeBinding(
                binding_slot=1,
                core_id="core-a",
                local_instance_id="instance-a",
            )
        )
        thread = RuntimeThread(user_id=7, status="active")
        runtime_db.add(thread)
        runtime_db.flush()
        run = RuntimeRun(
            thread_id=thread.id,
            user_id=7,
            provider="test",
            model="test",
            mode="chat",
            status="running",
        )
        runtime_db.add(run)
        runtime_db.flush()

        mark_run_failed(runtime_db, run, private_error)
        runtime_db.flush()
        raw_error = runtime_db.scalar(
            select(RuntimeRun.__table__.c.error_text).where(
                RuntimeRun.__table__.c.id == run.id
            )
        )
        run_id = int(run.id)
        runtime_db.expunge_all()
        hydrated = runtime_db.get(RuntimeRun, run_id)

    assert raw_error is None
    assert hydrated is not None
    assert hydrated.error_text == private_error


def test_unlock_converter_seals_legacy_agent_run_error_text(monkeypatch) -> None:
    from anima_server.models.runtime import RuntimeRun, RuntimeThread
    from anima_server.services.corefs import sealed_runtime
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
    from conftest_runtime import runtime_db_session

    index = CoreFSProgressiveIndex("core-a")
    index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
    monkeypatch.setattr(sealed_runtime, "_active_runtime_index", lambda _user_id: index)
    private_error = "Legacy failure from /private/logical/archive.pdf"

    with runtime_db_session() as runtime_db:
        thread = RuntimeThread(user_id=7, status="active")
        runtime_db.add(thread)
        runtime_db.flush()
        run = RuntimeRun(
            thread_id=thread.id,
            user_id=7,
            provider="test",
            model="test",
            mode="chat",
            status="failed",
            error_text=private_error,
        )
        runtime_db.add(run)
        runtime_db.flush()
        run_id = int(run.id)

        converted = sealed_runtime.convert_legacy_runtime_rows(
            runtime_db,
            index=index,
            user_id=7,
        )
        raw_error = runtime_db.scalar(
            select(RuntimeRun.__table__.c.error_text).where(
                RuntimeRun.__table__.c.id == run_id
            )
        )
        runtime_db.expunge_all()
        hydrated = runtime_db.get(RuntimeRun, run_id)

    assert converted >= 1
    assert raw_error is None
    assert hydrated is not None
    assert hydrated.error_text == private_error


def test_document_chunk_replacement_deletes_superseded_sealed_payload(
    monkeypatch,
) -> None:
    from anima_server.models.runtime_embedding import RuntimeEmbedding
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
    from anima_server.services.ingestion.retrieval import _upsert_embedding
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
        old_chunks = replace_document_chunks(
            runtime_db,
            document_id=document.id,
            chunks=[ExtractedDocumentChunk(chunk_index=0, content_text="old private")],
            parse_quality="native",
        )
        _upsert_embedding(
            runtime_db,
            user_id=1,
            source_type="document_chunk",
            source_id=old_chunks[0].id,
            text="old private",
            category="document",
            importance=3,
            embedding_fn=lambda _text: [0.0] * RuntimeEmbedding.__table__.c.embedding.type.dim,
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
        embedding_sealed_count = runtime_db.scalar(
            select(func.count(CoreFSSealedPayload.id)).where(
                CoreFSSealedPayload.row_type == "runtime_embedding"
            )
        )

    assert sealed_count == 2
    assert embedding_sealed_count == 0


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
    from anima_server.models.runtime_embedding import RuntimeEmbedding
    from anima_server.services.corefs import sealed_runtime
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
    from anima_server.services.images import deletion as image_deletion
    from anima_server.services.images.indexing import (
        _upsert_active_annotation,
        _upsert_runtime_embedding,
    )
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
        annotation = _upsert_active_annotation(
            runtime_db,
            user_id=1,
            image_asset_id=image.id,
            annotation_kind="ocr_text",
            content_text="private OCR",
            source_model=None,
        )
        _upsert_runtime_embedding(
            runtime_db,
            user_id=1,
            annotation=annotation,
            embedding=[0.0] * RuntimeEmbedding.__table__.c.embedding.type.dim,
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
                "runtime_embedding",
            )
        }

    assert counts == {
        "runtime_source_artifact": 1,
        "runtime_source_span": 1,
        "runtime_image_annotation": 0,
        "runtime_embedding": 0,
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
    from anima_server.services.agent.thread_manager import maybe_set_thread_title
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
        maybe_set_thread_title(
            runtime_db,
            thread,
            "Please permanently delete this private conversation",
        )
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
            "runtime_thread",
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
    from anima_server.models.runtime import (
        RuntimeDocument,
        RuntimeDocumentChunk,
        RuntimeImageAsset,
        RuntimeKnowledgeConcept,
        RuntimeSource,
        RuntimeThread,
        RuntimeWorkflowCheckpoint,
        RuntimeWorkflowRun,
    )
    from anima_server.models.runtime_embedding import RuntimeEmbedding
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

    legacy_document_text = ("legacy full document embedding input " * 10) + "final marker"
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
                metadata_json={"private_note": "legacy document metadata"},
            ),
        )
        chunks = replace_document_chunks(
            runtime_db,
            document_id=document.id,
            chunks=[
                ExtractedDocumentChunk(
                    chunk_index=0,
                    content_text=legacy_document_text,
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
        concept = RuntimeKnowledgeConcept(
            user_id=7,
            concept_type="claim",
            slug="legacy-private-concept",
            title="Legacy private title",
            description="Legacy private description",
            body_markdown="Legacy private concept body",
            frontmatter_json={"tags": ["legacy-private-tag"]},
            content_hash="c" * 64,
            status="active",
        )
        runtime_db.add(concept)
        runtime_db.flush()
        image_asset = RuntimeImageAsset(
            user_id=7,
            filename="legacy-private.png",
            mime_type="image/png",
            storage_path="users/7/media/images/legacy-private.png",
            sha256="8" * 64,
            size_bytes=64,
            metadata_json={"private_note": "legacy image metadata"},
        )
        runtime_db.add(image_asset)
        source = RuntimeSource(
            user_id=7,
            kind="web_capture",
            source_uri="https://private.example.test/legacy",
            content_hash="9" * 64,
            title="Legacy private source title",
            media_type="text/html",
            metadata_json={"private_note": "legacy source metadata"},
        )
        runtime_db.add(source)
        runtime_db.flush()
        thread = RuntimeThread(
            user_id=7,
            status="active",
            title="Legacy private thread title",
        )
        runtime_db.add(thread)
        runtime_db.flush()
        workflow_run = RuntimeWorkflowRun(
            user_id=7,
            thread_id=thread.id,
            workflow_type="pdf_ingestion",
            status="awaiting_input",
            current_state="awaiting_approval",
            input_json={"filename": "legacy-private.pdf"},
            result_json={"proposed_facts": ["legacy private fact"]},
        )
        runtime_db.add(workflow_run)
        runtime_db.flush()
        workflow_checkpoint = RuntimeWorkflowCheckpoint(
            workflow_run_id=workflow_run.id,
            checkpoint_index=1,
            state_name="summarized",
            status="completed",
            input_json={"storage_path": ".anima/documents/7/legacy-private.pdf"},
            output_json={"summary": "legacy private summary"},
            idempotency_key="legacy-summary-1",
        )
        runtime_db.add(workflow_checkpoint)
        runtime_db.flush()
        runtime_db.add(
            CoreFSRuntimeBinding(
                binding_slot=1,
                core_id="core-a",
                local_instance_id="instance-a",
            )
        )
        vector = [0.0] * RuntimeEmbedding.__table__.c.embedding.type.dim
        vector[0] = 1.0
        runtime_embedding = RuntimeEmbedding(
            user_id=7,
            source_type="document_chunk",
            source_id=chunks[0].id,
            content_hash="e" * 64,
            embedding_checksum=RuntimeEmbedding.compute_embedding_checksum(vector),
            embedding=vector,
            content_preview=legacy_document_text[:200],
            category="fact",
            importance=4,
        )
        runtime_db.add(runtime_embedding)
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
        raw_concept = runtime_db.execute(
            select(
                RuntimeKnowledgeConcept.__table__.c.title,
                RuntimeKnowledgeConcept.__table__.c.description,
                RuntimeKnowledgeConcept.__table__.c.body_markdown,
                RuntimeKnowledgeConcept.__table__.c.frontmatter_json,
            ).where(RuntimeKnowledgeConcept.__table__.c.id == concept.id)
        ).one()
        raw_document = runtime_db.execute(
            select(
                RuntimeDocument.__table__.c.filename,
                RuntimeDocument.__table__.c.mime_type,
                RuntimeDocument.__table__.c.storage_path,
                RuntimeDocument.__table__.c.metadata_json,
            ).where(RuntimeDocument.__table__.c.id == document.id)
        ).one()
        raw_image_asset = runtime_db.execute(
            select(
                RuntimeImageAsset.__table__.c.filename,
                RuntimeImageAsset.__table__.c.mime_type,
                RuntimeImageAsset.__table__.c.storage_path,
                RuntimeImageAsset.__table__.c.metadata_json,
            ).where(RuntimeImageAsset.__table__.c.id == image_asset.id)
        ).one()
        raw_source = runtime_db.execute(
            select(
                RuntimeSource.__table__.c.source_uri,
                RuntimeSource.__table__.c.title,
                RuntimeSource.__table__.c.media_type,
                RuntimeSource.__table__.c.metadata_json,
            ).where(RuntimeSource.__table__.c.id == source.id)
        ).one()
        raw_thread = runtime_db.execute(
            select(RuntimeThread.__table__.c.title).where(RuntimeThread.__table__.c.id == thread.id)
        ).scalar_one()
        raw_workflow_run = runtime_db.execute(
            select(
                RuntimeWorkflowRun.__table__.c.input_json,
                RuntimeWorkflowRun.__table__.c.result_json,
            ).where(RuntimeWorkflowRun.__table__.c.id == workflow_run.id)
        ).one()
        raw_workflow_checkpoint = runtime_db.execute(
            select(
                RuntimeWorkflowCheckpoint.__table__.c.input_json,
                RuntimeWorkflowCheckpoint.__table__.c.output_json,
            ).where(RuntimeWorkflowCheckpoint.__table__.c.id == workflow_checkpoint.id)
        ).one()
        raw_embedding = runtime_db.execute(
            select(
                RuntimeEmbedding.__table__.c.content_preview,
                RuntimeEmbedding.__table__.c.embedding,
                RuntimeEmbedding.__table__.c.embedding_checksum,
            ).where(RuntimeEmbedding.__table__.c.id == runtime_embedding.id)
        ).one()
        assert converted == 10
        assert raw_chunk[0:3] == ("", len(legacy_document_text), None)
        assert raw_chunk[3] is None
        assert raw_pending == ("", None)
        assert raw_concept == ("", None, "", {})
        assert raw_document == ("", "", "", None)
        assert raw_image_asset == (None, "", "", None)
        assert raw_source[0] != "https://private.example.test/legacy"
        assert raw_source[1:] == (None, None, None)
        assert raw_thread is None
        assert raw_workflow_run == (None, None)
        assert raw_workflow_checkpoint == (None, None)
        assert raw_embedding == ("", None, None)
        hits = index.search_runtime_embeddings(tuple(vector), limit=5)
        assert [(hit.source_type, hit.source_id) for hit in hits] == [
            ("document_chunk", chunks[0].id)
        ]
        assert runtime_db.scalar(select(CoreFSSealedPayload.id).limit(1)) is not None

        monkeypatch.setattr(
            sealed_runtime,
            "_active_runtime_index",
            lambda _user_id: index,
        )
        runtime_db.expunge_all()
        loaded_chunk = runtime_db.get(RuntimeDocumentChunk, chunks[0].id)
        loaded_pending = runtime_db.get(PendingMemoryOp, pending.id)
        loaded_concept = runtime_db.get(RuntimeKnowledgeConcept, concept.id)
        loaded_document = runtime_db.get(RuntimeDocument, document.id)
        loaded_image_asset = runtime_db.get(RuntimeImageAsset, image_asset.id)
        loaded_source = runtime_db.get(RuntimeSource, source.id)
        loaded_thread = runtime_db.get(RuntimeThread, thread.id)
        loaded_workflow_run = runtime_db.get(RuntimeWorkflowRun, workflow_run.id)
        loaded_workflow_checkpoint = runtime_db.get(
            RuntimeWorkflowCheckpoint,
            workflow_checkpoint.id,
        )
        assert loaded_chunk is not None
        assert loaded_chunk.content_text == legacy_document_text
        assert loaded_chunk.section_title == "legacy private section"
        assert loaded_chunk.metadata_json == {"outline": "legacy private outline"}
        assert loaded_pending is not None
        assert loaded_pending.content == "legacy pending plaintext"
        assert loaded_pending.old_content == "older plaintext"
        assert loaded_concept is not None
        assert loaded_concept.title == "Legacy private title"
        assert loaded_concept.description == "Legacy private description"
        assert loaded_concept.body_markdown == "Legacy private concept body"
        assert loaded_concept.frontmatter_json == {"tags": ["legacy-private-tag"]}
        assert loaded_document is not None
        assert loaded_document.filename == "legacy.pdf"
        assert loaded_document.mime_type == "application/pdf"
        assert loaded_document.storage_path == ".anima/documents/7/legacy.pdf"
        assert loaded_document.metadata_json == {"private_note": "legacy document metadata"}
        assert loaded_image_asset is not None
        assert loaded_image_asset.filename == "legacy-private.png"
        assert loaded_image_asset.mime_type == "image/png"
        assert loaded_image_asset.storage_path == ("users/7/media/images/legacy-private.png")
        assert loaded_image_asset.metadata_json == {"private_note": "legacy image metadata"}
        assert loaded_source is not None
        assert loaded_source.source_uri == "https://private.example.test/legacy"
        assert loaded_source.title == "Legacy private source title"
        assert loaded_source.media_type == "text/html"
        assert loaded_source.metadata_json == {"private_note": "legacy source metadata"}
        assert loaded_thread is not None
        assert loaded_thread.title == "Legacy private thread title"
        assert loaded_workflow_run is not None
        assert loaded_workflow_run.input_json == {"filename": "legacy-private.pdf"}
        assert loaded_workflow_run.result_json == {"proposed_facts": ["legacy private fact"]}
        assert loaded_workflow_checkpoint is not None
        assert loaded_workflow_checkpoint.input_json == {
            "storage_path": ".anima/documents/7/legacy-private.pdf"
        }
        assert loaded_workflow_checkpoint.output_json == {"summary": "legacy private summary"}
        assert (
            sealed_runtime.convert_legacy_runtime_rows(
                runtime_db,
                index=index,
                user_id=7,
            )
            == 0
        )

        rebuilt_index = CoreFSProgressiveIndex("core-a")
        rebuilt_index.unlock(
            sqlcipher_key=b"k" * 32,
            local_instance_id="instance-a",
        )
        embedded_texts: list[str] = []
        assert (
            sealed_runtime.rebuild_runtime_embeddings(
                runtime_db,
                index=rebuilt_index,
                user_id=7,
                embedder=lambda value: embedded_texts.append(value) or tuple(vector),
            )
            == 1
        )
        assert embedded_texts == [f"legacy private section\n\n{legacy_document_text}"]


def test_runtime_embedding_rebuild_restores_vectors_for_a_new_unlock(
    monkeypatch,
) -> None:
    from anima_server.models.runtime_embedding import RuntimeEmbedding
    from anima_server.services.corefs import sealed_runtime
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
    from conftest_runtime import runtime_db_session

    content = ("full private embedding content " * 10) + "trailing regeneration marker"
    dimension = RuntimeEmbedding.__table__.c.embedding.type.dim
    vector = [0.0] * dimension
    vector[0] = 1.0
    first_index = CoreFSProgressiveIndex("core-a")
    first_index.unlock(
        sqlcipher_key=b"k" * 32,
        local_instance_id="instance-a",
    )
    monkeypatch.setattr(
        sealed_runtime,
        "_active_runtime_index",
        lambda _user_id: first_index,
    )

    with runtime_db_session() as runtime_db:
        runtime_db.add(
            CoreFSRuntimeBinding(
                binding_slot=1,
                core_id="core-a",
                local_instance_id="instance-a",
            )
        )
        row = RuntimeEmbedding(
            user_id=7,
            source_type="memory_item",
            source_id=92,
            content_hash=RuntimeEmbedding.compute_content_hash(content),
            embedding_checksum=None,
            embedding=None,
            content_preview="",
            category="fact",
            importance=5,
        )
        sealed_runtime.persist_runtime_embedding(
            runtime_db,
            row=row,
            owner_id=7,
            embedding=vector,
            content=content,
        )
        runtime_db.flush()

        second_index = CoreFSProgressiveIndex("core-a")
        second_index.unlock(
            sqlcipher_key=b"k" * 32,
            local_instance_id="instance-a",
        )
        embedded_texts = []

        def embed(text_value: str) -> tuple[float, ...]:
            embedded_texts.append(text_value)
            return tuple(vector)

        rebuilt = sealed_runtime.rebuild_runtime_embeddings(
            runtime_db,
            index=second_index,
            user_id=7,
            embedder=embed,
        )

        assert rebuilt == 1
        assert embedded_texts == [content]
        hits = second_index.search_runtime_embeddings(tuple(vector), limit=5)
        assert [(hit.source_type, hit.source_id) for hit in hits] == [("memory_item", 92)]


def test_runtime_embedding_refresh_rebuilds_existing_vectors_for_new_fingerprint() -> None:
    from anima_server.models.runtime_embedding import RuntimeEmbedding
    from anima_server.services.corefs import sealed_runtime
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
    from conftest_runtime import runtime_db_session

    index = CoreFSProgressiveIndex("core-a")
    index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
    old_vector = (1.0, 0.0)
    index.begin_runtime_embedding_rebuild(embedding_fingerprint="old")
    index.upsert_runtime_embedding(
        source_type="memory_item",
        source_id=93,
        vector=old_vector,
        content="private content",
        category="fact",
        importance=5,
        embedding_fingerprint="old",
    )

    with runtime_db_session() as runtime_db:
        runtime_db.add(
            CoreFSRuntimeBinding(
                binding_slot=1,
                core_id="core-a",
                local_instance_id="instance-a",
            )
        )
        row = RuntimeEmbedding(
            user_id=7,
            source_type="memory_item",
            source_id=93,
            content_hash=RuntimeEmbedding.compute_content_hash("private content"),
            content_preview="",
            category="fact",
            importance=5,
        )
        runtime_db.add(row)
        runtime_db.flush()
        sealed_runtime.seal_runtime_record(
            runtime_db,
            index=index,
            row_type="runtime_embedding",
            row_id=row.id,
            owner_id=7,
            payload={
                "content_preview": "private content",
                "embedding_content": "private content",
            },
        )
        index.request_runtime_embedding_refresh(embedding_fingerprint="new")
        with pytest.raises(ValueError, match="configuration changed"):
            index.upsert_runtime_embedding(
                source_type="memory_item",
                source_id=93,
                vector=old_vector,
                content="stale private content",
                category="fact",
                importance=5,
                embedding_fingerprint="old",
            )

        embedded_texts: list[str] = []

        def embed(value: str) -> tuple[float, ...]:
            embedded_texts.append(value)
            return (0.0, 1.0)

        embed.corefs_embedding_fingerprint = "new"  # type: ignore[attr-defined]
        assert (
            sealed_runtime.rebuild_runtime_embeddings(
                runtime_db,
                index=index,
                user_id=7,
                embedder=embed,
            )
            == 1
        )
        assert embedded_texts == ["private content"]
        assert index.runtime_embedding_vector(
            source_type="memory_item",
            source_id=93,
        ) == (0.0, 1.0)


def test_legacy_memory_embedding_content_uses_the_constructing_session_dek(
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    from anima_server.db import session as soul_session
    from anima_server.services.corefs import sealed_runtime
    from anima_server.services.crypto import encrypt_text_with_dek
    from conftest_runtime import runtime_db_session

    memory_dek = b"m" * 32
    content = ("full legacy memory embedding input " * 10) + "final marker"
    encrypted = encrypt_text_with_dek(
        content,
        memory_dek,
        aad=b"memory_items:7:content",
    )

    class SoulDB:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, _model, source_id: int):
            assert source_id == 96
            return SimpleNamespace(user_id=7, content=encrypted)

    monkeypatch.setattr(
        soul_session,
        "get_user_session_factory",
        lambda _owner_id: SoulDB,
    )

    with runtime_db_session() as runtime_db:
        assert (
            sealed_runtime._legacy_runtime_embedding_content(
                runtime_db,
                owner_id=7,
                source_type="memory_item",
                source_id=96,
                memory_dek=memory_dek,
            )
            == content
        )


def test_runtime_embedding_cache_changes_only_after_commit_and_fans_out(
    monkeypatch,
) -> None:
    from anima_server.models.runtime_embedding import RuntimeEmbedding
    from anima_server.services.corefs import sealed_runtime
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
    from conftest_runtime import runtime_db_session

    indexes = (
        CoreFSProgressiveIndex("core-a"),
        CoreFSProgressiveIndex("core-a"),
    )
    for index in indexes:
        index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
    monkeypatch.setattr(sealed_runtime, "_active_runtime_index", lambda _user_id: indexes[0])
    monkeypatch.setattr(sealed_runtime, "active_runtime_indexes", lambda _user_id: indexes)
    vector = (1.0, 0.0)

    with runtime_db_session() as runtime_db:
        runtime_db.add(
            CoreFSRuntimeBinding(
                binding_slot=1,
                core_id="core-a",
                local_instance_id="instance-a",
            )
        )
        runtime_db.commit()

        rolled_back = RuntimeEmbedding(
            user_id=7,
            source_type="memory_item",
            source_id=94,
            content_hash=RuntimeEmbedding.compute_content_hash("rolled back"),
            content_preview="",
            category="fact",
            importance=5,
        )
        sealed_runtime.persist_runtime_embedding(
            runtime_db,
            row=rolled_back,
            owner_id=7,
            embedding=vector,
            content="rolled back",
        )
        runtime_db.flush()
        assert all(
            index.runtime_embedding_vector(source_type="memory_item", source_id=94) is None
            for index in indexes
        )
        runtime_db.rollback()
        assert all(
            index.runtime_embedding_vector(source_type="memory_item", source_id=94) is None
            for index in indexes
        )

        committed = RuntimeEmbedding(
            user_id=7,
            source_type="memory_item",
            source_id=95,
            content_hash=RuntimeEmbedding.compute_content_hash("committed"),
            content_preview="",
            category="fact",
            importance=5,
        )
        sealed_runtime.persist_runtime_embedding(
            runtime_db,
            row=committed,
            owner_id=7,
            embedding=vector,
            content="committed",
        )
        runtime_db.flush()
        runtime_db.commit()
        assert all(
            index.runtime_embedding_vector(source_type="memory_item", source_id=95) == vector
            for index in indexes
        )

        assert (
            sealed_runtime.delete_runtime_embedding_records(
                runtime_db,
                owner_id=7,
                source_type="memory_item",
                source_ids=[95],
            )
            == 1
        )
        runtime_db.rollback()
        assert all(
            index.runtime_embedding_vector(source_type="memory_item", source_id=95) == vector
            for index in indexes
        )

        assert (
            sealed_runtime.delete_runtime_embedding_records(
                runtime_db,
                owner_id=7,
                source_type="memory_item",
                source_ids=[95],
            )
            == 1
        )
        runtime_db.commit()
        assert all(
            index.runtime_embedding_vector(source_type="memory_item", source_id=95) is None
            for index in indexes
        )


def test_runtime_embedding_cache_waits_for_outer_transaction_after_savepoint(
    monkeypatch,
) -> None:
    from anima_server.models.runtime_embedding import RuntimeEmbedding
    from anima_server.services.corefs import sealed_runtime
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
    from conftest_runtime import runtime_db_session

    indexes = (
        CoreFSProgressiveIndex("core-a"),
        CoreFSProgressiveIndex("core-a"),
    )
    for index in indexes:
        index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
    monkeypatch.setattr(sealed_runtime, "_active_runtime_index", lambda _user_id: indexes[0])
    monkeypatch.setattr(sealed_runtime, "active_runtime_indexes", lambda _user_id: indexes)
    vector = (1.0, 0.0)

    with runtime_db_session() as runtime_db:
        runtime_db.add(
            CoreFSRuntimeBinding(
                binding_slot=1,
                core_id="core-a",
                local_instance_id="instance-a",
            )
        )
        runtime_db.commit()

        with runtime_db.begin_nested():
            rolled_back = RuntimeEmbedding(
                user_id=7,
                source_type="memory_item",
                source_id=96,
                content_hash=RuntimeEmbedding.compute_content_hash("savepoint upsert"),
                content_preview="",
                category="fact",
                importance=5,
            )
            sealed_runtime.persist_runtime_embedding(
                runtime_db,
                row=rolled_back,
                owner_id=7,
                embedding=vector,
                content="savepoint upsert",
            )
            runtime_db.flush()

        assert all(
            index.runtime_embedding_vector(source_type="memory_item", source_id=96) is None
            for index in indexes
        )
        runtime_db.rollback()
        assert all(
            index.runtime_embedding_vector(source_type="memory_item", source_id=96) is None
            for index in indexes
        )

        committed = RuntimeEmbedding(
            user_id=7,
            source_type="memory_item",
            source_id=97,
            content_hash=RuntimeEmbedding.compute_content_hash("savepoint delete"),
            content_preview="",
            category="fact",
            importance=5,
        )
        sealed_runtime.persist_runtime_embedding(
            runtime_db,
            row=committed,
            owner_id=7,
            embedding=vector,
            content="savepoint delete",
        )
        runtime_db.flush()
        runtime_db.commit()

        with runtime_db.begin_nested():
            assert (
                sealed_runtime.delete_runtime_embedding_records(
                    runtime_db,
                    owner_id=7,
                    source_type="memory_item",
                    source_ids=[97],
                )
                == 1
            )

        assert all(
            index.runtime_embedding_vector(source_type="memory_item", source_id=97) == vector
            for index in indexes
        )
        runtime_db.rollback()
        assert all(
            index.runtime_embedding_vector(source_type="memory_item", source_id=97) == vector
            for index in indexes
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


def test_managed_test_client_provisions_core_before_runtime_claim() -> None:
    from conftest import managed_test_client

    with managed_test_client(
        "anima-corefs-runtime-registry-",
        invalidate_agent=False,
    ) as client:
        assert client.get("/health").status_code == 200


def test_production_embedding_writers_seal_content_previews(
    monkeypatch,
) -> None:
    from anima_server.models.runtime import RuntimeImageAsset
    from anima_server.models.runtime_embedding import RuntimeEmbedding
    from anima_server.services.agent.pgvec_store import PgVecStore
    from anima_server.services.corefs import sealed_runtime
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
    from anima_server.services.images.indexing import (
        _upsert_active_annotation,
    )
    from anima_server.services.images.indexing import (
        _upsert_runtime_embedding as upsert_image_embedding,
    )
    from anima_server.services.ingestion.retrieval import (
        _upsert_embedding as upsert_source_embedding,
    )
    from conftest_runtime import runtime_db_session

    index = CoreFSProgressiveIndex("core-a")
    index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
    monkeypatch.setattr(
        sealed_runtime,
        "_active_runtime_index",
        lambda _user_id: index,
    )
    dimension = RuntimeEmbedding.__table__.c.embedding.type.dim
    image_embedding = [1.0, *([0.0] * (dimension - 1))]
    source_embedding_vector = [0.0, 1.0, *([0.0] * (dimension - 2))]
    document_embedding = [1.0, 1.0, *([0.0] * (dimension - 2))]

    with runtime_db_session() as runtime_db:
        image = RuntimeImageAsset(
            user_id=7,
            filename="private.png",
            mime_type="image/png",
            storage_path="corefs://private.png",
            sha256="a" * 64,
            size_bytes=64,
        )
        runtime_db.add(image)
        runtime_db.flush()
        annotation = _upsert_active_annotation(
            runtime_db,
            user_id=7,
            image_asset_id=image.id,
            annotation_kind="ocr_text",
            content_text="private image embedding preview",
            source_model=None,
        )
        upsert_image_embedding(
            runtime_db,
            user_id=7,
            annotation=annotation,
            embedding=image_embedding,
        )
        source_embedding = upsert_source_embedding(
            runtime_db,
            user_id=7,
            source_type="source_span",
            source_id=99,
            text="private source embedding preview",
            category="source",
            importance=3,
            embedding_fn=lambda _text: source_embedding_vector,
        )
        PgVecStore(runtime_db).upsert_source(
            7,
            source_type="document_chunk",
            source_id=101,
            content="private document embedding preview",
            embedding=document_embedding,
        )
        runtime_db.flush()

        raw_rows = runtime_db.execute(
            select(
                RuntimeEmbedding.__table__.c.embedding,
                RuntimeEmbedding.__table__.c.embedding_checksum,
                RuntimeEmbedding.__table__.c.content_preview,
            ).order_by(RuntimeEmbedding.__table__.c.source_type)
        ).all()
        assert all(row[0] is None for row in raw_rows)
        assert all(row[1] is None for row in raw_rows)
        assert [row[2] for row in raw_rows] == ["", "", ""]
        runtime_db.commit()
        image_hits = PgVecStore(runtime_db).search_by_vector(
            7,
            query_embedding=image_embedding,
            source_types=["image_annotation"],
            limit=1,
        )
        source_hits = PgVecStore(runtime_db).search_by_vector(
            7,
            query_embedding=source_embedding_vector,
            source_types=["source_span"],
            limit=1,
        )
        sealed_count = len(
            runtime_db.scalars(
                select(CoreFSSealedPayload).where(
                    CoreFSSealedPayload.row_type == "runtime_embedding"
                )
            ).all()
        )
        runtime_db.expunge_all()
        hydrated = {
            row.source_type: row.content_preview
            for row in runtime_db.scalars(select(RuntimeEmbedding)).all()
        }

    assert source_embedding is not None
    assert sealed_count == 3
    assert [hit.item_id for hit in image_hits] == [annotation.id]
    assert [hit.item_id for hit in source_hits] == [99]
    assert hydrated == {
        "document_chunk": "private document embedding preview",
        "image_annotation": "private image embedding preview",
        "source_span": "private source embedding preview",
    }


def test_workflow_and_compiler_payloads_use_sealed_runtime_rows(
    monkeypatch,
) -> None:
    import json

    from anima_server.models.runtime import (
        RuntimeKnowledgeConcept,
        RuntimeKnowledgeConceptSource,
        RuntimeWorkflowCheckpoint,
        RuntimeWorkflowRun,
    )
    from anima_server.services.corefs import sealed_runtime
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
    from anima_server.services.ingestion.artifacts import (
        replace_source_artifacts_and_spans,
    )
    from anima_server.services.ingestion.compiler import compile_source_to_concepts
    from anima_server.services.ingestion.models import (
        SourceArtifactInput,
        SourceIdentity,
        SourceSpanInput,
    )
    from anima_server.services.ingestion.sources import register_source
    from anima_server.services.workflows.checkpoints import (
        append_checkpoint,
        mark_workflow_awaiting_input,
        start_workflow,
    )
    from conftest_runtime import runtime_db_session

    index = CoreFSProgressiveIndex("core-a")
    index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
    monkeypatch.setattr(
        sealed_runtime,
        "_active_runtime_index",
        lambda _user_id: index,
    )

    with runtime_db_session() as runtime_db:
        run = start_workflow(
            runtime_db,
            user_id=7,
            workflow_type="pdf_ingestion",
            input_json={
                "filename": "private.pdf",
                "storage_path": ".anima/documents/7/private.pdf",
            },
        )
        checkpoint = append_checkpoint(
            runtime_db,
            workflow_run_id=run.id,
            state_name="summarized",
            status="completed",
            idempotency_key="summary-1",
            input_json={"rejection_reason": "private workflow rejection reason"},
            output_json={"summary": "private workflow checkpoint"},
        )
        mark_workflow_awaiting_input(
            runtime_db,
            run,
            state_name="awaiting_approval",
            result_json={"proposed_facts": ["private workflow result"]},
        )

        source = register_source(
            runtime_db,
            SourceIdentity(
                user_id=7,
                kind="markdown",
                source_uri="file://private.md",
                content_hash="b" * 64,
                title="Private source",
                media_type="text/markdown",
            ),
        )
        _, spans = replace_source_artifacts_and_spans(
            runtime_db,
            source=source,
            artifacts=[
                SourceArtifactInput(
                    artifact_kind="plain_text",
                    content_text="private compiler evidence",
                    content_hash="c" * 64,
                )
            ],
            spans=[
                SourceSpanInput(
                    artifact_kind="plain_text",
                    span_kind="paragraph",
                    locator_json={"paragraph_index": 0},
                    content_text="private compiler evidence",
                    content_hash="d" * 64,
                )
            ],
        )
        compile_source_to_concepts(
            runtime_db,
            user_id=7,
            source_id=source.id,
            span_ids=[spans[0].id],
            model=lambda _request: json.dumps(
                {
                    "concepts": [
                        {
                            "type": "claim",
                            "slug": "private-claim",
                            "title": "Private claim",
                            "description": "private concept description",
                            "body_markdown": "private compiled body",
                            "tags": ["private-tag"],
                            "source_span_ids": [spans[0].id],
                        }
                    ],
                    "links": [],
                }
            ),
        )
        runtime_db.flush()

        raw_checkpoint = runtime_db.execute(
            select(
                RuntimeWorkflowCheckpoint.__table__.c.input_json,
                RuntimeWorkflowCheckpoint.__table__.c.output_json,
            ).where(RuntimeWorkflowCheckpoint.__table__.c.id == checkpoint.id)
        ).one()
        raw_result = runtime_db.execute(
            select(
                RuntimeWorkflowRun.__table__.c.input_json,
                RuntimeWorkflowRun.__table__.c.result_json,
            ).where(RuntimeWorkflowRun.__table__.c.id == run.id)
        ).one()
        raw_concept = runtime_db.execute(
            select(
                RuntimeKnowledgeConcept.__table__.c.title,
                RuntimeKnowledgeConcept.__table__.c.description,
                RuntimeKnowledgeConcept.__table__.c.body_markdown,
                RuntimeKnowledgeConcept.__table__.c.frontmatter_json,
            )
        ).one()
        raw_quote = runtime_db.execute(
            select(RuntimeKnowledgeConceptSource.__table__.c.quote_text)
        ).scalar_one()
        runtime_db.expunge_all()
        hydrated_run = runtime_db.get(RuntimeWorkflowRun, run.id)
        hydrated_checkpoint = runtime_db.get(RuntimeWorkflowCheckpoint, checkpoint.id)
        hydrated_concept = runtime_db.scalar(select(RuntimeKnowledgeConcept))
        hydrated_citation = runtime_db.scalar(select(RuntimeKnowledgeConceptSource))

    assert raw_checkpoint == (None, None)
    assert raw_result == (None, None)
    assert raw_concept == ("", None, "", {})
    assert raw_quote is None
    assert hydrated_run is not None
    assert hydrated_run.input_json == {
        "filename": "private.pdf",
        "storage_path": ".anima/documents/7/private.pdf",
    }
    assert hydrated_run.result_json == {"proposed_facts": ["private workflow result"]}
    assert hydrated_checkpoint is not None
    assert hydrated_checkpoint.input_json == {
        "rejection_reason": "private workflow rejection reason"
    }
    assert hydrated_checkpoint.output_json == {"summary": "private workflow checkpoint"}
    assert hydrated_concept is not None
    assert hydrated_concept.title == "Private claim"
    assert hydrated_concept.description == "private concept description"
    assert hydrated_concept.body_markdown == "private compiled body"
    assert hydrated_concept.frontmatter_json["tags"] == ["private-tag"]
    assert hydrated_citation is not None
    assert hydrated_citation.quote_text == "private compiler evidence"


def test_conversation_derived_thread_title_uses_sealed_runtime_row(
    monkeypatch,
) -> None:
    from anima_server.models.runtime import RuntimeThread
    from anima_server.services.agent.thread_manager import maybe_set_thread_title
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
        thread = RuntimeThread(user_id=7, status="active")
        runtime_db.add(thread)
        runtime_db.flush()

        maybe_set_thread_title(
            runtime_db,
            thread,
            "Can you help me diagnose the private memory retrieval issue?",
        )
        raw_title = runtime_db.execute(
            select(RuntimeThread.__table__.c.title).where(RuntimeThread.__table__.c.id == thread.id)
        ).scalar_one()
        runtime_db.expunge_all()
        hydrated_thread = runtime_db.get(RuntimeThread, thread.id)

    assert raw_title is None
    assert hydrated_thread is not None
    assert hydrated_thread.title == "Diagnose the private memory retrieval issue"


def test_embedding_deletion_and_rebuild_remove_sealed_previews(
    monkeypatch,
) -> None:
    from anima_server.models.runtime_embedding import RuntimeEmbedding
    from anima_server.services.agent.pgvec_store import PgVecStore
    from anima_server.services.corefs import sealed_runtime
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
    from conftest_runtime import runtime_db_session
    from sqlalchemy import func

    index = CoreFSProgressiveIndex("core-a")
    index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
    monkeypatch.setattr(
        sealed_runtime,
        "_active_runtime_index",
        lambda _user_id: index,
    )
    embedding = [0.0] * RuntimeEmbedding.__table__.c.embedding.type.dim

    with runtime_db_session() as runtime_db:
        store = PgVecStore(runtime_db)
        store.upsert_source(
            7,
            source_type="document_chunk",
            source_id=11,
            content="private document preview",
            embedding=embedding,
        )
        store.upsert(
            7,
            item_id=12,
            content="old private memory preview",
            embedding=embedding,
        )
        store.delete_source(7, source_type="document_chunk", source_id=11)
        after_delete = runtime_db.scalar(
            select(func.count(CoreFSSealedPayload.id)).where(
                CoreFSSealedPayload.row_type == "runtime_embedding"
            )
        )

        store.rebuild(
            7,
            [(13, "new private memory preview", embedding, "fact", 3)],
        )
        after_rebuild = runtime_db.scalar(
            select(func.count(CoreFSSealedPayload.id)).where(
                CoreFSSealedPayload.row_type == "runtime_embedding"
            )
        )
        runtime_db.expunge_all()
        hydrated = runtime_db.scalar(
            select(RuntimeEmbedding).where(
                RuntimeEmbedding.user_id == 7,
                RuntimeEmbedding.source_type == "memory_item",
            )
        )

    assert after_delete == 1
    assert after_rebuild == 1
    assert hydrated is not None
    assert hydrated.source_id == 13
    assert hydrated.content_preview == "new private memory preview"


def test_eval_reset_purges_all_owner_bound_sealed_rows(
    monkeypatch,
) -> None:
    from anima_server.services.corefs import sealed_runtime
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
    from anima_server.services.eval_reset import _reset_runtime_state
    from conftest_runtime import runtime_db_session

    index = CoreFSProgressiveIndex("core-a")
    index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
    monkeypatch.setattr(
        sealed_runtime,
        "_active_runtime_index",
        lambda _user_id: index,
    )

    with runtime_db_session() as runtime_db:
        sealed_runtime.seal_runtime_record(
            runtime_db,
            index=index,
            row_type="runtime_message",
            row_id=101,
            owner_id=7,
            payload={"content_text": "eval private"},
        )
        sealed_runtime.seal_runtime_record(
            runtime_db,
            index=index,
            row_type="runtime_message",
            row_id=202,
            owner_id=8,
            payload={"content_text": "other private"},
        )

        _reset_runtime_state(runtime_db, user_id=7, deleted={})

        remaining = runtime_db.scalars(select(CoreFSSealedPayload)).all()
        other_payload = sealed_runtime.load_runtime_record(
            runtime_db,
            row_type="runtime_message",
            row_id=202,
            owner_id=8,
        )

    assert len(remaining) == 1
    assert other_payload == {"content_text": "other private"}
