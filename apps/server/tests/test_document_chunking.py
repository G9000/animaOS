from __future__ import annotations

from inspect import signature

import pytest
from anima_server.services.documents.chunking import chunk_pages
from anima_server.services.documents.pdf_text import PageText


def test_chunk_pages_default_overlap_is_enabled() -> None:
    parameters = signature(chunk_pages).parameters

    assert parameters["overlap_chars"].default == 200


def test_chunk_pages_rejects_negative_overlap() -> None:
    with pytest.raises(ValueError, match="overlap_chars"):
        chunk_pages(
            [PageText(page_number=1, text="First paragraph.\n\nSecond paragraph.")],
            target_chars=20,
            overlap_chars=-1,
        )


def test_chunk_pages_carries_overlap_tail_into_next_chunk() -> None:
    chunks = chunk_pages(
        [
            PageText(
                page_number=1,
                text="First paragraph body here.\n\nSecond paragraph body here.",
            ),
        ],
        target_chars=40,
        overlap_chars=10,
    )

    assert len(chunks) == 2
    assert chunks[0].content_text == "First paragraph body here."
    assert chunks[1].content_text == "here.\n\nSecond paragraph body here."
    assert chunks[1].page_start == 1


def test_chunk_pages_overlap_does_not_emit_carried_text_only_chunks() -> None:
    chunks = chunk_pages(
        [PageText(page_number=1, text="Alpha paragraph text.\n\nBeta paragraph text.")],
        target_chars=25,
        overlap_chars=12,
    )

    contents = [chunk.content_text for chunk in chunks]
    assert contents[0] == "Alpha paragraph text."
    # The final chunk contains real content, never just the carried overlap tail.
    assert all("paragraph" in content for content in contents)
    assert len(chunks) == 2


def test_chunk_pages_overlap_spans_page_boundaries() -> None:
    chunks = chunk_pages(
        [
            PageText(page_number=1, text="Page one closing sentence."),
            PageText(page_number=2, text="Page two opening sentence."),
        ],
        target_chars=30,
        overlap_chars=12,
    )

    assert len(chunks) == 2
    assert chunks[0].page_start == 1
    assert chunks[1].content_text.startswith("sentence.")
    assert "Page two opening sentence." in chunks[1].content_text
    # The carried tail originates on page 1, so the second chunk spans pages 1-2.
    assert (chunks[1].page_start, chunks[1].page_end) == (1, 2)


def test_chunk_pages_preserves_stable_page_order() -> None:
    chunks = chunk_pages(
        [
            PageText(page_number=1, text="Page one first.\n\nPage one second."),
            PageText(page_number=2, text="Page two first.\n\nPage two second."),
        ],
        target_chars=18,
        overlap_chars=0,
    )

    assert [chunk.chunk_index for chunk in chunks] == [0, 1, 2, 3]
    assert [chunk.content_text for chunk in chunks] == [
        "Page one first.",
        "Page one second.",
        "Page two first.",
        "Page two second.",
    ]
    assert [(chunk.page_start, chunk.page_end) for chunk in chunks] == [
        (1, 1),
        (1, 1),
        (2, 2),
        (2, 2),
    ]


def test_chunk_pages_merges_small_pages_and_retains_page_span() -> None:
    chunks = chunk_pages(
        [
            PageText(page_number=1, text="Intro paragraph."),
            PageText(page_number=2, text="Follow-up paragraph."),
        ],
        target_chars=80,
        overlap_chars=0,
    )

    assert chunks[0].chunk_index == 0
    assert chunks[0].content_text == "Intro paragraph.\n\nFollow-up paragraph."
    assert chunks[0].page_start == 1
    assert chunks[0].page_end == 2
    assert len(chunks) == 1


def test_chunk_pages_keeps_chunks_under_target_when_splitting_paragraphs() -> None:
    chunks = chunk_pages(
        [
            PageText(
                page_number=5,
                text="alpha beta gamma delta epsilon zeta eta theta iota kappa",
            ),
        ],
        target_chars=16,
        overlap_chars=0,
    )

    assert [chunk.content_text for chunk in chunks] == [
        "alpha beta gamma",
        "delta epsilon",
        "zeta eta theta",
        "iota kappa",
    ]
    assert all(len(chunk.content_text) <= 16 for chunk in chunks)
    assert all(chunk.page_start == 5 and chunk.page_end == 5 for chunk in chunks)


def test_chunk_pages_skips_blank_pages_without_empty_chunks() -> None:
    chunks = chunk_pages(
        [
            PageText(page_number=1, text=" \n\n\t"),
            PageText(page_number=2, text="Only real content."),
            PageText(page_number=3, text="\r\n\r\n"),
        ],
        target_chars=80,
        overlap_chars=0,
    )

    assert len(chunks) == 1
    assert chunks[0].content_text == "Only real content."
    assert chunks[0].page_start == 2
    assert chunks[0].page_end == 2


def test_chunk_pages_returns_one_chunk_for_tiny_single_page_document() -> None:
    chunks = chunk_pages(
        [PageText(page_number=7, text="tiny note")],
        target_chars=1800,
        overlap_chars=0,
    )

    assert len(chunks) == 1
    assert chunks[0].chunk_index == 0
    assert chunks[0].content_text == "tiny note"
    assert chunks[0].page_start == 7
    assert chunks[0].page_end == 7


def test_chunk_pages_emits_single_token_that_exceeds_target_without_looping() -> None:
    chunks = chunk_pages(
        [PageText(page_number=9, text="supercalifragilistic")],
        target_chars=8,
        overlap_chars=0,
    )

    assert len(chunks) == 1
    assert chunks[0].content_text == "supercalifragilistic"
    assert chunks[0].page_start == 9
    assert chunks[0].page_end == 9


def test_chunk_pages_structured_records_merged_section_paths() -> None:
    from anima_server.services.documents.chunking import chunk_pages_structured

    chunks = chunk_pages_structured(
        [
            PageText(
                page_number=1,
                text="# Alpha\n\nShort alpha body.\n\n# Beta\n\nShort beta body.",
            ),
        ],
        target_chars=200,
    )

    assert len(chunks) == 1
    merged = chunks[0]
    assert merged.section_title == "Alpha"
    assert merged.metadata_json == {"section_paths": ["Alpha", "Beta"]}


def test_chunk_pages_structured_keeps_single_titled_path_after_preamble_merge() -> None:
    from anima_server.services.documents.chunking import chunk_pages_structured

    chunks = chunk_pages_structured(
        [
            PageText(
                page_number=1,
                text="Intro preamble before any heading.\n\n# Alpha\n\nAlpha body.",
            ),
        ],
        target_chars=300,
    )

    assert len(chunks) == 1
    merged = chunks[0]
    assert merged.section_title is None
    assert merged.metadata_json == {"section_paths": ["Alpha"]}


def test_chunk_pages_structured_preserves_overlong_single_path_in_metadata() -> None:
    from anima_server.services.documents.chunking import chunk_pages_structured

    long_heading = "SPECIFICATION " + "DETAIL " * 40  # single ALL-CAPS heading > 255
    long_heading = long_heading.strip()[:300]
    chunks = chunk_pages_structured(
        [PageText(page_number=1, text=f"# {long_heading}\n\nBody under a deep heading.")],
        target_chars=400,
    )

    assert len(chunks) == 1
    chunk = chunks[0]
    assert len(chunk.section_title) > 255  # pre-insert value keeps the full path
    assert chunk.metadata_json == {"section_paths": [chunk.section_title]}
