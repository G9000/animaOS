from __future__ import annotations

import base64
import hashlib
import json
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import anima_core
import anima_server.services.corefs.writing_source as writing_source
import pytest
from anima_server.db.base import Base
from anima_server.schemas.diary import DIARY_BODY_MAX_LENGTH, DiaryDraftImportRequest
from anima_server.services.corefs.diary_migration import (
    DiaryMigrationError,
    InactiveFolder,
    LegacyDiaryAttachment,
    LegacyDiaryDraft,
    LegacyDiaryEntry,
    LegacyDiaryFolder,
    LegacyNote,
    build_inactive_diary_catalog,
    migration_opaque_id,
    prepare_diary_validation_catalog,
    read_prepared_writing_body,
    read_prepared_writing_objects,
)
from anima_server.services.corefs.formats import (
    MAX_WRITING_BODY_CHARACTERS,
    CoreFormatError,
    canonicalize_diary_html,
    decode_diary_document,
    decode_draft_document,
    encode_diary_document,
    encode_note_document,
)
from anima_server.services.corefs.writing_source import (
    WritingSourceInventory,
    WritingSourceObjectDescriptor,
    iter_writing_source_objects,
)
from corefs_writing_test_support import publish_catalog_native
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


class _PreparationFaultProxy:
    def __init__(
        self,
        native: object,
        *,
        fail_after_object: int | None = None,
        fail_finalize_before: bool = False,
        fail_finalize_after: bool = False,
    ) -> None:
        self.native = native
        self.fail_after_object = fail_after_object
        self.fail_finalize_before = fail_finalize_before
        self.fail_finalize_after = fail_finalize_after
        self.prepared_objects = 0

    def __getattr__(self, name: str) -> object:
        return getattr(self.native, name)

    def preparation_prepare_object_v1(
        self, keys: object, request: str, body: bytes
    ) -> object:
        result = self.native.preparation_prepare_object_v1(keys, request, body)  # type: ignore[attr-defined]
        self.prepared_objects += 1
        if self.prepared_objects == self.fail_after_object:
            raise RuntimeError("simulated crash after durable prepared object")
        return result

    def preparation_finalize_v1(self, keys: object, request: str) -> object:
        if self.fail_finalize_before:
            raise RuntimeError("simulated native finalization failure")
        result = self.native.preparation_finalize_v1(keys, request)  # type: ignore[attr-defined]
        if self.fail_finalize_after:
            raise RuntimeError("simulated crash after native finalization")
        return result


class _PreparationStatusFailure:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def preparation_status_v1(self, _keys: object) -> object:
        raise self.error


class _ReadySourceDriftNative:
    def __init__(self, head: dict[str, object] | ValueError) -> None:
        self.head = head
        self.finalize_requests: list[dict[str, object]] = []

    def validation_snapshot(self, _keys: object) -> dict[str, object]:
        if isinstance(self.head, ValueError):
            raise self.head
        return self.head

    def preparation_finalize_v1(self, _keys: object, request: str) -> dict[str, object]:
        self.finalize_requests.append(json.loads(request))
        return {
            "validationGeneration": 8,
            "validationCatalogSha256": "8" * 64,
        }


def test_attachment_metadata_reencoding_uses_inventory_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        SimpleNamespace(
            id=3,
            entry_id=7,
            kind="file",
            mime_type="text/plain",
            size_bytes=3,
            storage_path="three",
            sha256="3" * 64,
            original_filename="three.txt",
            caption=None,
            created_at=datetime(2026, 8, 2, 0, 0, 1, tzinfo=UTC),
        ),
        SimpleNamespace(
            id=2,
            entry_id=7,
            kind="file",
            mime_type="text/plain",
            size_bytes=2,
            storage_path="two",
            sha256="2" * 64,
            original_filename="two.txt",
            caption=None,
            created_at=datetime(2026, 8, 2, tzinfo=UTC),
        ),
        SimpleNamespace(
            id=1,
            entry_id=7,
            kind="file",
            mime_type="text/plain",
            size_bytes=1,
            storage_path="one",
            sha256="1" * 64,
            original_filename="one.txt",
            caption=None,
            created_at=datetime(2026, 8, 2, tzinfo=UTC),
        ),
    ]
    db = SimpleNamespace(execute=lambda _statement: SimpleNamespace(all=lambda: rows))
    monkeypatch.setattr(
        "anima_server.services.data_crypto.df",
        lambda _user_id, value, **_kwargs: value,
    )

    grouped = writing_source._attachment_metadata_by_entry(db, user_id=9)

    assert [item.id for item in grouped[7]] == [1, 2, 3]


def test_preparation_status_only_treats_the_exact_absent_condition_as_missing() -> None:
    assert (
        writing_source._preparation_status_or_none(
            _PreparationStatusFailure(ValueError("no active preparation exists")),
            object(),
        )
        is None
    )

    with pytest.raises(RuntimeError, match="snapshot is missing"):
        writing_source._preparation_status_or_none(
            _PreparationStatusFailure(
                RuntimeError("the active preparation snapshot is missing")
            ),
            object(),
        )


def _ready_source_drift_status() -> dict[str, object]:
    return {
        "preparationId": "01J20000000000000000000000",
        "snapshotSequence": 7,
        "pointerSha256": "7" * 64,
        "sourceMutationGeneration": 3,
        "sourceInventorySha256": "3" * 64,
        "expectedValidationGeneration": 7,
        "expectedValidationCatalogSha256": "6" * 64,
        "intendedValidationGeneration": 8,
        "intendedValidationCatalogSha256": "8" * 64,
    }


def test_ready_source_drift_recovers_an_already_published_head() -> None:
    native = _ReadySourceDriftNative(
        {"generation": 8, "catalogHash": "8" * 64}
    )

    outcome = writing_source._reconcile_ready_source_drift(
        native, object(), _ready_source_drift_status()
    )

    assert outcome == "recovered"
    assert native.finalize_requests == [
        {
            "expected": {
                "pointerSha256": "7" * 64,
                "snapshotSequence": 7,
            },
            "preparationId": "01J20000000000000000000000",
            "sourceInventorySha256": "3" * 64,
            "sourceMutationGeneration": 3,
        }
    ]


def test_ready_source_drift_only_abandons_an_unpublished_head() -> None:
    native = _ReadySourceDriftNative(
        {"generation": 7, "catalogHash": "6" * 64}
    )

    assert (
        writing_source._reconcile_ready_source_drift(
            native, object(), _ready_source_drift_status()
        )
        == "unpublished"
    )
    assert native.finalize_requests == []


def test_ready_source_drift_preserves_a_conflicting_head() -> None:
    native = _ReadySourceDriftNative(
        {"generation": 9, "catalogHash": "9" * 64}
    )

    with pytest.raises(DiaryMigrationError, match="conflicts with the ready preparation"):
        writing_source._reconcile_ready_source_drift(
            native, object(), _ready_source_drift_status()
        )
    assert native.finalize_requests == []


