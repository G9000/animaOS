from __future__ import annotations

import pytest
from anima_server.services.corefs.formats import (
    CoreFormatError,
    decode_note_document,
    encode_note_document,
)


@pytest.mark.parametrize("content_type", ["text/markdown", "text/html"])
def test_note_codec_round_trips_supported_formats(content_type: str) -> None:
    body = "# Private\n\nA note." if content_type == "text/markdown" else "<h1>Private</h1><p>A note.</p>"
    encoded = encode_note_document(
        stable_id="note-1",
        title="Private",
        content_type=content_type,
        body=body,
    )

    decoded = decode_note_document(encoded)

    assert decoded.stable_id == "note-1"
    assert decoded.content_type == content_type
    assert decoded.body == body


def test_html_notes_share_the_diary_sanitizer_contract() -> None:
    decoded = decode_note_document(
        encode_note_document(
            stable_id="note-2",
            title=None,
            content_type="text/html",
            body='<p onclick="bad()">safe</p><script>bad()</script>',
        )
    )

    assert decoded.body == "<p>safe</p>"


def test_note_codec_rejects_unsupported_content_type() -> None:
    with pytest.raises(CoreFormatError):
        encode_note_document(
            stable_id="note-3",
            title=None,
            content_type="text/plain",
            body="not canonical",
        )
