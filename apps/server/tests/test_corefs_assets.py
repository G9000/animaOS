from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import anima_core
import pytest
from anima_server.config import settings
from anima_server.db import runtime as runtime_mod
from anima_server.db.base import Base
from anima_server.models.runtime import (
    RuntimeDocument,
    RuntimeImageAsset,
    RuntimeSource,
    RuntimeSourceArtifact,
)
from anima_server.services.corefs import logical
from anima_server.services.corefs.asset_authority import CoreFsSourceError
from anima_server.services.corefs.asset_migration import (
    PortableBinaryAssetSource,
    PortableKnowledgeSource,
    build_portable_asset_shadow,
    collect_portable_asset_shadow,
)
from anima_server.services.corefs.conversation_migration import (
    build_conversation_shadow_catalog,
)
from anima_server.services.corefs.diary_migration import (
    migration_opaque_id,
    read_prepared_writing_body,
    read_prepared_writing_snapshot,
)
from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
from anima_server.services.corefs.messages import decode_message_segment
from anima_server.services.corefs.migration import (
    _walk_authenticated_files,
    rebuild_unlocked_search,
)
from anima_server.services.corefs.writing_source import prepare_writing_source_catalog
from anima_server.services.images.deletion import forget_image_asset
from anima_server.services.images.store import resolve_projected_image_byte_source
from pdf_fixtures import write_text_pdf
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def test_gallery_shadow_keeps_original_bytes_and_opaque_references(tmp_path: Path) -> None:
    image = tmp_path / "private-user-image.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nportable-image")
    digest = hashlib.sha256(image.read_bytes()).hexdigest()

    shadow = build_portable_asset_shadow(
        user_id=7,
        binary_assets=[
            PortableBinaryAssetSource(
                namespace="image-asset",
                legacy_id="8",
                name="portrait.png",
                kind="gallery-asset",
                content_type="image/png",
                path=image,
                size=image.stat().st_size,
                sha256=digest,
                created_at="2026-08-13T00:00:00.000000+00:00",
                updated_at="2026-08-13T00:00:00.000000+00:00",
                metadata={"width": 1, "height": 1},
            )
        ],
    )

    assert len(shadow.folders) == 1
    gallery = shadow.folders[0]
    assert gallery.role == "core.gallery"
    assert (gallery.owner, gallery.agent_access, gallery.policy) == (
        "user",
        "write",
        "user-write",
    )
    asset = shadow.objects[0]
    expected_id = migration_opaque_id("image-asset", "8")
    assert asset.descriptor.stable_id == expected_id
    assert asset.descriptor.parent_id == gallery.stable_id
    assert asset.descriptor.body_source == "supplemental_path"
    assert asset.descriptor.source_key == str(image)
    assert asset.descriptor.content_sha256 == digest
    assert asset.body is None
    assert str(image) not in repr(asset.descriptor.metadata)
    assert shadow.resolve_reference({"assetId": 8}) == f"corefs://object/{expected_id}"


def test_gallery_reference_map_reconciles_message_attachment_links(tmp_path: Path) -> None:
    image = tmp_path / "linked.webp"
    image.write_bytes(b"RIFF\x00\x00\x00\x00WEBPportable")
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    shadow = build_portable_asset_shadow(
        user_id=7,
        binary_assets=[
            PortableBinaryAssetSource(
                namespace="image-asset",
                legacy_id="9",
                name="linked.webp",
                kind="gallery-asset",
                content_type="image/webp",
                path=image,
                size=image.stat().st_size,
                sha256=digest,
                created_at="2026-08-13T00:00:00.000000+00:00",
                updated_at="2026-08-13T00:00:00.000000+00:00",
            )
        ],
        attachment_links={"message-attachment-9": "9"},
    )

    expected_id = migration_opaque_id("image-asset", "9")
    assert shadow.resolve_reference({"id": "message-attachment-9"}) == (
        f"corefs://object/{expected_id}"
    )
    assert shadow.resolve_reference({"sha256": digest}) == (f"corefs://object/{expected_id}")
    assert shadow.resolve_reference({"storagePath": str(image)}) is None