def test_exact_inventory_match_authenticates_each_durable_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = WritingSourceObjectDescriptor(
        stable_id="01J20000000000000000000002",
        parent_id="01J20000000000000000000001",
        name="entry.diary.json",
        kind="diary",
        content_type="application/vnd.anima.diary+json",
        body_encoding="utf8",
        body_length=4,
        content_sha256="a" * 64,
        source_fingerprint_sha256="b" * 64,
        created_at="2026-08-02T00:00:00Z",
        updated_at="2026-08-02T00:00:00Z",
        revision=1,
        metadata={"legacyId": 1},
    )
    folder = SimpleNamespace(
        stable_id="01J20000000000000000000001",
        parent_id=None,
        name="Journal",
        role="core.journal",
    )
    current = SimpleNamespace(
        stable_id=descriptor.stable_id,
        content_hash=descriptor.content_sha256,
        parent_id=descriptor.parent_id,
        name=descriptor.name,
        kind=descriptor.kind,
        content_type=descriptor.content_type,
        body_encoding=descriptor.body_encoding,
        body_length=descriptor.body_length,
        created_at=descriptor.created_at,
        updated_at=descriptor.updated_at,
        metadata=descriptor.metadata,
    )
    inventory = WritingSourceInventory(
        source_generation=1,
        source_digest="c" * 64,
        expected_head=(1, "d" * 64),
        folders=(folder,),
        objects=(descriptor,),
        source_counts={"diary": 1},
    )
    monkeypatch.setattr(
        "anima_server.services.corefs.diary_migration.read_prepared_writing_snapshot",
        lambda **_kwargs: SimpleNamespace(folders=(folder,), objects=(current,)),
    )
    monkeypatch.setattr(
        "anima_server.services.corefs.diary_migration.read_prepared_writing_body",
        lambda **_kwargs: (_ for _ in ()).throw(DiaryMigrationError("corrupt body")),
    )

    with pytest.raises(DiaryMigrationError, match="corrupt body"):
        writing_source._inventory_matches_current(session=object(), inventory=inventory)


def test_matching_checkpoint_cannot_bypass_exact_body_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory = WritingSourceInventory(
        source_generation=9,
        source_digest="c" * 64,
        expected_head=(4, "d" * 64),
        folders=(),
        objects=(),
        source_counts={},
    )
    session = SimpleNamespace(user_id=7, corefs_session=object(), corefs_keys=object())
    monkeypatch.setattr(
        writing_source, "build_writing_source_inventory", lambda **_kwargs: inventory
    )
    monkeypatch.setattr(writing_source, "_preparation_status_or_none", lambda *_args: None)
    monkeypatch.setattr(
        writing_source,
        "_inventory_matches_current",
        lambda **_kwargs: (_ for _ in ()).throw(DiaryMigrationError("corrupt body")),
    )

    with pytest.raises(DiaryMigrationError, match="corrupt body"):
        writing_source.prepare_writing_source_catalog(session=session, db=object())


def test_legacy_local_storage_draft_extracts_and_deduplicates_inline_images() -> None:
    encoded = base64.b64encode(b"draft-image").decode()
    catalog = build_inactive_diary_catalog(
        user_id=1,
        folders=(),
        entries=(),
        drafts=(
            LegacyDiaryDraft(
                id="local-storage",
                target_entry_id=None,
                body=(
                    f'<p><img src="data:image/png;base64,{encoded}">'
                    f'<img src="data:image/png;base64,{encoded}"></p>'
                ),
                content_type="text/html",
                updated_at="2026-08-02T00:00:00Z",
            ),
        ),
    )
    draft_id = migration_opaque_id("diary-draft", "local-storage")
    media_id = migration_opaque_id("diary-inline-media", hashlib.sha256(b"draft-image").hexdigest())
    draft = catalog.object(draft_id)
    decoded = decode_draft_document(draft.content)
    assert decoded.body.count(f"corefs://object/{media_id}") == 2
    assert draft.references == (media_id,)
    assert catalog.object(media_id).content == b"draft-image"
    assert catalog.object(media_id).metadata["origin"] == "legacy-local-storage-draft"


def test_poison_validation_head_is_checkpointed_and_never_reinitialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "core"
    native = anima_core.CorefsSession(
        str(root), migration_opaque_id("test-core", "poison-writing-head")
    )
    keys = anima_core.corefs_derive_subkeys(anima_core.corefs_generate_root_key(), 1)
    publish_catalog_native(
        build_inactive_diary_catalog(user_id=7, folders=(), entries=()),
        corefs_session=native,
        keys=keys,
    )
    head_path = root / "fs" / "VALIDATION_HEAD"
    poison = b"authenticated-head-was-corrupted"
    head_path.write_bytes(poison)
    manifest: dict[str, object] = {}

    def update(mutator: object) -> None:
        assert callable(mutator)
        mutator(manifest)

    monkeypatch.setattr("anima_server.services.core.update_core_manifest", update)
    with pytest.raises(DiaryMigrationError, match="could not be opened"):
        prepare_diary_validation_catalog(
            session=SimpleNamespace(
                user_id=7,
                corefs_session=native,
                corefs_keys=keys,
            ),
            db=object(),  # failure occurs before any SQL access
        )
    assert head_path.read_bytes() == poison
    checkpoint = manifest["migration_checkpoints"]["pcf004:7"]  # type: ignore[index]
    assert checkpoint["state"] == "retry-required"
    assert checkpoint["authoritative"] is False


def test_public_draft_schema_round_trips_four_byte_unicode_through_native_boundary(
    tmp_path: Path,
) -> None:
    assert DIARY_BODY_MAX_LENGTH == MAX_WRITING_BODY_CHARACTERS == 20_000_000
    html = f"<p>{chr(0x10FFFF) * 1024}</p>"
    payload = DiaryDraftImportRequest(
        userId=1,
        draftId="public-boundary",
        clientRevision=1,
        contentSha256=hashlib.sha256(html.encode()).hexdigest(),
        html=html,
        title="",
        mood="",
        entryDate="2026-08-02",
        updatedAt="2026-08-02T00:00:00Z",
    )
    catalog = build_inactive_diary_catalog(
        user_id=1,
        folders=(),
        entries=(),
        drafts=(
            LegacyDiaryDraft(
                id=payload.draftId,
                target_entry_id=None,
                body=payload.html,
                content_type="text/html",
                updated_at="2026-08-02T00:00:00Z",
            ),
        ),
    )
    native = anima_core.CorefsSession(
        str(tmp_path / "core"), migration_opaque_id("test-core", "public-writing-boundary")
    )
    keys = anima_core.corefs_derive_subkeys(anima_core.corefs_generate_root_key(), 1)
    first = publish_catalog_native(catalog, corefs_session=native, keys=keys)
    draft_id = migration_opaque_id("diary-draft", payload.draftId)
    prepared = read_prepared_writing_objects(
        session=SimpleNamespace(corefs_session=native, corefs_keys=keys)
    )
    draft_item = next(item for item in prepared if item.stable_id == draft_id)
    decoded = decode_draft_document(
        read_prepared_writing_body(
            session=SimpleNamespace(corefs_session=native, corefs_keys=keys),
            item=draft_item,
        )
    )
    assert decoded.body == html
    before = native.validation_snapshot(keys)
    assert before["generation"] == first["generation"]
    with pytest.raises(ValidationError):
        DiaryDraftImportRequest(
            userId=1,
            draftId="oversized",
            clientRevision=1,
            contentSha256=hashlib.sha256(("x" * (DIARY_BODY_MAX_LENGTH + 1)).encode()).hexdigest(),
            html="x" * (DIARY_BODY_MAX_LENGTH + 1),
            title="",
            mood="",
            entryDate="2026-08-02",
            updatedAt="2026-08-02T00:00:00Z",
        )
    assert native.validation_snapshot(keys) == before


