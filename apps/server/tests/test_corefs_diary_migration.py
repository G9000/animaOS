from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest
from anima_server.services.corefs.formats import (
    CoreFormatError,
    canonicalize_diary_html,
    decode_diary_document,
    encode_diary_document,
)
from corefs_catalog_test_support import migration_opaque_id


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