def test_runtime_collector_keeps_originals_and_excludes_derived_families(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)  # type: ignore[attr-defined]
    image_data = b"\x89PNG\r\n\x1a\nportable-runtime-image"
    image_hash = hashlib.sha256(image_data).hexdigest()
    image_storage = f"users/7/media/images/{image_hash[:2]}/{image_hash}.png"
    image_path = tmp_path / image_storage
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(image_data)

    document_data = b"%PDF-1.7\nportable-runtime-document\n%%EOF"
    document_hash = hashlib.sha256(document_data).hexdigest()
    document_storage = "users/7/attachments/report.pdf"
    document_path = tmp_path / document_storage
    document_path.parent.mkdir(parents=True)
    document_path.write_bytes(document_data)
    avatar_path = tmp_path / "users/7/avatars/agent.webp"
    avatar_path.parent.mkdir(parents=True)
    avatar_path.write_bytes(b"RIFF\x00\x00\x00\x00WEBPportable-avatar")
    now = datetime(2026, 8, 13, tzinfo=UTC)
    source_text = "# Exact authored source\n"
    source_hash = hashlib.sha256(source_text.encode()).hexdigest()

    factory = runtime_mod.get_runtime_session_factory()
    with factory() as runtime_db:
        image = RuntimeImageAsset(
            user_id=7,
            filename="portrait.png",
            mime_type="image/png",
            storage_path=image_storage,
            sha256=image_hash,
            size_bytes=len(image_data),
            status="ready",
            retention_state="durable",
            metadata_json={"storagePath": "must-not-survive", "caption": "private"},
            created_at=now,
            updated_at=now,
        )
        document = RuntimeDocument(
            user_id=7,
            filename="report.pdf",
            mime_type="application/pdf",
            storage_path=document_storage,
            sha256=document_hash,
            size_bytes=len(document_data),
            status="ready",
            parse_quality="full",
            metadata_json={"path": "/private/report.pdf", "title": "Private"},
            created_at=now,
            updated_at=now,
        )
        source = RuntimeSource(
            user_id=7,
            kind="markdown",
            source_uri="markdown://notes.md",
            content_hash=source_hash,
            title="Notes",
            media_type="text/markdown",
            status="indexed",
            metadata_json={"filename": "notes.md"},
            created_at=now,
            updated_at=now,
        )
        runtime_db.add_all((image, document, source))
        runtime_db.flush()
        runtime_db.add(
            RuntimeSourceArtifact(
                user_id=7,
                source_id=source.id,
                artifact_kind="markdown",
                content_text=source_text,
                content_hash=source_hash,
                metadata_json={"filename": "notes.md"},
                created_at=now,
                updated_at=now,
            )
        )
        runtime_db.commit()

        soul_db = MagicMock()
        soul_db.scalar.return_value = SimpleNamespace(
            avatar_url="/consciousness/7/agent-profile/avatar",
            created_at=now,
            updated_at=now,
        )
        shadow = collect_portable_asset_shadow(
            soul_db=soul_db,
            runtime_db=runtime_db,
            user_id=7,
        )

    assert {item.descriptor.kind for item in shadow.objects} == {
        "attachment",
        "gallery-asset",
        "knowledge-source",
    }
    assert sum(item.descriptor.kind == "gallery-asset" for item in shadow.objects) == 2
    assert any(
        item.descriptor.metadata.get("origin") == "identity-avatar" for item in shadow.objects
    )
    assert not any(
        key in repr(item.descriptor.metadata)
        for item in shadow.objects
        for key in (str(tmp_path), "must-not-survive", "/private/report.pdf")
    )
    knowledge = next(item for item in shadow.objects if item.descriptor.kind == "knowledge-source")
    assert knowledge.body == source_text.encode()


def test_post_cutover_delete_fails_before_touching_legacy_runtime(
    monkeypatch: object,
) -> None:
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "anima_server.services.corefs.asset_authority.active_asset_authority_session",
        lambda _user_id: object(),
    )
    runtime_db = MagicMock()

    with pytest.raises(CoreFsSourceError, match="disabled after CoreFS"):
        forget_image_asset(runtime_db, user_id=7, image_asset_id=4)

    runtime_db.scalar.assert_not_called()