def test_restart_rebuild_preserves_extracted_draft_attachment_exactly_and_is_a_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("anima_server.services.core.update_core_manifest", lambda _mutator: None)
    engine = create_engine(f"sqlite:///{(tmp_path / 'source.db').as_posix()}")
    Base.metadata.create_all(engine)
    root = tmp_path / "core"
    keys = anima_core.corefs_derive_subkeys(anima_core.corefs_generate_root_key(), 1)
    core_id = migration_opaque_id("test-core", "draft-attachment-rebuild")
    first_session = anima_core.CorefsSession(str(root), core_id)
    image = b"restart-safe-image"
    body = '<p><img src="data:image/png;base64,' + base64.b64encode(image).decode() + '"></p>'
    with Session(engine) as db:
        first = prepare_diary_validation_catalog(
            session=SimpleNamespace(
                user_id=41,
                corefs_session=first_session,
                corefs_keys=keys,
            ),
            db=db,
            staged_drafts=(
                LegacyDiaryDraft(
                    id="restart-draft",
                    target_entry_id=None,
                    body=body,
                    content_type="text/html",
                    updated_at="2026-08-02T00:00:00Z",
                    native_metadata={},  # pre-source-count validation envelope
                ),
            ),
        )
    media_id = migration_opaque_id("diary-inline-media", hashlib.sha256(image).hexdigest())
    before = read_prepared_writing_objects(
        session=SimpleNamespace(corefs_session=first_session, corefs_keys=keys)
    )
    attachment_before = next(item for item in before if item.stable_id == media_id)

    del first_session
    restarted = anima_core.CorefsSession(str(root), core_id)
    with Session(engine) as db:
        second = prepare_diary_validation_catalog(
            session=SimpleNamespace(
                user_id=41,
                corefs_session=restarted,
                corefs_keys=keys,
            ),
            db=db,
        )
    after = read_prepared_writing_objects(
        session=SimpleNamespace(corefs_session=restarted, corefs_keys=keys)
    )
    attachment_after = next(item for item in after if item.stable_id == media_id)

    assert second.published is False
    assert second.generation == first.generation
    assert attachment_after == attachment_before
    draft_item = next(item for item in after if item.kind == "draft")
    draft = decode_draft_document(
        read_prepared_writing_body(
            session=SimpleNamespace(corefs_session=restarted, corefs_keys=keys),
            item=draft_item,
        )
    )
    assert f"corefs://object/{media_id}" in draft.body


@pytest.mark.parametrize("fail_after_object", [1, 2])
def test_streaming_preparation_reconciles_every_durable_object_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_after_object: int,
) -> None:
    monkeypatch.setattr("anima_server.services.core.update_core_manifest", lambda _mutator: None)
    monkeypatch.setattr(
        "anima_server.services.core.get_manifest_path",
        lambda: tmp_path / "missing-manifest.json",
    )
    engine = create_engine(f"sqlite:///{(tmp_path / 'source.db').as_posix()}")
    Base.metadata.create_all(engine)
    root = tmp_path / "core"
    core_id = migration_opaque_id("test-core", f"restart-after-{fail_after_object}")
    keys = anima_core.corefs_derive_subkeys(anima_core.corefs_generate_root_key(), 1)
    native = anima_core.CorefsSession(str(root), core_id)
    draft = LegacyDiaryDraft(
        id="restart-draft",
        target_entry_id=None,
        body=(
            '<p><img src="data:image/png;base64,'
            + base64.b64encode(b"bounded-restart-image").decode()
            + '"></p>'
        ),
        content_type="text/html",
        updated_at="2026-08-02T00:00:00Z",
    )
    crashing = _PreparationFaultProxy(native, fail_after_object=fail_after_object)
    with Session(engine) as db, pytest.raises(
        DiaryMigrationError, match="failed safely"
    ):
        prepare_diary_validation_catalog(
            session=SimpleNamespace(
                user_id=51,
                corefs_session=crashing,
                corefs_keys=keys,
            ),
            db=db,
            staged_drafts=(draft,),
        )

    del crashing
    del native
    restarted = anima_core.CorefsSession(str(root), core_id)
    counting = _PreparationFaultProxy(restarted)
    resumed_source_ids: list[str] = []
    original_iter = writing_source.iter_writing_source_objects

    def capture_pending_inventory(**kwargs: object):  # type: ignore[no-untyped-def]
        inventory = kwargs["inventory"]
        assert isinstance(inventory, WritingSourceInventory)
        resumed_source_ids.extend(item.stable_id for item in inventory.objects)
        return original_iter(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        writing_source,
        "iter_writing_source_objects",
        capture_pending_inventory,
    )
    with Session(engine) as db:
        result = prepare_diary_validation_catalog(
            session=SimpleNamespace(
                user_id=51,
                corefs_session=counting,
                corefs_keys=keys,
            ),
            db=db,
            staged_drafts=(draft,),
        )

    assert result.published is True
    assert result.generation == 1
    assert counting.prepared_objects == 2 - fail_after_object
    assert len(resumed_source_ids) == 2 - fail_after_object
    assert restarted.validation_snapshot(keys)["generation"] == 1


