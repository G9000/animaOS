from __future__ import annotations

import base64
import hashlib
import json
import unicodedata
from pathlib import Path
from types import SimpleNamespace

import anima_core
import pytest
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
    read_prepared_writing_objects,
)
from anima_server.services.corefs.formats import (
    MAX_WRITING_BODY_CHARACTERS,
    CoreFormatError,
    canonicalize_diary_html,
    decode_diary_document,
    decode_draft_document,
    encode_diary_document,
)
from pydantic import ValidationError


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
    native = anima_core.CorefsSession(str(root), "poison-writing-head")
    keys = anima_core.corefs_derive_subkeys(anima_core.corefs_generate_root_key(), 1)
    build_inactive_diary_catalog(user_id=7, folders=(), entries=()).publish_native(
        corefs_session=native, keys=keys
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
    native = anima_core.CorefsSession(str(tmp_path / "core"), "public-writing-boundary")
    keys = anima_core.corefs_derive_subkeys(anima_core.corefs_generate_root_key(), 1)
    first = catalog.publish_native(corefs_session=native, keys=keys)
    draft_id = migration_opaque_id("diary-draft", payload.draftId)
    prepared = read_prepared_writing_objects(
        session=SimpleNamespace(corefs_session=native, corefs_keys=keys)
    )
    decoded = decode_draft_document(
        next(item.content for item in prepared if item.stable_id == draft_id)
    )
    assert decoded.body == html
    before = native.validation_snapshot(keys)
    assert before["generation"] == first["generation"]
    with pytest.raises(ValidationError):
        DiaryDraftImportRequest(
            userId=1,
            draftId="oversized",
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
    class EmptyDb:
        def scalars(self, _statement: object) -> EmptyDb:
            return self

        def all(self) -> list[object]:
            return []

    monkeypatch.setattr("anima_server.services.core.update_core_manifest", lambda _mutator: None)
    root = tmp_path / "core"
    keys = anima_core.corefs_derive_subkeys(anima_core.corefs_generate_root_key(), 1)
    first_session = anima_core.CorefsSession(str(root), "draft-attachment-rebuild")
    image = b"restart-safe-image"
    body = '<p><img src="data:image/png;base64,' + base64.b64encode(image).decode() + '"></p>'
    first = prepare_diary_validation_catalog(
        session=SimpleNamespace(
            user_id=41,
            corefs_session=first_session,
            corefs_keys=keys,
        ),
        db=EmptyDb(),
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
    restarted = anima_core.CorefsSession(str(root), "draft-attachment-rebuild")
    second = prepare_diary_validation_catalog(
        session=SimpleNamespace(
            user_id=41,
            corefs_session=restarted,
            corefs_keys=keys,
        ),
        db=EmptyDb(),
    )
    after = read_prepared_writing_objects(
        session=SimpleNamespace(corefs_session=restarted, corefs_keys=keys)
    )
    attachment_after = next(item for item in after if item.stable_id == media_id)

    assert second.published is False
    assert second.generation == first.generation
    assert attachment_after == attachment_before
    draft = decode_draft_document(next(item.content for item in after if item.kind == "draft"))
    assert f"corefs://object/{media_id}" in draft.body


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
    session = anima_core.CorefsSession(str(tmp_path / "core"), "draft-inline-failure")
    keys = anima_core.corefs_derive_subkeys(anima_core.corefs_generate_root_key(), 1)
    baseline = build_inactive_diary_catalog(user_id=1, folders=(), entries=())
    first = baseline.publish_native(corefs_session=session, keys=keys)
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
    session = anima_core.CorefsSession(str(tmp_path / "core"), "portable-writing-names")
    keys = anima_core.corefs_derive_subkeys(anima_core.corefs_generate_root_key(), 1)

    first = catalog.publish_native(corefs_session=session, keys=keys)
    prepared = read_prepared_writing_objects(
        session=SimpleNamespace(corefs_session=session, corefs_keys=keys)
    )
    attachment_id = migration_opaque_id("diary-attachment", "201")
    native_attachment = next(item for item in prepared if item.stable_id == attachment_id)
    assert native_attachment.content == data
    assert "/" not in catalog.object(attachment_id).name

    revisions = {item.stable_id: item.revision for item in prepared}
    rerun = catalog.with_expected_revisions(revisions)
    second = rerun.publish_native(
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


def test_native_publication_wrapper_sends_bounded_metadata_and_separate_binary_parts() -> None:
    catalog = build_inactive_diary_catalog(
        user_id=8,
        folders=(),
        entries=(),
        drafts=(
            LegacyDiaryDraft(
                id="transport-draft",
                target_entry_id=None,
                body="<p>separate bytes</p>",
                content_type="text/html",
                updated_at="2026-08-02T00:00:00Z",
            ),
        ),
    )

    class Session:
        def __init__(self) -> None:
            self.calls: list[tuple[object, str, list[bytes]]] = []

        def validation_batch_parts_v1(
            self, keys: object, payload: str, parts: list[bytes]
        ) -> dict[str, object]:
            self.calls.append((keys, payload, parts))
            return {"generation": 1, "catalogHash": "a" * 64, "published": True}

    session = Session()
    keys = object()
    result = catalog.publish_native(corefs_session=session, keys=keys)

    assert result["published"] is True
    assert len(session.calls) == 1
    assert session.calls[0][0] is keys
    payload = json.loads(session.calls[0][1])
    assert session.calls[0][2] == [item.content for item in catalog.objects]
    assert all("contentBase64" not in item for item in payload["objects"])
    assert [item.get("sourceCharacterCount") for item in payload["objects"]] == [
        len("<p>separate bytes</p>")
    ]
    assert [item["contentIndex"] for item in payload["objects"]] == list(
        range(len(catalog.objects))
    )
    assert payload["initialize"] is True
    assert {folder["role"] for folder in payload["folders"]} >= {
        "core.journal",
        "core.notes",
    }
    assert all(len(folder["stableId"]) == 26 for folder in payload["folders"])


def test_native_transport_round_trips_100_mib_attachment_and_rejects_oversize(
    tmp_path: Path,
) -> None:
    session = anima_core.CorefsSession(str(tmp_path / "core"), "large-writing-transport")
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

    first = catalog.publish_native(corefs_session=session, keys=keys)
    prepared = read_prepared_writing_objects(
        session=SimpleNamespace(corefs_session=session, corefs_keys=keys)
    )
    attachment_id = migration_opaque_id("diary-attachment", "700")
    native_attachment = next(item for item in prepared if item.stable_id == attachment_id)
    assert native_attachment.content == attachment_bytes

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
        oversized.publish_native(
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
