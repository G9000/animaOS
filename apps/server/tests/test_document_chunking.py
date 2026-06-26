from __future__ import annotations

from inspect import signature

import pytest
from anima_server.services.documents.chunking import chunk_pages
from anima_server.services.documents.pdf_text import PageText


def test_chunk_pages_default_overlap_is_zero() -> None:
    parameters = signature(chunk_pages).parameters

    assert parameters["overlap_chars"].default == 0


def test_chunk_pages_rejects_positive_overlap_until_supported() -> None:
    with pytest.raises(ValueError, match="overlap_chars"):
        chunk_pages(
            [PageText(page_number=1, text="First paragraph.\n\nSecond paragraph.")],
            target_chars=20,
            overlap_chars=1,
        )


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