@pytest.mark.parametrize("after_publication", [False, True])
def test_streaming_preparation_recovers_native_finalize_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    after_publication: bool,
) -> None:
    monkeypatch.setattr("anima_server.services.core.update_core_manifest", lambda _mutator: None)
    monkeypatch.setattr(
        "anima_server.services.core.get_manifest_path",
        lambda: tmp_path / "missing-manifest.json",
    )
    engine = create_engine(f"sqlite:///{(tmp_path / 'source.db').as_posix()}")
    Base.metadata.create_all(engine)
    root = tmp_path / "core"
    core_id = migration_opaque_id("test-core", f"finalize-{after_publication}")
    keys = anima_core.corefs_derive_subkeys(anima_core.corefs_generate_root_key(), 1)
    native = anima_core.CorefsSession(str(root), core_id)
    draft = LegacyDiaryDraft(
        id="finalize-draft",
        target_entry_id=None,
        body="<p>durable before completion</p>",
        content_type="text/html",
        updated_at="2026-08-02T00:00:00Z",
    )
    failing = _PreparationFaultProxy(
        native,
        fail_finalize_before=not after_publication,
        fail_finalize_after=after_publication,
    )
    with Session(engine) as db, pytest.raises(
        DiaryMigrationError, match="failed safely"
    ):
        prepare_diary_validation_catalog(
            session=SimpleNamespace(user_id=52, corefs_session=failing, corefs_keys=keys),
            db=db,
            staged_drafts=(draft,),
        )

    del failing
    del native
    restarted = anima_core.CorefsSession(str(root), core_id)
    with Session(engine) as db:
        recovered = prepare_diary_validation_catalog(
            session=SimpleNamespace(user_id=52, corefs_session=restarted, corefs_keys=keys),
            db=db,
            staged_drafts=(draft,),
        )

    assert restarted.validation_snapshot(keys) == {
        "generation": recovered.generation,
        "catalogHash": recovered.catalog_hash,
    }
    assert recovered.generation == 1
    assert recovered.published is (not after_publication)


def test_body_iterator_models_over_one_gibibyte_without_aggregate_ownership(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'source.db').as_posix()}")
    Base.metadata.create_all(engine)
    parent_id = migration_opaque_id("core-folder-role", "core.notes")
    logical_body_size = 256 * 1024 * 1024 + 1
    notes: list[LegacyNote] = []
    descriptors: list[WritingSourceObjectDescriptor] = []
    for index in range(4):
        note = LegacyNote(
            id=f"logical-large-{index}",
            title=f"Part {index}",
            body="small generated body",
            content_type="text/markdown",
            updated_at="2026-08-02T00:00:00Z",
            source_character_count=logical_body_size,
        )
        stable_id = migration_opaque_id("note", note.id)
        body = encode_note_document(
            stable_id=stable_id,
            title=note.title,
            content_type=note.content_type,
            body=note.body,
        )
        notes.append(note)
        descriptors.append(
            WritingSourceObjectDescriptor(
                stable_id=stable_id,
                parent_id=parent_id,
                name=f"{stable_id}.note.json",
                kind="note",
                content_type="application/vnd.anima.note+json;v=1",
                body_encoding="utf-8",
                body_length=len(body),
                content_sha256=hashlib.sha256(body).hexdigest(),
                source_fingerprint_sha256=hashlib.sha256(note.body.encode()).hexdigest(),
                created_at=note.updated_at,
                updated_at=note.updated_at,
                revision=1,
                source_character_count=logical_body_size,
                body_source="staged_note",
                source_key=note.id,
            )
        )
    inventory = WritingSourceInventory(
        source_generation=1,
        source_digest="0" * 64,
        expected_head=None,
        folders=(),
        objects=tuple(descriptors),
        source_counts={"folders": 0, "entries": 0, "attachments": 0, "drafts": 0, "notes": 4},
    )

    logical_aggregate = sum(item.source_character_count or 0 for item in descriptors)
    max_owned_body = 0
    yielded = 0
    with Session(engine) as db:
        produced = iter_writing_source_objects(
            session=SimpleNamespace(user_id=53),
            db=db,
            inventory=inventory,
            staged_notes=notes,
        )
        assert iter(produced) is produced
        for item in produced:
            yielded += 1
            max_owned_body = max(max_owned_body, len(item.body))
            del item

    assert logical_aggregate > 1024**3
    assert yielded == 4
    assert max_owned_body < 1024


def test_rebuild_rejects_dangling_and_foreign_draft_corefs_references() -> None:
    dangling_id = migration_opaque_id("missing", "attachment")
    draft = LegacyDiaryDraft(
        id="unsafe-reference",
        target_entry_id=None,
        body=f'<img src="corefs://object/{dangling_id}">',
        content_type="text/html",
        updated_at="2026-08-02T00:00:00Z",
    )
    with pytest.raises(ValueError, match="dangling"):
        build_inactive_diary_catalog(
            user_id=1,
            folders=(),
            entries=(),
            drafts=(draft,),
        )

    existing = build_inactive_diary_catalog(
        user_id=1,
        folders=(),
        entries=(),
        notes=(
            LegacyNote(
                id="foreign",
                title=None,
                body="# Not media",
                content_type="text/markdown",
                updated_at="2026-08-02T00:00:00Z",
            ),
        ),
    )
    note = next(item for item in existing.objects if item.kind == "note")
    foreign_draft = LegacyDiaryDraft(
        id="foreign-reference",
        target_entry_id=None,
        body=f'<img src="corefs://object/{note.stable_id}">',
        content_type="text/html",
        updated_at="2026-08-02T00:00:00Z",
    )
    with pytest.raises(ValueError, match="foreign"):
        build_inactive_diary_catalog(
            user_id=1,
            folders=(),
            entries=(),
            drafts=(foreign_draft,),
            preserved_objects=(note,),
        )


@pytest.mark.parametrize(
    "body",
    [
        '<img src="data:image/png;base64,not-base64!">',
        '<img src="data:image/png;base64,'
        + base64.b64encode(b"x" * (10 * 1024 * 1024 + 1)).decode()
        + '">',
    ],
    ids=("malformed", "oversized"),
)
def test_invalid_legacy_draft_inline_media_fails_before_native_head_change(
    tmp_path: Path, body: str
) -> None:
    session = anima_core.CorefsSession(
        str(tmp_path / "core"), migration_opaque_id("test-core", "draft-inline-failure")
    )
    keys = anima_core.corefs_derive_subkeys(anima_core.corefs_generate_root_key(), 1)
    baseline = build_inactive_diary_catalog(user_id=1, folders=(), entries=())
    first = publish_catalog_native(baseline, corefs_session=session, keys=keys)
    before = session.validation_snapshot(keys)
    with pytest.raises(CoreFormatError):
        build_inactive_diary_catalog(
            user_id=1,
            folders=(),
            entries=(),
            drafts=(
                LegacyDiaryDraft(
                    id="invalid",
                    target_entry_id=None,
                    body=body,
                    content_type="text/html",
                    updated_at="2026-08-02T00:00:00Z",
                ),
            ),
        )
    assert session.validation_snapshot(keys) == before
    assert before["generation"] == first["generation"]


