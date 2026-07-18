from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


def _assemble_pdf(objects: list[bytes]) -> bytes:
    """Assemble a minimal single-revision PDF from indirect object bodies.

    Object N is ``objects[N - 1]``; each body is the ``<< ... >>`` dict (or
    dict + stream) without the ``N 0 obj``/``endobj`` wrapper — this function
    adds that, plus the xref table and trailer. Object 1 must be the Catalog.
    """
    chunks = [b"%PDF-1.4\n"]
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(sum(len(chunk) for chunk in chunks))
        chunks.append(f"{index} 0 obj\n".encode("ascii") + body + b"\nendobj\n")

    xref_offset = sum(len(chunk) for chunk in chunks)
    xref = [f"xref\n0 {len(objects) + 1}\n".encode("ascii")]
    xref.append(b"0000000000 65535 f \n")
    for offset in offsets:
        xref.append(f"{offset:010d} 00000 n \n".encode("ascii"))
    xref.append(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return b"".join(chunks + xref)


def _escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_text_pdf(path: Path, text: str) -> None:
    escaped = _escape_pdf_text(text)
    stream = f"BT\n/F1 12 Tf\n72 120 Td\n({escaped}) Tj\nET\n".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(stream)).encode("ascii")
        + b" >>\nstream\n"
        + stream
        + b"endstream",
    ]
    path.write_bytes(_assemble_pdf(objects))


@dataclass(frozen=True, slots=True)
class TextRun:
    """A single absolutely-positioned line of text on a page.

    ``x``/``y`` are PDF user-space points measured from the page's bottom-left
    corner (PDF's native origin) — placed via the ``Tm`` (set text matrix)
    operator rather than ``Td`` so each run is positioned independently
    instead of accumulating relative offsets within one ``BT``/``ET`` block.
    This is what lets ``write_multi_text_pdf`` place unrelated blocks (e.g.
    two newspaper-style columns, or a grid of table cells) anywhere on the
    page deterministically.
    """

    x: float
    y: float
    text: str
    font_size: int = 12


def _format_number(value: float) -> str:
    return f"{value:g}"


def _text_run_stream(runs: Sequence[TextRun]) -> bytes:
    lines = ["BT"]
    for run in runs:
        escaped = _escape_pdf_text(run.text)
        lines.append(f"/F1 {run.font_size} Tf")
        lines.append(f"1 0 0 1 {_format_number(run.x)} {_format_number(run.y)} Tm")
        lines.append(f"({escaped}) Tj")
    lines.append("ET")
    return ("\n".join(lines) + "\n").encode("ascii")


def write_multi_text_pdf(
    path: Path,
    runs: Sequence[TextRun],
    *,
    media_box: tuple[float, float, float, float] = (0, 0, 612, 792),
) -> None:
    """Write a single-page PDF with multiple absolutely-positioned text runs.

    Extends ``write_text_pdf`` (same hand-crafted object/xref plumbing, now
    shared via ``_assemble_pdf``) to support the golden-corpus fixtures:
    multi-column layouts and table grids, both of which need several text
    runs placed at distinct, deterministic coordinates on one page.
    """
    stream = _text_run_stream(runs)
    media_box_str = " ".join(_format_number(v) for v in media_box)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [{media_box_str}] "
            "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ).encode("ascii"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(stream)).encode("ascii")
        + b" >>\nstream\n"
        + stream
        + b"endstream",
    ]
    path.write_bytes(_assemble_pdf(objects))


def write_image_only_pdf(path: Path, image: object) -> None:
    """Write a single-page PDF whose only content is an embedded raster image.

    ``image`` is a Pillow ``Image``; it is converted to 8-bit grayscale and
    embedded as a ``DeviceGray``/``FlateDecode`` Image XObject filling the
    page. There is no ``/Font`` resource and no text-showing operator
    anywhere in the file, so pdfium's text layer extraction genuinely finds
    nothing (unlike ``write_text_pdf`` with empty text) — this is what forces
    the Docling OCR path in the golden-corpus scanned-PDF fixture.
    """
    import zlib

    gray = image.convert("L")
    width, height = gray.size
    raw = gray.tobytes()
    compressed = zlib.compress(raw, level=9)
    content = f"q {width} 0 0 {height} 0 0 cm /Im0 Do Q\n".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
            "/Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>"
        ).encode("ascii"),
        (
            f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
            "/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode "
            f"/Length {len(compressed)} >>\nstream\n"
        ).encode("ascii")
        + compressed
        + b"\nendstream",
        f"<< /Length {len(content)} >>\nstream\n".encode("ascii")
        + content
        + b"endstream",
    ]
    path.write_bytes(_assemble_pdf(objects))


__all__ = [
    "TextRun",
    "write_image_only_pdf",
    "write_multi_text_pdf",
    "write_text_pdf",
]
