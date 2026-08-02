from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest
from anima_server.services.corefs.diary_migration import (
    InactiveFolder,
    LegacyDiaryAttachment,
    LegacyDiaryDraft,
    LegacyDiaryEntry,
    LegacyDiaryFolder,
    LegacyNote,
    build_inactive_diary_catalog,
    migration_opaque_id,
)
from anima_server.services.corefs.formats import (
    CoreFormatError,
    canonicalize_diary_html,
    decode_diary_document,
    encode_diary_document,
)


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

def test_native_publication_wrapper_emits_one_complete_strict_batch() -> None:
    catalog = build_inactive_diary_catalog(
        user_id=8,
        folders=(),
        entries=(),
    )

    class Session:
        def __init__(self) -> None:
            self.calls: list[tuple[object, str]] = []

        def validation_batch_v1(self, keys: object, payload: str) -> dict[str, object]:
            self.calls.append((keys, payload))
            return {"generation": 1, "catalogHash": "a" * 64, "published": True}

    session = Session()
    keys = object()
    result = catalog.publish_native(corefs_session=session, keys=keys)

    assert result["published"] is True
    assert len(session.calls) == 1
    assert session.calls[0][0] is keys
    payload = json.loads(session.calls[0][1])
    assert payload["initialize"] is True
    assert {folder["role"] for folder in payload["folders"]} >= {
        "core.journal",
        "core.notes",
    }
    assert all(len(folder["stableId"]) == 26 for folder in payload["folders"])


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