def test_diary_codec_preserves_tiptap_markup_and_rejects_executable_html() -> None:
    entry_id = migration_opaque_id("test", "entry-7")
    folder_id = migration_opaque_id("test", "folder-3")
    cover_id = migration_opaque_id("test", "cover-1")
    file_id = migration_opaque_id("test", "file-1")
    document = encode_diary_document(
        stable_id=entry_id,
        entry_date="2026-08-02",
        title="Private",
        mood="calm",
        folder_id=folder_id,
        html=(
            '<h2>Title</h2><blockquote><p onclick="bad()"><strong>Private</strong> '
            "<em>note</em></p></blockquote><ul><li>memory</li></ul>"
            '<a href="javascript:bad()">link</a><script>bad()</script>'
        ),
        cover_uri=f"corefs://object/{cover_id}",
        attachment_uris=(f"corefs://object/{file_id}",),
    )

    decoded = decode_diary_document(document)

    assert decoded.format_version == 1
    assert decoded.stable_id == entry_id
    assert decoded.html.startswith("<h2>Title</h2>")
    assert "<blockquote><p><strong>Private</strong> <em>note</em></p></blockquote>" in decoded.html
    assert "<ul><li>memory</li></ul>" in decoded.html
    assert "onclick" not in decoded.html
    assert "javascript:" not in decoded.html
    assert "<script" not in decoded.html
    assert decoded.cover_uri == f"corefs://object/{cover_id}"
    assert decoded.attachment_uris == (f"corefs://object/{file_id}",)


def test_inline_media_is_validated_deduplicated_and_replaced() -> None:
    png = b"\x89PNG\r\n\x1a\nprivate"
    encoded = base64.b64encode(png).decode()
    seen: list[tuple[str, bytes, str]] = []

    def publish(mime_type: str, data: bytes, sha256: str) -> str:
        seen.append((mime_type, data, sha256))
        return f"corefs://object/{migration_opaque_id('inline', sha256)}"

    result = canonicalize_diary_html(
        f'<p><img src="data:image/png;base64,{encoded}"></p>'
        f'<img alt="duplicate" src="data:image/png;base64,{encoded}">',
        media_reference_factory=publish,
    )

    digest = hashlib.sha256(png).hexdigest()
    assert len(seen) == 1
    assert seen[0] == ("image/png", png, digest)
    object_id = migration_opaque_id("inline", digest)
    assert result.html.count(f"corefs://object/{object_id}") == 2
    assert result.media_uris == (f"corefs://object/{object_id}",)
    assert "data:" not in result.html


@pytest.mark.parametrize(
    "source",
    [
        '<img src="data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=">',
        '<img src="data:image/png;base64,not-base64!">',
    ],
)
def test_invalid_inline_media_fails_before_publication(source: str) -> None:
    published: list[str] = []

    with pytest.raises(CoreFormatError):
        canonicalize_diary_html(
            source,
            media_reference_factory=lambda _mime, _data, digest: published.append(digest) or digest,
        )

    assert published == []


def test_diary_codec_rejects_non_native_corefs_uri_ids() -> None:
    with pytest.raises(CoreFormatError, match="stable CoreFS URIs"):
        encode_diary_document(
            stable_id=migration_opaque_id("test", "entry"),
            entry_date="2026-08-02",
            title=None,
            mood=None,
            folder_id=None,
            html="<p>safe</p>",
            cover_uri="corefs://object/legacy-display-id",
        )


def test_plain_text_is_escaped_into_html_paragraphs() -> None:
    result = canonicalize_diary_html("first <private>\n\nsecond & final", legacy_plain_text=True)

    assert result.html == "<p>first &lt;private&gt;</p><p>second &amp; final</p>"


def test_literal_data_prose_survives_but_data_url_attributes_do_not() -> None:
    result = canonicalize_diary_html("<p>The data: prefix is ordinary prose.</p>")
    assert result.html == "<p>The data: prefix is ordinary prose.</p>"

    with pytest.raises(CoreFormatError):
        canonicalize_diary_html('<img src="data:text/plain;base64,QQ==">')


def test_server_matches_shared_versioned_sanitizer_goldens() -> None:
    contract = json.loads(
        Path("apps/server/src/anima_server/services/corefs/writing-sanitizer-v1.json").read_text(
            encoding="utf-8"
        )
    )
    for golden in contract["goldens"]:
        assert canonicalize_diary_html(golden["input"]).html == golden["output"]


def test_server_matches_shared_data_uri_canonicalization_policy() -> None:
    contract = json.loads(
        Path("apps/server/src/anima_server/services/corefs/writing-sanitizer-v1.json").read_text(
            encoding="utf-8"
        )
    )
    published: list[tuple[str, bytes, str]] = []

    def publish(mime_type: str, data: bytes, sha256: str) -> str:
        published.append((mime_type, data, sha256))
        return "corefs://object/00000000000000000000000000"

    for golden in contract["dataGoldens"]:
        if golden["canonicalAction"] == "extract":
            result = canonicalize_diary_html(golden["input"], media_reference_factory=publish)
            assert result.html == (
                '<img src="corefs://object/00000000000000000000000000" alt="memory">'
            )
            assert published[-1][0:2] == ("image/png", b"\x00\x00\x00")
        else:
            with pytest.raises(CoreFormatError):
                canonicalize_diary_html(golden["input"], media_reference_factory=publish)


def test_inactive_catalog_preserves_empty_folders_ids_hashes_and_roles() -> None:
    cover = LegacyDiaryAttachment(
        id=31,
        entry_id=9,
        kind="image",
        mime_type="image/png",
        data=b"cover",
        sha256=hashlib.sha256(b"cover").hexdigest(),
        filename="cover.png",
        caption=None,
    )
    attachment = LegacyDiaryAttachment(
        id=32,
        entry_id=9,
        kind="file",
        mime_type="application/pdf",
        data=b"private pdf",
        sha256=hashlib.sha256(b"private pdf").hexdigest(),
        filename="private.pdf",
        caption="scan",
    )
    catalog = build_inactive_diary_catalog(
        user_id=4,
        folders=(
            LegacyDiaryFolder(id=5, name="Empty", parent_id=None, order=0),
            LegacyDiaryFolder(id=6, name="Travel", parent_id=None, order=1),
        ),
        entries=(
            LegacyDiaryEntry(
                id=9,
                entry_date="2026-08-02",
                title="Arrival",
                body="landed",
                body_is_html=False,
                mood=None,
                folder_id=6,
                cover_attachment_id=31,
                attachments=(cover, attachment),
            ),
        ),
    )

    journal = catalog.folder_for_role("core.journal")
    notes = catalog.folder_for_role("core.notes")
    assert (journal.owner, journal.agent_access) == ("user", "write")
    assert (notes.owner, notes.agent_access) == ("user", "write")
    empty_id = migration_opaque_id("diary-folder", "5")
    travel_id = migration_opaque_id("diary-folder", "6")
    entry_id = migration_opaque_id("diary-entry", "9")
    cover_id = migration_opaque_id("diary-attachment", "31")
    attachment_id = migration_opaque_id("diary-attachment", "32")
    assert catalog.folder(empty_id).name == "Empty"
    assert catalog.folder(empty_id).children == ()
    entry = catalog.object(entry_id)
    assert entry.parent_id == travel_id
    assert entry.content_hash == hashlib.sha256(entry.content).hexdigest()
    assert catalog.object(cover_id).source_hash == cover.sha256
    assert catalog.object(attachment_id).source_hash == attachment.sha256
    assert f"corefs://object/{cover_id}".encode() in entry.content
    assert f"corefs://object/{attachment_id}".encode() in entry.content
    decoded = decode_diary_document(entry.content)
    assert decoded.legacy_id == 9
    assert decoded.legacy_folder_id == 6
    assert decoded.source == "user"
    assert decoded.attachment_metadata == (
        {
            "caption": None,
            "createdAt": None,
            "filename": "cover.png",
            "kind": "image",
            "legacyId": 31,
            "mimeType": "image/png",
            "sha256": cover.sha256,
            "stableId": cover_id,
        },
        {
            "caption": "scan",
            "createdAt": None,
            "filename": "private.pdf",
            "kind": "file",
            "legacyId": 32,
            "mimeType": "application/pdf",
            "sha256": attachment.sha256,
            "stableId": attachment_id,
        },
    )
    assert [catalog.folder(empty_id).order, catalog.folder(travel_id).order] == [0, 1]
    assert catalog.folder(travel_id).policy == "inherit"


