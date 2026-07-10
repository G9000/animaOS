from __future__ import annotations

from anima_server.services.documents.pdf_text import PageText
from anima_server.services.ingestion.structured import (
    chunk_structured_document,
    parse_markdown_structure,
    parse_page_structure,
)


def test_parse_markdown_structure_block_kinds_and_locators() -> None:
    content = "\n".join(
        [
            "# Title",
            "",
            "Intro paragraph.",
            "",
            "```python",
            "print('hi')",
            "```",
            "",
            "| a | b |",
            "| - | - |",
            "| 1 | 2 |",
            "",
            "Closing paragraph.",
        ]
    )

    document = parse_markdown_structure(content)

    kinds = [block.kind for block in document.blocks]
    assert kinds == ["heading", "paragraph", "code", "table", "paragraph"]
    heading = document.blocks[0]
    assert heading.text == "Title"
    assert heading.heading_level == 1
    assert (heading.line_start, heading.line_end) == (1, 1)
    code = document.blocks[2]
    assert code.text.startswith("```python")
    assert code.text.endswith("```")
    table = document.blocks[3]
    assert table.text.count("\n") == 2
    assert document.blocks[4].text == "Closing paragraph."


def test_sections_derive_nested_paths_and_preface() -> None:
    content = "\n".join(
        [
            "Preface before any heading.",
            "",
            "# Guide",
            "",
            "Guide intro.",
            "",
            "## Setup",
            "",
            "Setup steps.",
            "",
            "## Usage",
            "",
            "Usage notes.",
            "",
            "# Appendix",
            "",
            "Extra details.",
        ]
    )

    document = parse_markdown_structure(content)
    sections = document.sections()

    assert [section.section_path for section in sections] == [
        "",
        "Guide",
        "Guide > Setup",
        "Guide > Usage",
        "Appendix",
    ]
    assert sections[0].heading_level == 0
    assert sections[2].heading_level == 2
    assert "Setup steps." in sections[2].content_text
    outline = document.outline()
    assert [entry["section_path"] for entry in outline] == [
        "",
        "Guide",
        "Guide > Setup",
        "Guide > Usage",
        "Appendix",
    ]
    assert outline[1]["heading_level"] == 1


def test_sections_reset_deeper_levels_when_parent_changes() -> None:
    content = "# A\n\nBody a.\n\n## A1\n\nBody a1.\n\n# B\n\n## B1\n\nBody b1."

    sections = parse_markdown_structure(content).sections()

    # "B" stays in the outline as a heading-only section; its path resets the
    # deeper "A1" level so "B1" nests under "B", not under "A".
    assert [section.section_path for section in sections] == [
        "A",
        "A > A1",
        "B",
        "B > B1",
    ]
    assert sections[2].content_text == ""


def test_parse_page_structure_detects_conservative_headings() -> None:
    pages = [
        PageText(
            page_number=1,
            text="3.2 Retention Results\n\nRetention improved in every cohort.",
        ),
        PageText(
            page_number=2,
            text="APPENDIX\n\nRaw tables follow. This sentence is a normal paragraph.",
        ),
    ]

    document = parse_page_structure(pages)

    kinds = [(block.kind, block.page_number) for block in document.blocks]
    assert kinds == [
        ("heading", 1),
        ("paragraph", 1),
        ("heading", 2),
        ("paragraph", 2),
    ]
    assert document.blocks[0].heading_level == 2
    assert document.blocks[2].heading_level == 1
    sections = document.sections()
    assert sections[0].section_path == "3.2 Retention Results"
    assert sections[0].page_start == 1


def test_parse_page_structure_does_not_over_detect_headings() -> None:
    pages = [
        PageText(
            page_number=1,
            text=(
                "This is an ordinary sentence that ends with a period.\n\n"
                "Short line."
            ),
        ),
    ]

    document = parse_page_structure(pages)

    assert all(block.kind == "paragraph" for block in document.blocks)


def test_chunker_merges_small_sections_up_to_target() -> None:
    content = "\n".join(
        [
            "# One",
            "",
            "Alpha body.",
            "",
            "# Two",
            "",
            "Beta body.",
            "",
            "# Three",
            "",
            "Gamma body.",
        ]
    )

    chunks = chunk_structured_document(
        parse_markdown_structure(content),
        target_chars=200,
    )

    assert len(chunks) == 1
    assert chunks[0].section_indexes == (0, 1, 2)
    assert "Alpha body." in chunks[0].content_text
    assert "Gamma body." in chunks[0].content_text


def test_chunker_splits_oversized_section_with_overlap_parts() -> None:
    body_one = " ".join(["alpha"] * 60)  # ~360 chars
    body_two = " ".join(["beta"] * 60)  # ~300 chars
    content = f"# Big\n\n{body_one}\n\n{body_two}"

    chunks = chunk_structured_document(
        parse_markdown_structure(content),
        target_chars=400,
        overlap_chars=30,
    )

    assert len(chunks) == 2
    assert [chunk.part for chunk in chunks] == [1, 2]
    assert all(chunk.section_path == "Big" for chunk in chunks)
    assert chunks[0].content_text.startswith("alpha")
    # Second part carries an overlap tail from the first.
    assert chunks[1].content_text.startswith("alpha")
    assert "beta" in chunks[1].content_text


def test_chunker_keeps_tables_atomic() -> None:
    rows = "\n".join(f"| value {i} | detail {i} |" for i in range(60))
    table = f"| a | b |\n| - | - |\n{rows}"
    content = f"# Data\n\nIntro paragraph.\n\n{table}\n\nAfter table."

    chunks = chunk_structured_document(
        parse_markdown_structure(content),
        target_chars=300,
    )

    table_chunks = [chunk for chunk in chunks if chunk.is_atomic]
    assert len(table_chunks) == 1
    assert table_chunks[0].content_text.count("| value") == 60
    # The table is never split across chunks.
    assert all(
        "| value" not in chunk.content_text
        for chunk in chunks
        if not chunk.is_atomic
    )


def test_chunker_respects_target_bounds_for_non_atomic_chunks() -> None:
    paragraphs = "\n\n".join(
        " ".join([f"word{i}"] * 40) for i in range(12)
    )
    content = f"# Long\n\n{paragraphs}"

    target = 500
    chunks = chunk_structured_document(
        parse_markdown_structure(content),
        target_chars=target,
        overlap_chars=50,
    )

    assert len(chunks) > 1
    for chunk in chunks:
        if not chunk.is_atomic:
            # Parts may exceed target only by the carried overlap tail.
            assert len(chunk.content_text) <= target + 50 + 2


def test_to_markdown_round_trips_structure() -> None:
    content = "# Title\n\nBody paragraph.\n\n## Sub\n\nMore text."

    document = parse_markdown_structure(content)

    assert document.to_markdown() == content
