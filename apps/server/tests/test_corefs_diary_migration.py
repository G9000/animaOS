from __future__ import annotations

import base64
import hashlib

import pytest
from anima_server.services.corefs.diary_migration import (
    LegacyDiaryAttachment,
    LegacyDiaryEntry,
    LegacyDiaryFolder,
    build_inactive_diary_catalog,
)
from anima_server.services.corefs.formats import (
    CoreFormatError,
    canonicalize_diary_html,
    decode_diary_document,
    encode_diary_document,
)


def test_diary_codec_preserves_tiptap_markup_and_rejects_executable_html() -> None:
    document = encode_diary_document(
        stable_id="entry-7",
        entry_date="2026-08-02",
        title="Private",
        mood="calm",
        folder_id="folder-3",
        html=(
            '<h2>Title</h2><blockquote><p onclick="bad()"><strong>Private</strong> '
            '<em>note</em></p></blockquote><ul><li>memory</li></ul>'
            '<a href="javascript:bad()">link</a><script>bad()</script>'
        ),
        cover_uri="corefs://object/cover-1",
        attachment_uris=("corefs://object/file-1",),
    )

    decoded = decode_diary_document(document)

    assert decoded.format_version == 1
    assert decoded.stable_id == "entry-7"
    assert decoded.html.startswith("<h2>Title</h2>")
    assert "<blockquote><p><strong>Private</strong> <em>note</em></p></blockquote>" in decoded.html
    assert "<ul><li>memory</li></ul>" in decoded.html
    assert "onclick" not in decoded.html
    assert "javascript:" not in decoded.html
    assert "<script" not in decoded.html
    assert decoded.cover_uri == "corefs://object/cover-1"
    assert decoded.attachment_uris == ("corefs://object/file-1",)


def test_inline_media_is_validated_deduplicated_and_replaced() -> None:
    png = b"\x89PNG\r\n\x1a\nprivate"
    encoded = base64.b64encode(png).decode()
    seen: list[tuple[str, bytes, str]] = []

    def publish(mime_type: str, data: bytes, sha256: str) -> str:
        seen.append((mime_type, data, sha256))
        return f"corefs://object/{sha256}"

    result = canonicalize_diary_html(
        f'<p><img src="data:image/png;base64,{encoded}"></p>'
        f'<img alt="duplicate" src="data:image/png;base64,{encoded}">',
        media_reference_factory=publish,
    )

    digest = hashlib.sha256(png).hexdigest()
    assert len(seen) == 1
    assert seen[0] == ("image/png", png, digest)
    assert result.html.count(f"corefs://object/{digest}") == 2
    assert result.media_uris == (f"corefs://object/{digest}",)
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


def test_plain_text_is_escaped_into_html_paragraphs() -> None:
    result = canonicalize_diary_html("first <private>\n\nsecond & final", legacy_plain_text=True)

    assert result.html == "<p>first &lt;private&gt;</p><p>second &amp; final</p>"


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
    assert catalog.folder("diary-folder-5").name == "Empty"
    assert catalog.folder("diary-folder-5").children == ()
    entry = catalog.object("diary-entry-9")
    assert entry.parent_id == "diary-folder-6"
    assert entry.content_hash == hashlib.sha256(entry.content).hexdigest()
    assert catalog.object("diary-attachment-31").source_hash == cover.sha256
    assert catalog.object("diary-attachment-32").source_hash == attachment.sha256
    assert b"corefs://object/diary-attachment-31" in entry.content
    assert b"corefs://object/diary-attachment-32" in entry.content


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