def test_inactive_catalog_is_idempotent_and_publishes_atomically() -> None:
    kwargs = {
        "user_id": 7,
        "folders": (LegacyDiaryFolder(id=1, name="Only", parent_id=None, order=0),),
        "entries": (),
    }
    first = build_inactive_diary_catalog(**kwargs)
    second = build_inactive_diary_catalog(**kwargs)
    published: list[object] = []

    assert first.catalog_hash == second.catalog_hash
    first.publish(lambda snapshot: published.append(snapshot))
    assert published == [first]

    def fail(_snapshot: object) -> None:
        raise RuntimeError("publication failed")

    with pytest.raises(RuntimeError, match="publication failed"):
        second.publish(fail)
    assert published == [first]


def test_legacy_names_are_portable_unique_and_preserve_exact_display_metadata() -> None:
    first_attachment = LegacyDiaryAttachment(
        id=101,
        entry_id=10,
        kind="file",
        mime_type="text/plain",
        data=b"first",
        sha256=hashlib.sha256(b"first").hexdigest(),
        filename="same/name.txt",
        caption=None,
    )
    second_attachment = LegacyDiaryAttachment(
        id=102,
        entry_id=11,
        kind="file",
        mime_type="text/plain",
        data=b"second",
        sha256=hashlib.sha256(b"second").hexdigest(),
        filename="same/name.txt",
        caption=None,
    )
    decomposed_name = "Cafe\u0301"
    catalog = build_inactive_diary_catalog(
        user_id=7,
        folders=(
            LegacyDiaryFolder(id=1, name="Duplicate", parent_id=None, order=0),
            LegacyDiaryFolder(id=2, name="Duplicate", parent_id=None, order=1),
            LegacyDiaryFolder(id=3, name="path/to\\folder", parent_id=None, order=2),
            LegacyDiaryFolder(id=4, name=decomposed_name, parent_id=None, order=3),
            LegacyDiaryFolder(id=5, name="..", parent_id=None, order=4),
        ),
        entries=(
            LegacyDiaryEntry(
                id=10,
                entry_date="2026-08-02",
                title=None,
                body="",
                body_is_html=True,
                mood=None,
                folder_id=None,
                cover_attachment_id=None,
                attachments=(first_attachment,),
            ),
            LegacyDiaryEntry(
                id=11,
                entry_date="2026-08-03",
                title=None,
                body="",
                body_is_html=True,
                mood=None,
                folder_id=None,
                cover_attachment_id=None,
                attachments=(second_attachment,),
            ),
        ),
    )

    legacy_folders = [
        catalog.folder(migration_opaque_id("diary-folder", str(folder_id)))
        for folder_id in range(1, 6)
    ]
    sibling_names = [folder.name for folder in legacy_folders]
    assert len(sibling_names) == len(set(sibling_names))
    assert all(name not in {".", ".."} for name in sibling_names)
    assert all("/" not in name and "\\" not in name for name in sibling_names)
    assert all(unicodedata.normalize("NFC", name) == name for name in sibling_names)
    assert legacy_folders[3].name == "Caf\u00e9"
    assert [folder.metadata["displayName"] for folder in legacy_folders] == [
        "Duplicate",
        "Duplicate",
        "path/to\\folder",
        decomposed_name,
        "..",
    ]
    assert [folder.metadata["originalName"] for folder in legacy_folders] == [
        "Duplicate",
        "Duplicate",
        "path/to\\folder",
        decomposed_name,
        "..",
    ]

    attachment_ids = [
        migration_opaque_id("diary-attachment", "101"),
        migration_opaque_id("diary-attachment", "102"),
    ]
    attachments = [catalog.object(stable_id) for stable_id in attachment_ids]
    assert len({item.name for item in attachments}) == 2
    assert all("/" not in item.name and "\\" not in item.name for item in attachments)
    assert [item.metadata["displayName"] for item in attachments] == [
        "same/name.txt",
        "same/name.txt",
    ]
    assert [item.metadata["originalName"] for item in attachments] == [
        "same/name.txt",
        "same/name.txt",
    ]

    rerun = build_inactive_diary_catalog(
        user_id=7,
        folders=(
            LegacyDiaryFolder(id=1, name="Duplicate", parent_id=None, order=0),
            LegacyDiaryFolder(id=2, name="Duplicate", parent_id=None, order=1),
            LegacyDiaryFolder(id=3, name="path/to\\folder", parent_id=None, order=2),
            LegacyDiaryFolder(id=4, name=decomposed_name, parent_id=None, order=3),
            LegacyDiaryFolder(id=5, name="..", parent_id=None, order=4),
        ),
        entries=(
            LegacyDiaryEntry(
                id=10,
                entry_date="2026-08-02",
                title=None,
                body="",
                body_is_html=True,
                mood=None,
                folder_id=None,
                cover_attachment_id=None,
                attachments=(first_attachment,),
            ),
            LegacyDiaryEntry(
                id=11,
                entry_date="2026-08-03",
                title=None,
                body="",
                body_is_html=True,
                mood=None,
                folder_id=None,
                cover_attachment_id=None,
                attachments=(second_attachment,),
            ),
        ),
    )
    assert rerun.catalog_hash == catalog.catalog_hash
    assert [(item.stable_id, item.name) for item in rerun.folders] == [
        (item.stable_id, item.name) for item in catalog.folders
    ]
    assert [(item.stable_id, item.name) for item in rerun.objects] == [
        (item.stable_id, item.name) for item in catalog.objects
    ]