def test_combined_native_publication_references_gallery_and_survives_restart_rerun(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "anima_server.services.core.update_core_manifest", lambda _update: None
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "anima_server.services.core.get_manifest_path",
        lambda: tmp_path / "missing-manifest.json",
    )
    engine = create_engine(f"sqlite:///{(tmp_path / 'source.db').as_posix()}")
    Base.metadata.create_all(engine)
    native = anima_core.CorefsSession(
        str(tmp_path / "core"),
        migration_opaque_id("test-core", "combined-gallery"),
    )
    keys = anima_core.corefs_derive_subkeys(anima_core.corefs_generate_root_key(), 1)
    session = SimpleNamespace(user_id=7, corefs_session=native, corefs_keys=keys)
    image = tmp_path / "original.png"
    image.write_bytes(b"portable original image")
    image_hash = hashlib.sha256(image.read_bytes()).hexdigest()
    document = tmp_path / "offline.pdf"
    write_text_pdf(document, "Offline canonical document relay guide")
    document_hash = hashlib.sha256(document.read_bytes()).hexdigest()
    assets = build_portable_asset_shadow(
        user_id=7,
        binary_assets=[
            PortableBinaryAssetSource(
                namespace="image-asset",
                legacy_id="8",
                name="original.png",
                kind="gallery-asset",
                content_type="image/png",
                path=image,
                size=image.stat().st_size,
                sha256=image_hash,
                created_at="2026-08-13T00:00:00Z",
                updated_at="2026-08-13T00:00:00Z",
            ),
            PortableBinaryAssetSource(
                namespace="document",
                legacy_id="23",
                name="offline.pdf",
                kind="attachment",
                content_type="application/pdf",
                path=document,
                size=document.stat().st_size,
                sha256=document_hash,
                created_at="2026-08-13T00:00:00Z",
                updated_at="2026-08-13T00:00:00Z",
            ),
        ],
        knowledge_sources=[
            PortableKnowledgeSource(
                legacy_id="41:raw_html",
                name="capture.raw.html",
                content_type="text/html; charset=utf-8",
                data=b"<p>offline canonical source</p>",
                created_at="2026-08-13T00:00:00Z",
                updated_at="2026-08-13T00:00:00Z",
                metadata={
                    "sourceId": 41,
                    "artifactId": 42,
                    "sourceKind": "web_capture",
                    "sourceUri": "https://example.test/offline",
                    "sourceTitle": "Offline source",
                    "sourceMediaType": "text/html",
                    "artifactKind": "raw_html",
                },
            )
        ],
    )
    conversations = build_conversation_shadow_catalog(
        user_id=7,
        active_messages=[
            {
                "id": 1,
                "thread_id": 4,
                "sequence_id": 1,
                "role": "user",
                "content_text": "see original",
                "content_json": {"attachments": [{"assetId": 8}]},
                "created_at": datetime(2026, 8, 13, tzinfo=UTC),
            }
        ],
        attachment_resolver=assets.resolve_reference,
    )
    with Session(engine) as db:
        first = prepare_writing_source_catalog(
            session=session,
            db=db,
            supplemental_folders=(*conversations.folders, *assets.folders),
            supplemental_objects=(*conversations.objects, *assets.objects),
        )
    assert first.published is True
    prepared = read_prepared_writing_snapshot(session=session)
    assert {folder.role for folder in prepared.folders} >= {
        "core.conversations",
        "core.gallery",
        "core.journal",
        "core.notes",
    }
    prepared_asset = next(item for item in prepared.objects if item.kind == "gallery-asset")
    assert read_prepared_writing_body(session=session, item=prepared_asset) == image.read_bytes()
    prepared_source = next(item for item in prepared.objects if item.kind == "knowledge-source")
    assert read_prepared_writing_body(session=session, item=prepared_source) == (
        b"<p>offline canonical source</p>"
    )
    segment_item = next(item for item in prepared.objects if item.kind == "message-segment")
    segment = decode_message_segment(
        read_prepared_writing_body(session=session, item=segment_item),
        expected_previous_segment_id=None,
        expected_previous_sha256=None,
    )
    assert segment.events[0].attachment_uris == (f"corefs://object/{prepared_asset.stable_id}",)
    index = CoreFSProgressiveIndex("combined-gallery")
    index.unlock(sqlcipher_key=b"s" * 32, local_instance_id="instance-a")
    session.runtime_index = index
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "anima_server.services.documents.parsing.parsing_pack_ready", lambda: False
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "anima_server.services.documents.parsing.ensure_parsing_pack", lambda: None
    )
    selected = logical.select_validation_snapshot(corefs_session=native, keys=keys)
    walked, failures = _walk_authenticated_files(
        corefs_session=native,
        keys=keys,
        selected=selected,
    )
    assert failures == []
    walked_document = next(item for item in walked if item["family"] == "attachment")
    assert walked_document["metadata"] == {
        "legacyId": "23",
        "mimeType": "application/pdf",
        "origin": "document",
        "originalName": "offline.pdf",
        "sha256": document_hash,
        "sizeBytes": document.stat().st_size,
    }
    first_chunk = json.loads(
        logical.read_chunk_v1(
            corefs_session=native,
            keys=keys,
            selected=selected,
            path=walked_document["path"],
            offset=0,
            max_bytes=document.stat().st_size,
        )
    )["result"]
    assert (
        first_chunk["stableId"],
        first_chunk["contentHash"],
        first_chunk["offset"],
    ) == (
        walked_document["stable_id"],
        walked_document["content_hash"],
        0,
    )
    rebuild_unlocked_search(session)
    assert index.search_text("offline canonical source") == (prepared_source.stable_id,)
    knowledge_projection = index.knowledge_source_projections()
    assert len(knowledge_projection) == 1
    assert knowledge_projection[0].source_id == 41
    assert knowledge_projection[0].content_text == "<p>offline canonical source</p>"
    assert index.search_text("portable original image") == ()
    image_projection = index.image_projection(8)
    assert image_projection is not None
    assert image_projection.stable_id == prepared_asset.stable_id
    assert image_projection.filename == "original.png"
    session.content_authority = {
        "version": 1,
        "state": "cutover_complete",
        "families": ["assets", "documents", "knowledge"],
        "generation": selected.generation,
        "catalogHash": selected.catalog_hash,
    }
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "anima_server.services.corefs.asset_authority.active_asset_authority_session",
        lambda user_id: session if user_id == 7 else None,
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "anima_server.services.corefs.asset_authority.authenticated_content_authority",
        lambda current, *, family: current.content_authority,
    )
    projected_source = resolve_projected_image_byte_source(
        user_id=7,
        image_asset_id=8,
    )
    assert projected_source is not None
    assert projected_source.read_all() == image.read_bytes()
    projection = index.document_projection(23)
    assert projection is not None, index.snapshot()
    assert projection.filename == "offline.pdf"
    assert projection.chunks[0].content_text.startswith("Offline canonical docum")
    index.clear_unlocked_state()
    sensitive = index.sensitive_buffer_counts()
    assert sensitive["document_projections"] == 0
    assert sensitive["image_projections"] == 0
    assert sensitive["knowledge_source_projections"] == 0
    assert sensitive["knowledge_concept_projections"] == 0

    native.close()
    restarted_native = anima_core.CorefsSession(
        str(tmp_path / "core"),
        migration_opaque_id("test-core", "combined-gallery"),
    )
    restarted_session = SimpleNamespace(
        user_id=7,
        corefs_session=restarted_native,
        corefs_keys=keys,
    )
    with Session(engine) as db:
        repeated = prepare_writing_source_catalog(session=restarted_session, db=db)
    assert repeated.published is False
    after = read_prepared_writing_snapshot(session=restarted_session)
    assert {item.stable_id for item in after.objects} == {
        item.stable_id for item in prepared.objects
    }
