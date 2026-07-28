"""Deterministic generator for the golden-corpus PDF fixtures (PDP-012 Task 5).

Produces four PDFs — ``simple.pdf``, ``multicolumn.pdf``, ``tables.pdf``,
``scanned.pdf`` — plus ``gold.json`` describing the queries and content
assertions ``tests/test_golden_corpus.py`` drives through the real Docling
parser, fastembed embeddings, and ONNX reranker.

No network access, no randomness, no wall-clock timestamps: running this
script twice must produce byte-identical files (verified by
``test_golden_corpus.py::test_generate_fixtures_is_deterministic``). The one
external input is pypdfium2's rasterizer, used only to turn a throwaway
in-memory text PDF into the pixel data embedded in ``scanned.pdf`` — pdfium's
rendering is itself deterministic for a given font/text/scale, which the
determinism test guards.

Run directly to regenerate the checked-in fixtures after editing this file:

    uv run --project apps/server python \\
        apps/server/tests/fixtures/golden_corpus/generate_fixtures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# tests/fixtures/golden_corpus/generate_fixtures.py -> tests/ two levels up.
# Added explicitly (rather than relying on pytest's rootdir insertion) so this
# script also runs standalone via `python generate_fixtures.py`.
_TESTS_DIR = Path(__file__).resolve().parents[2]
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from pdf_fixtures import TextRun, write_image_only_pdf, write_multi_text_pdf

FIXTURES_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# simple.pdf — single column, headings + body paragraphs.
# ---------------------------------------------------------------------------

SIMPLE_HEADING = "Nebula Basin Field Station"
SIMPLE_SUBHEAD_1 = "Water Reclamation Protocol"
SIMPLE_BODY_1 = (
    "The reclamation unit filters brine through a triple-stage membrane "
    "before it reaches the potable cistern."
)
SIMPLE_SUBHEAD_2 = "Emergency Shutdown"
SIMPLE_BODY_2 = (
    "If the pressure gauge exceeds ninety kilopascals, vent the auxiliary "
    "valve immediately and notify the duty engineer."
)


def _write_simple_pdf(path: Path) -> None:
    runs = [
        TextRun(x=72, y=700, text=SIMPLE_HEADING, font_size=18),
        TextRun(x=72, y=660, text=SIMPLE_SUBHEAD_1, font_size=14),
        TextRun(x=72, y=630, text=SIMPLE_BODY_1, font_size=11),
        TextRun(x=72, y=580, text=SIMPLE_SUBHEAD_2, font_size=14),
        TextRun(x=72, y=550, text=SIMPLE_BODY_2, font_size=11),
    ]
    write_multi_text_pdf(path, runs, media_box=(0, 0, 612, 792))


# ---------------------------------------------------------------------------
# multicolumn.pdf — two independent text columns at different x offsets.
# ---------------------------------------------------------------------------

# Two tall, narrow columns side by side with a wide gutter — the realistic
# newspaper geometry a layout model needs to recognise a column boundary.
# Both columns span the SAME vertical range (top to bottom), so reading order
# is the discriminator: a column-aware reader emits all of column 1 (heading +
# every body line, top to bottom) and only then all of column 2, whereas a
# naive y-descending / x-ascending sort interleaves the two columns row by row.
# The reading-order test compares column 1's LAST body line against column 2's
# FIRST body line: column-aware puts them in that order, a y-sort reverses them
# (column 2's first line sits near the top of the page, column 1's last near
# the bottom). Lines are kept short (~40 chars) so each column's text stays
# well clear of the ~100pt gutter between column1_x+width and column2_x.
COLUMN1_HEADING = "Marsh Orchid Cultivation Notes"
COLUMN1_LINES = [
    "Marsh orchids thrive in waterlogged peat.",
    "Keep the crown above the waterline.",
    "Feed a dilute tonic at first bloom.",
    "Shield the bed from hard overnight frost.",
    "Divide the rhizomes every third spring.",
    "Label each division by its bloom colour.",
]

COLUMN2_HEADING = "Solar Array Maintenance Notes"
COLUMN2_LINES = [
    "Wipe the panel glass with a soft cloth.",
    "Inspect mounting bolts for corrosion.",
    "Log the midday output current daily.",
    "Reseat any brittle connector insulation.",
    "Check the inverter fuse below eighty percent.",
    "Record the firmware after each update.",
]

_MULTICOLUMN_Y_START = 720
_MULTICOLUMN_LINE_HEIGHT = 26


def _column_runs(x: float, heading: str, lines: list[str]) -> list[TextRun]:
    runs = [TextRun(x=x, y=_MULTICOLUMN_Y_START, text=heading, font_size=12)]
    for index, line in enumerate(lines, start=1):
        runs.append(
            TextRun(
                x=x,
                y=_MULTICOLUMN_Y_START - index * _MULTICOLUMN_LINE_HEIGHT,
                text=line,
                font_size=9,
            )
        )
    return runs


def _write_multicolumn_pdf(path: Path) -> None:
    column1_x = 54
    column2_x = 330
    runs = [
        *_column_runs(column1_x, COLUMN1_HEADING, COLUMN1_LINES),
        *_column_runs(column2_x, COLUMN2_HEADING, COLUMN2_LINES),
    ]
    write_multi_text_pdf(path, runs, media_box=(0, 0, 612, 792))


# ---------------------------------------------------------------------------
# tables.pdf — a 4x6 grid (header row + 5 data rows) of positioned cells.
# ---------------------------------------------------------------------------

TABLE_HEADERS = ["Item", "Quantity", "Unit Price", "Total"]
TABLE_ROWS = [
    ["Oxygen Canister", "12", "45.00", "540.00"],
    ["Water Filter", "8", "22.50", "180.00"],
    ["Solar Cell", "20", "9.75", "195.00"],
    ["Medkit", "5", "60.00", "300.00"],
    ["Rope Spool", "15", "8.20", "123.00"],
]
TABLE_COLUMN_X = [72, 260, 360, 460]
TABLE_ROW_Y_START = 700
TABLE_ROW_HEIGHT = 40


def _write_tables_pdf(path: Path) -> None:
    runs: list[TextRun] = []
    grid = [TABLE_HEADERS, *TABLE_ROWS]
    for row_index, row in enumerate(grid):
        y = TABLE_ROW_Y_START - row_index * TABLE_ROW_HEIGHT
        for column_index, cell_text in enumerate(row):
            runs.append(
                TextRun(x=TABLE_COLUMN_X[column_index], y=y, text=cell_text, font_size=11)
            )
    write_multi_text_pdf(path, runs, media_box=(0, 0, 612, 792))


# ---------------------------------------------------------------------------
# scanned.pdf — rendered-to-image, zero text layer (forces the OCR path).
# ---------------------------------------------------------------------------

SCANNED_LINE_1 = "Coolant manifold pressure must stay below forty psi"
SCANNED_LINE_2 = "during the sunrise patrol."
SCANNED_PHRASE = f"{SCANNED_LINE_1} {SCANNED_LINE_2}"
_SCANNED_RENDER_SCALE = 4


def _write_scanned_pdf(path: Path, *, tmp_dir: Path) -> None:
    import pypdfium2 as pdfium

    source_path = tmp_dir / "_scanned_source.pdf"
    runs = [
        TextRun(x=40, y=150, text=SCANNED_LINE_1, font_size=28),
        TextRun(x=40, y=90, text=SCANNED_LINE_2, font_size=28),
    ]
    write_multi_text_pdf(source_path, runs, media_box=(0, 0, 700, 220))

    document = pdfium.PdfDocument(str(source_path))
    try:
        page = document[0]
        try:
            bitmap = page.render(scale=_SCANNED_RENDER_SCALE)
            try:
                image = bitmap.to_pil()
                write_image_only_pdf(path, image)
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        document.close()
    source_path.unlink()


# ---------------------------------------------------------------------------
# gold.json — queries and content assertions per fixture.
# ---------------------------------------------------------------------------


def _gold_corpus() -> dict[str, object]:
    return {
        "simple": {
            "extraction_phrases": ["triple-stage membrane", "auxiliary valve"],
            "queries": [
                {
                    "id": "simple-membrane",
                    "query": "What filters the brine before it reaches the cistern?",
                    "expect": "triple-stage membrane",
                },
                {
                    "id": "simple-valve",
                    "query": "What should I do if the pressure gauge exceeds ninety kilopascals?",
                    "expect": "auxiliary valve",
                },
            ],
        },
        "multicolumn": {
            # column1_first / column1_last / column2_first are the specific
            # lines the reading-order test compares: with the interleaved
            # y-layout (see _write_multicolumn_pdf) the test asserts column
            # 1's LAST body line precedes column 2's FIRST body line, which a
            # naive y-sort gets wrong.
            "column1_first_phrase": "Marsh orchids thrive in waterlogged peat",
            "column1_last_phrase": "Label each division by its bloom colour",
            "column2_first_phrase": "Wipe the panel glass",
            "queries": [
                {
                    "id": "multicolumn-orchid",
                    "query": "How often should marsh orchid rhizomes be divided?",
                    "expect": "every third spring",
                },
                {
                    "id": "multicolumn-solar",
                    "query": "When should I check the solar array inverter fuse?",
                    "expect": "eighty percent",
                },
            ],
        },
        "tables": {
            "row1": TABLE_ROWS[0],
            "row2": TABLE_ROWS[1],
            "queries": [
                {
                    "id": "tables-oxygen",
                    "query": "How many oxygen canisters are in stock?",
                    "expect": "Oxygen Canister",
                },
                {
                    "id": "tables-medkit",
                    "query": "What is the unit price of a medkit?",
                    "expect": "Medkit",
                },
            ],
        },
        "scanned": {
            "phrase": SCANNED_PHRASE,
            "queries": [
                {
                    "id": "scanned-coolant",
                    "query": "What is the maximum coolant manifold pressure allowed?",
                    "expect": "coolant manifold",
                },
            ],
        },
    }


def generate_all(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_simple_pdf(output_dir / "simple.pdf")
    _write_multicolumn_pdf(output_dir / "multicolumn.pdf")
    _write_tables_pdf(output_dir / "tables.pdf")
    _write_scanned_pdf(output_dir / "scanned.pdf", tmp_dir=output_dir)
    gold_path = output_dir / "gold.json"
    # newline="\n": without it, text mode translates "\n" to os.linesep, so a
    # Windows run emits CRLF and fails the byte-exact cross-environment
    # comparison against the (LF) checked-in copy.
    gold_path.write_text(
        json.dumps(_gold_corpus(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    generate_all(FIXTURES_DIR)
    print(f"Golden-corpus fixtures written to {FIXTURES_DIR}")