def test_mapped_legacy_names_publish_read_back_and_rerun_natively(tmp_path: Path) -> None:
    data = b"portable"
    attachment = LegacyDiaryAttachment(
        id=201,
        entry_id=20,
        kind="file",
        mime_type="application/octet-stream",
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
        filename="../portable.bin",
        caption=None,
        created_at="2026-08-02T00:00:00Z",
    )
    catalog = build_inactive_diary_catalog(
        user_id=20,
        folders=(LegacyDiaryFolder(id=20, name="bad/name", parent_id=None, order=0),),
        entries=(
            LegacyDiaryEntry(
                id=20,
                entry_date="2026-08-02",
                title=None,
                body="",
                body_is_html=True,
                mood=None,
                folder_id=20,
                cover_attachment_id=None,
                attachments=(attachment,),
                created_at="2026-08-02T00:00:00Z",
                updated_at="2026-08-02T00:00:00Z",
            ),
        ),
    )
    session = anima_core.CorefsSession(
        str(tmp_path / "core"), migration_opaque_id("test-core", "portable-writing-names")
    )
    keys = anima_core.corefs_derive_subkeys(anima_core.corefs_generate_root_key(), 1)

    first = publish_catalog_native(catalog, corefs_session=session, keys=keys)
    prepared = read_prepared_writing_objects(
        session=SimpleNamespace(corefs_session=session, corefs_keys=keys)
    )
    attachment_id = migration_opaque_id("diary-attachment", "201")
    native_attachment = next(item for item in prepared if item.stable_id == attachment_id)
    assert (
        read_prepared_writing_body(
            session=SimpleNamespace(corefs_session=session, corefs_keys=keys),
            item=native_attachment,
        )
        == data
    )
    assert "/" not in catalog.object(attachment_id).name

    revisions = {item.stable_id: item.revision for item in prepared}
    rerun = catalog.with_expected_revisions(revisions)
    second = publish_catalog_native(
        rerun,
        corefs_session=session,
        keys=keys,
        expected_head=(int(first["generation"]), str(first["catalogHash"])),
    )
    assert second["published"] is False
    assert session.validation_snapshot(keys) == {
        "generation": int(first["generation"]),
        "catalogHash": str(first["catalogHash"]),
    }
    assert catalog.object(attachment_id).stable_id == attachment_id


def test_legacy_folder_policy_is_carried_into_native_descendant_policy() -> None:
    catalog = build_inactive_diary_catalog(
        user_id=7,
        folders=(
            LegacyDiaryFolder(id=1, name="Private", parent_id=None, order=0, policy="deny"),
            LegacyDiaryFolder(id=2, name="Child", parent_id=1, order=0, policy="inherit"),
            LegacyDiaryFolder(id=3, name="Read only", parent_id=None, order=1, policy="read"),
            LegacyDiaryFolder(id=4, name="Unknown", parent_id=None, order=2, policy="surprise"),
        ),
        entries=(),
    )

    assert catalog.folder(migration_opaque_id("diary-folder", "1")).policy == "deny"
    assert catalog.folder(migration_opaque_id("diary-folder", "2")).policy == "inherit"
    # The validation converter cannot represent lowered write access. Fail closed.
    assert catalog.folder(migration_opaque_id("diary-folder", "3")).policy == "deny"
    assert catalog.folder(migration_opaque_id("diary-folder", "4")).policy == "deny"


def test_server_production_path_has_no_aggregate_validation_transport() -> None:
    source = Path("apps/server/src/anima_server/services/corefs/diary_migration.py").read_text(
        encoding="utf-8"
    )
    writing_source = Path(
        "apps/server/src/anima_server/services/corefs/writing_source.py"
    ).read_text(encoding="utf-8")
    ffi = Path("packages/anima-core/src/ffi.rs").read_text(encoding="utf-8")

    assert "validation_batch_parts_v1" not in source
    assert "validation_batch_parts_v1" not in writing_source
    assert "validation_batch_parts_v1" not in ffi
    assert "CORE_FS_VALIDATION_BODY_AGGREGATE_LIMIT" not in ffi
    assert "[item.content for item" not in source


def test_native_transport_round_trips_100_mib_attachment_and_rejects_oversize(
    tmp_path: Path,
) -> None:
    session = anima_core.CorefsSession(
        str(tmp_path / "core"), migration_opaque_id("test-core", "large-writing-transport")
    )
    keys = anima_core.corefs_derive_subkeys(anima_core.corefs_generate_root_key(), 1)
    limit = 100 * 1024 * 1024
    attachment_bytes = b"a" * limit
    attachment = LegacyDiaryAttachment(
        id=700,
        entry_id=70,
        kind="file",
        mime_type="application/octet-stream",
        data=attachment_bytes,
        sha256=hashlib.sha256(attachment_bytes).hexdigest(),
        filename="valid-100-mib.bin",
        caption=None,
        created_at="2026-08-02T00:00:00Z",
    )
    catalog = build_inactive_diary_catalog(
        user_id=70,
        folders=(),
        entries=(
            LegacyDiaryEntry(
                id=70,
                entry_date="2026-08-02",
                title="large",
                body="",
                body_is_html=True,
                mood=None,
                folder_id=None,
                cover_attachment_id=None,
                attachments=(attachment,),
                created_at="2026-08-02T00:00:00Z",
                updated_at="2026-08-02T00:00:00Z",
            ),
        ),
    )

    first = publish_catalog_native(catalog, corefs_session=session, keys=keys)
    prepared = read_prepared_writing_objects(
        session=SimpleNamespace(corefs_session=session, corefs_keys=keys)
    )
    attachment_id = migration_opaque_id("diary-attachment", "700")
    native_attachment = next(item for item in prepared if item.stable_id == attachment_id)
    assert (
        read_prepared_writing_body(
            session=SimpleNamespace(corefs_session=session, corefs_keys=keys),
            item=native_attachment,
        )
        == attachment_bytes
    )

    head_before = session.validation_snapshot(keys)
    oversized_bytes = b"b" * (limit + 1)
    oversized_attachment = LegacyDiaryAttachment(
        id=701,
        entry_id=71,
        kind="file",
        mime_type="application/octet-stream",
        data=oversized_bytes,
        sha256=hashlib.sha256(oversized_bytes).hexdigest(),
        filename="oversized.bin",
        caption=None,
        created_at="2026-08-02T00:00:00Z",
    )
    oversized = build_inactive_diary_catalog(
        user_id=70,
        folders=(),
        entries=(
            LegacyDiaryEntry(
                id=71,
                entry_date="2026-08-02",
                title="oversized",
                body="",
                body_is_html=True,
                mood=None,
                folder_id=None,
                cover_attachment_id=None,
                attachments=(oversized_attachment,),
                created_at="2026-08-02T00:00:00Z",
                updated_at="2026-08-02T00:00:00Z",
            ),
        ),
    )
    with pytest.raises(ValueError, match="kind-specific converter limits"):
        publish_catalog_native(
            oversized,
            corefs_session=session,
            keys=keys,
            expected_head=(int(first["generation"]), str(first["catalogHash"])),
        )
    assert session.validation_snapshot(keys) == head_before


def test_inactive_catalog_includes_native_drafts_and_generic_notes() -> None:
    catalog = build_inactive_diary_catalog(
        user_id=9,
        folders=(),
        entries=(),
        drafts=(
            LegacyDiaryDraft(
                id="browser-draft-1",
                target_entry_id=None,
                body="<p>unsaved</p>",
                content_type="text/html",
                updated_at="2026-08-02T00:00:00Z",
            ),
        ),
        notes=(
            LegacyNote(
                id="note-1",
                title="Private",
                body="# Native note",
                content_type="text/markdown",
                updated_at="2026-08-02T00:00:00Z",
            ),
        ),
    )

    draft = catalog.object(migration_opaque_id("diary-draft", "browser-draft-1"))
    note = catalog.object(migration_opaque_id("note", "note-1"))
    assert (draft.kind, draft.content_type, draft.body_encoding) == (
        "draft",
        "application/vnd.anima.draft+json;version=1",
        "utf-8",
    )
    assert (note.kind, note.content_type, note.body_encoding) == (
        "note",
        "application/vnd.anima.note+json;version=1",
        "utf-8",
    )
    assert note.parent_id == catalog.folder_for_role("core.notes").stable_id


def test_attachment_only_and_cover_only_entries_are_not_dropped() -> None:
    cover = LegacyDiaryAttachment(
        id=1,
        entry_id=10,
        kind="image",
        mime_type="image/png",
        data=b"cover",
        sha256=hashlib.sha256(b"cover").hexdigest(),
        filename="cover.png",
        caption="cover caption",
    )
    file = LegacyDiaryAttachment(
        id=2,
        entry_id=11,
        kind="file",
        mime_type="application/pdf",
        data=b"pdf",
        sha256=hashlib.sha256(b"pdf").hexdigest(),
        filename="only.pdf",
        caption=None,
    )
    catalog = build_inactive_diary_catalog(
        user_id=1,
        folders=(),
        entries=(
            LegacyDiaryEntry(
                id=10,
                entry_date="2026-08-02",
                title=None,
                body="",
                body_is_html=True,
                mood=None,
                folder_id=None,
                cover_attachment_id=1,
                attachments=(cover,),
            ),
            LegacyDiaryEntry(
                id=11,
                entry_date="2026-08-03",
                title=None,
                body="",
                body_is_html=True,
                mood=None,
                folder_id=None,
                cover_attachment_id=None,
                attachments=(file,),
            ),
        ),
    )
    cover_entry = decode_diary_document(
        catalog.object(migration_opaque_id("diary-entry", "10")).content
    )
    attachment_entry = decode_diary_document(
        catalog.object(migration_opaque_id("diary-entry", "11")).content
    )
    assert cover_entry.html == ""
    assert cover_entry.cover_uri is not None
    assert attachment_entry.html == ""
    assert attachment_entry.attachment_uris == (
        f"corefs://object/{migration_opaque_id('diary-attachment', '2')}",
    )


def test_oversized_inline_image_fails_before_catalog_publication() -> None:
    encoded = base64.b64encode(b"oversized").decode()
    published: list[object] = []
    with pytest.raises(CoreFormatError):
        canonicalize_diary_html(
            f'<img src="data:image/png;base64,{encoded}">',
            max_inline_media_bytes=1,
            media_reference_factory=lambda *_: published.append(object()) or "unused",
        )
    assert published == []


def test_python_rerun_hydrates_current_native_revisions() -> None:
    catalog = build_inactive_diary_catalog(
        user_id=1,
        folders=(),
        entries=(),
        drafts=(
            LegacyDiaryDraft(
                id="draft",
                target_entry_id=None,
                body="<p>safe</p>",
                content_type="text/html",
                updated_at="2026-08-02T00:00:00Z",
            ),
        ),
    )
    draft_id = migration_opaque_id("diary-draft", "draft")
    rerun = catalog.with_expected_revisions({draft_id: 3})
    assert rerun.object(draft_id).expected_revision == 3


def test_rerun_preserves_native_role_placement_and_object_envelope_fields() -> None:
    root_id = migration_opaque_id("existing", "root")
    journal_id = migration_opaque_id("existing", "journal")
    notes_id = migration_opaque_id("existing", "notes")
    preserved = (
        InactiveFolder(
            stable_id=journal_id,
            parent_id=root_id,
            name="My Journal",
            order=0,
            role="core.journal",
            owner="user",
            agent_access="write",
            policy="user-write",
        ),
        InactiveFolder(
            stable_id=notes_id,
            parent_id=journal_id,
            name="Reference Notes",
            order=1,
            role="core.notes",
            owner="user",
            agent_access="write",
            policy="user-write",
        ),
    )
    catalog = build_inactive_diary_catalog(
        user_id=1,
        folders=(),
        entries=(),
        drafts=(
            LegacyDiaryDraft(
                id="native-draft",
                target_entry_id=None,
                body="<p>same</p>",
                content_type="text/html",
                updated_at="2026-08-01T02:00:00Z",
                stable_id=migration_opaque_id("existing", "draft"),
                created_at="2026-08-01T01:00:00Z",
                native_metadata={"origin": "authenticated"},
            ),
        ),
        notes=(
            LegacyNote(
                id="native-note",
                title="Same",
                body="# same",
                content_type="text/markdown",
                updated_at="2026-08-01T04:00:00Z",
                stable_id=migration_opaque_id("existing", "note"),
                created_at="2026-08-01T03:00:00Z",
                native_metadata={"origin": "authenticated"},
            ),
        ),
        preserved_folders=preserved,
    )

    journal = catalog.folder_for_role("core.journal")
    notes = catalog.folder_for_role("core.notes")
    assert (journal.stable_id, journal.name, journal.parent_id) == (
        journal_id,
        "My Journal",
        root_id,
    )
    assert (notes.stable_id, notes.name, notes.parent_id) == (
        notes_id,
        "Reference Notes",
        journal_id,
    )
    draft = catalog.object(migration_opaque_id("existing", "draft"))
    note = catalog.object(migration_opaque_id("existing", "note"))
    assert (draft.created_at, draft.updated_at, draft.metadata) == (
        "2026-08-01T01:00:00Z",
        "2026-08-01T02:00:00Z",
        {"origin": "authenticated"},
    )
    assert (note.created_at, note.updated_at, note.metadata) == (
        "2026-08-01T03:00:00Z",
        "2026-08-01T04:00:00Z",
        {"origin": "authenticated"},
    )
