"""Golden-corpus eval: real Docling + real fastembed + real ONNX reranker.

This is the epic's capstone validation. Every other document-pipeline test
stubs Docling (layout+table+OCR) and the embedding/reranker models so the
default suite stays fast, hermetic, and offline. This file is the one place
that drives real PDFs through the REAL stack end to end — discharging the
"never ran Docling end-to-end" pending item from PDP-004, plus the first
real validation of bge-small (fastembed) + ms-marco (the local reranker)
together.

Marker: ``golden_corpus`` — excluded from the default suite via
``pyproject.toml`` addopts, same treatment as ``retrieval_eval``. Run it
explicitly:

    uv sync --project . --extra docling
    ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project . pytest -m golden_corpus -s

The first run downloads the Docling model weights (~500MB-1GB) and the
fastembed/reranker ONNX models (~210MB); subsequent runs reuse the on-disk
cache under ``settings.data_dir`` and are fast. Every test below that needs
docling calls ``_skip_unless_docling()`` first and skips cleanly (with the
command above in the message) when the extra is not installed — see
``test_docling_guard_detects_absence`` for a guard-logic unit test that runs
regardless of whether docling happens to be installed in this environment.

Opting out of the model-load stubs: ``tests/conftest.py`` permanently
replaces ``fastembed_backend._create_model`` and ``reranker._create_model``
with raisers so ordinary tests can never trigger a network download.
The ``real_models`` fixture here opts back in for exactly the tests that use
it: it loads a *separate, pristine* copy of each module straight from its
source file (via ``importlib.util.spec_from_file_location``, never touching
``sys.modules``) and ``monkeypatch``-installs that copy's real
``_create_model`` over the stub. ``monkeypatch`` reverts this automatically
at the end of each test, so the raiser stubs are back in place for any other
test that might run afterward in the same process — the belt-and-suspenders
protection in conftest.py is preserved, not weakened.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import re
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from anima_server.models.runtime import RuntimeKnowledgeConcept
from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.services.agent import fastembed_backend as fastembed_backend_module
from anima_server.services.agent import pgvec_store as pgvec_module
from anima_server.services.agent.embeddings import generate_embedding
from anima_server.services.agent.vector_store import VectorSearchResult
from anima_server.services.documents import reranker as reranker_module
from anima_server.services.documents.chunking import chunk_pages_structured
from anima_server.services.documents.indexing import embed_document_chunks
from anima_server.services.documents.models import DocumentRegistration
from anima_server.services.documents.parsing import (
    PARSE_QUALITY_DOCLING,
    PARSE_QUALITY_PREVIEW,
    extract_document_text,
)
from anima_server.services.documents.parsing_pack import (
    ensure_parsing_pack,
    pack_status,
    parsing_pack_ready,
)
from anima_server.services.documents.pdf_text import extract_pdf_text
from anima_server.services.documents.rag import search_document_chunks
from anima_server.services.documents.store import register_document, replace_document_chunks
from anima_server.services.ingestion.adapters.documents import sync_document_source
from sqlalchemy import func, select

pytestmark = pytest.mark.golden_corpus
pytest_plugins = ("conftest_runtime",)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "golden_corpus"
GOLD: dict[str, Any] = json.loads((FIXTURES_DIR / "gold.json").read_text(encoding="utf-8"))
FIXTURE_NAMES = ["simple", "multicolumn", "tables", "scanned"]
USER_ID = 1

# Model download allowed here; the whole point of this file. 30 minutes
# covers a cold cache on a slow connection (see task-5-report.md for the
# actual observed time).
_PACK_DOWNLOAD_TIMEOUT_S = 1800.0

# OCR is never pixel-perfect: the scanned fixture's phrase is recovered by
# fuzzy token overlap rather than an exact substring match. Tuned against the
# real Docling+EasyOCR output for this fixture (see task-5-report.md).
_OCR_TOKEN_OVERLAP_THRESHOLD = 0.8

_WORD_RE = re.compile(r"[a-z0-9]+")


def _docling_installed() -> bool:
    return importlib.util.find_spec("docling") is not None


def _skip_unless_docling() -> None:
    if not _docling_installed():
        pytest.skip(
            "docling extra not installed; run "
            "`uv sync --project . --extra docling && "
            "ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project . "
            "pytest -m golden_corpus -s`"
        )


def test_docling_guard_detects_absence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit-test the skip guard itself, independent of whether docling
    happens to be installed in this environment: this is the "verify it
    SKIPS cleanly without docling" check from the task brief. Faking
    ``find_spec`` to report absence must flip the guard, without needing an
    environment that actually lacks the extra.
    """
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)

    assert _docling_installed() is False


def _load_generator_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "golden_corpus_generate_fixtures", FIXTURES_DIR / "generate_fixtures.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generate_fixtures_is_deterministic(tmp_path: Path) -> None:
    """No docling needed — PDF generation must be reproducible.

    The three hand-built text PDFs and gold.json are pure deterministic byte
    output, so they are byte-compared both run-to-run and against the
    checked-in copies. scanned.pdf is the one fixture with a rasterization step
    (pypdfium2 render -> Pillow image -> zlib-compressed XObject); its exact
    bytes depend on the pypdfium2 / Pillow / zlib versions (Codex hit a 1-byte
    Flate-length difference across environments), so byte-comparing it against
    the checked-in copy is not portable. Instead assert its LOAD-BEARING
    invariant — a genuine image-only PDF with no extractable text layer, which
    is exactly what makes the OCR path meaningful.
    """
    from anima_server.services.documents.pdfium_text import extract_pdf_text_pdfium

    generator = _load_generator_module()
    output_a = tmp_path / "run_a"
    output_b = tmp_path / "run_b"

    generator.generate_all(output_a)
    generator.generate_all(output_b)

    # In-environment reproducibility: two runs identical for every fixture
    # (same env -> same raster -> same zlib, so scanned.pdf is stable here).
    all_names = ["simple.pdf", "multicolumn.pdf", "tables.pdf", "scanned.pdf", "gold.json"]
    for name in all_names:
        content_a = (output_a / name).read_bytes()
        content_b = (output_b / name).read_bytes()
        assert content_a == content_b, f"{name} was not byte-identical across two runs"

    # Cross-environment: the deterministic fixtures must match the checked-in
    # copies so a stale generator is caught.
    for name in ["simple.pdf", "multicolumn.pdf", "tables.pdf", "gold.json"]:
        checked_in = (FIXTURES_DIR / name).read_bytes()
        generated = (output_a / name).read_bytes()
        assert checked_in == generated, (
            f"checked-in {name} is stale relative to generate_fixtures.py — "
            "regenerate with `python tests/fixtures/golden_corpus/generate_fixtures.py`"
        )

    # scanned.pdf: assert the invariant (image-only, no text layer) on both the
    # freshly generated copy and the checked-in one, rather than byte-equality.
    for scanned_path in ((output_a / "scanned.pdf"), (FIXTURES_DIR / "scanned.pdf")):
        with pytest.raises(RuntimeError, match="no extractable text"):
            extract_pdf_text_pdfium(str(scanned_path))


def test_prefetch_models_import_path_is_valid() -> None:
    """The epic's open item: PDP-004 never verified this import against a
    real docling install. It does — no fix to parsing_pack.py was needed.
    """
    _skip_unless_docling()

    from docling.utils.model_downloader import download_models

    assert callable(download_models)


# ---------------------------------------------------------------------------
# Parsing-pack readiness (real model download).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def parsing_pack_ready_fixture() -> None:
    _skip_unless_docling()
    ensure_parsing_pack()
    deadline = time.monotonic() + _PACK_DOWNLOAD_TIMEOUT_S
    while not parsing_pack_ready():
        status = pack_status()
        if status.state == "error":
            pytest.fail(f"Parsing pack download failed: {status.error}")
        if time.monotonic() > deadline:
            pytest.fail(
                f"Timed out after {_PACK_DOWNLOAD_TIMEOUT_S}s waiting for the "
                "parsing pack download."
            )
        time.sleep(2)


# ---------------------------------------------------------------------------
# Real extraction (the actual Docling conversion, run once per fixture and
# shared across the tests below).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def golden_extractions(parsing_pack_ready_fixture: None) -> dict[str, Any]:
    return {
        name: extract_document_text(str(FIXTURES_DIR / f"{name}.pdf"))
        for name in FIXTURE_NAMES
    }


def _joined_text(outcome: Any) -> str:
    return "\n".join(page.text for page in outcome.pages)


def test_docling_parses_all_fixtures_at_docling_quality(
    golden_extractions: dict[str, Any],
) -> None:
    for name, outcome in golden_extractions.items():
        assert outcome.parse_quality == PARSE_QUALITY_DOCLING, (
            f"{name}.pdf did not parse at docling quality "
            f"(got {outcome.parse_quality!r}) — the pack should be ready by now"
        )


def test_simple_pdf_phrases_present(golden_extractions: dict[str, Any]) -> None:
    text = _joined_text(golden_extractions["simple"]).lower()
    for phrase in GOLD["simple"]["extraction_phrases"]:
        assert phrase.lower() in text, f"expected phrase {phrase!r} missing from Docling output"


def test_multicolumn_reading_order(golden_extractions: dict[str, Any]) -> None:
    """Genuinely column-aware reading order, not a y-sort coincidence.

    The fixture interleaves the two columns' y-values (column 2's rows sit
    between column 1's — see ``generate_fixtures._write_multicolumn_pdf``), so
    a naive extractor that sorts by y-descending then x-ascending would place
    column 2's FIRST body line above column 1's LAST body line. The assertion
    therefore compares column 1's *last* line against column 2's *first*:
    only a reader that groups by column (all of column 1 top-to-bottom, then
    all of column 2) puts them in the asserted order. A naive y-sort fails it.
    """
    text = _joined_text(golden_extractions["multicolumn"]).lower()
    column1_first = GOLD["multicolumn"]["column1_first_phrase"].lower()
    column1_last = GOLD["multicolumn"]["column1_last_phrase"].lower()
    column2_first = GOLD["multicolumn"]["column2_first_phrase"].lower()

    column1_first_index = text.find(column1_first)
    column1_last_index = text.find(column1_last)
    column2_first_index = text.find(column2_first)
    assert column1_first_index != -1, f"column 1 first line {column1_first!r} missing"
    assert column1_last_index != -1, f"column 1 last line {column1_last!r} missing"
    assert column2_first_index != -1, f"column 2 first line {column2_first!r} missing"
    # Sanity within column 1: its first line precedes its last line.
    assert column1_first_index < column1_last_index, (
        "column 1's own lines are out of order "
        f"(first at {column1_first_index}, last at {column1_last_index})"
    )
    # The load-bearing column-awareness check: column 1's LAST line must come
    # before column 2's FIRST line. Under the interleaved y-layout a naive
    # y-sort would reverse these.
    assert column1_last_index < column2_first_index, (
        "expected all of column 1 to precede column 2 in reading order — "
        "column 1's last line came AFTER column 2's first line, which is what "
        "a column-unaware (y-sorted) extractor would produce "
        f"(column1_last at {column1_last_index}, column2_first at {column2_first_index})"
    )


def test_tables_cell_values_present_and_row_reading_order(
    golden_extractions: dict[str, Any],
) -> None:
    text = _joined_text(golden_extractions["tables"]).lower()
    row1 = GOLD["tables"]["row1"]
    row2 = GOLD["tables"]["row2"]
    for cell in [*row1, *row2]:
        assert cell.lower() in text, f"expected table cell {cell!r} missing from Docling output"

    # Reading-order sanity: row1's own cells (e.g. "Oxygen Canister" and its
    # "45.00" unit price, same row) must sit closer together in the
    # extracted text than "Oxygen Canister" and "Water Filter" (same column,
    # next row) — i.e. Docling did not transpose the grid into column-major
    # order.
    row1_label, _qty, row1_price, _total = row1
    row2_label = row2[0]
    row1_label_index = text.find(row1_label.lower())
    row1_price_index = text.find(row1_price.lower())
    row2_label_index = text.find(row2_label.lower())

    same_row_distance = abs(row1_price_index - row1_label_index)
    same_column_distance = abs(row2_label_index - row1_label_index)
    assert same_row_distance < same_column_distance, (
        f"expected same-row cells to be closer ({same_row_distance} chars) than "
        f"same-column cells ({same_column_distance} chars) in the extracted text"
    )


def _tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def _token_overlap_ratio(expected: str, actual_text: str) -> float:
    expected_tokens = _tokenize(expected)
    actual_tokens = set(_tokenize(actual_text))
    if not expected_tokens:
        return 1.0
    hits = sum(1 for token in expected_tokens if token in actual_tokens)
    return hits / len(expected_tokens)


def test_scanned_pdf_ocr_recovers_phrase(golden_extractions: dict[str, Any]) -> None:
    text = _joined_text(golden_extractions["scanned"])
    expected = GOLD["scanned"]["phrase"]
    ratio = _token_overlap_ratio(expected, text)
    assert ratio >= _OCR_TOKEN_OVERLAP_THRESHOLD, (
        f"OCR recovered only {ratio:.0%} of expected tokens from {expected!r} "
        f"(need >= {_OCR_TOKEN_OVERLAP_THRESHOLD:.0%}); got: {text!r}"
    )


# ---------------------------------------------------------------------------
# Real fastembed + real reranker: opt out of the conftest.py model-load
# stubs for exactly the tests that need them (see module docstring).
# ---------------------------------------------------------------------------


def _load_pristine_module(module: ModuleType) -> ModuleType:
    """Load a fresh, independent copy of *module* straight from its source
    file — never touching ``sys.modules`` — so its (real, un-monkeypatched)
    ``_create_model`` can be lifted out without disturbing the live module
    object conftest.py already stubbed.
    """
    spec = importlib.util.spec_from_file_location(
        f"{module.__name__}__pristine_copy", module.__file__
    )
    assert spec is not None and spec.loader is not None
    pristine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pristine)
    return pristine


@pytest.fixture()
def real_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        fastembed_backend_module,
        "_create_model",
        _load_pristine_module(fastembed_backend_module)._create_model,
    )
    monkeypatch.setattr(
        reranker_module,
        "_create_model",
        _load_pristine_module(reranker_module)._create_model,
    )


@pytest.fixture(scope="module", autouse=True)
def _reset_model_caches_after_module() -> Any:
    yield
    # Belt-and-suspenders cleanup: return the process-wide model latches to
    # cold so nothing from this file's real-model loads leaks into whatever
    # runs in this interpreter afterward.
    fastembed_backend_module._reset_backend_for_tests()
    reranker_module._reset_model_cache_for_tests()


def _cosine_search_by_vector(
    self: Any,
    user_id: int,
    *,
    query_embedding: list[float],
    limit: int = 10,
    category: str | None = None,
    source_types: list[str] | None = None,
    source_ids: list[int] | None = None,
    source_id_query: Any | None = None,
) -> list[VectorSearchResult]:
    """The cosine ranking pgvector's ``<=>`` performs, over the SQLite
    scratch DB used in tests (mirrors ``test_retrieval_eval.py``'s helper of
    the same name — pgvector itself only runs against real PostgreSQL).
    """
    import math

    stmt = select(RuntimeEmbedding).where(RuntimeEmbedding.user_id == user_id)
    if source_types is not None:
        stmt = stmt.where(RuntimeEmbedding.source_type.in_(source_types))
    if source_ids is not None:
        stmt = stmt.where(RuntimeEmbedding.source_id.in_(source_ids))
    if source_id_query is not None:
        allowed = set(self._db.scalars(source_id_query).all())
        stmt = stmt.where(RuntimeEmbedding.source_id.in_(allowed))
    rows = list(self._db.scalars(stmt).all())

    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=False))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if not norm_a or not norm_b:
            return 0.0
        return dot / (norm_a * norm_b)

    scored = sorted(
        (
            VectorSearchResult(
                item_id=row.source_id,
                content=row.content_preview or "",
                category=row.category,
                importance=row.importance,
                similarity=_cosine(query_embedding, list(row.embedding)),
                source_type=row.source_type,
            )
            for row in rows
        ),
        key=lambda result: result.similarity,
        reverse=True,
    )
    return scored[:limit]


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture()
def golden_corpus_index(
    runtime_db: Any,
    monkeypatch: pytest.MonkeyPatch,
    real_models: None,
    golden_extractions: dict[str, Any],
) -> dict[str, int]:
    """Ingest all four fixtures — real Docling chunks, real fastembed
    embeddings — into the runtime DB, ready for real retrieval.
    """
    monkeypatch.setattr(
        pgvec_module.PgVecStore, "search_by_vector", _cosine_search_by_vector
    )
    document_ids: dict[str, int] = {}
    for name in FIXTURE_NAMES:
        outcome = golden_extractions[name]
        chunks = chunk_pages_structured(outcome.pages, target_chars=600)
        document = register_document(
            runtime_db,
            DocumentRegistration(
                user_id=USER_ID,
                filename=f"{name}.pdf",
                mime_type="application/pdf",
                storage_path=f".anima/documents/{USER_ID}/{name}.pdf",
                sha256=_sha(name),
                size_bytes=1024,
            ),
        )
        replace_document_chunks(
            runtime_db,
            document_id=document.id,
            chunks=chunks,
            parse_quality=outcome.parse_quality,
        )
        embedded = embed_document_chunks(
            runtime_db,
            user_id=USER_ID,
            document_id=document.id,
            embedding_fn=generate_embedding,
        )
        assert embedded == len(chunks), (
            f"{name}.pdf: expected {len(chunks)} chunks embedded, got {embedded} "
            "(real fastembed model failed to load or produced no vectors)"
        )
        document_ids[name] = document.id
    return document_ids


def test_golden_corpus_retrieval_hits_top5(
    golden_corpus_index: dict[str, int],
    runtime_db: Any,
) -> None:
    """This also validates bge-small (fastembed) + ms-marco (the local
    reranker) working together end to end for the first time — both are
    real here (see ``real_models``); ``settings.retrieval_reranker``
    defaults to "local" so the shipped default path (hybrid + rerank) runs
    unmodified.

    A hit is judged against ``content`` *and* ``section_title`` together —
    both are part of ``DocumentRagResult``, the surface actually returned to
    callers (a citation shows the section a passage came from alongside its
    body). This matters for the scanned fixture: Docling reasonably reads its
    single large isolated OCR'd line as a heading, so the expected phrase
    lands in ``section_title`` rather than ``content`` — confirmed a correct,
    real-content hit (not a quality gap) by inspecting the actual chunk
    (see task-5-report.md).
    """
    misses: list[str] = []
    ranks: dict[str, int | None] = {}
    for spec in GOLD.values():
        for query_item in spec["queries"]:
            results = search_document_chunks(
                runtime_db,
                USER_ID,
                query_item["query"],
                limit=5,
                embedding_fn=generate_embedding,
            )
            needle = query_item["expect"].lower()
            rank = next(
                (
                    position
                    for position, result in enumerate(results, start=1)
                    if needle in result.content.lower()
                    or needle in (result.section_title or "").lower()
                ),
                None,
            )
            ranks[query_item["id"]] = rank
            if rank is None:
                misses.append(query_item["id"])

    print(f"\ngolden-corpus retrieval ranks (top-5): {ranks}")
    assert not misses, f"gold queries missed top-5: {misses} (ranks: {ranks})"


# ---------------------------------------------------------------------------
# Wiki-gate: preview-quality ingest must not compile knowledge concepts;
# docling-quality ingest is allowed to (deterministic compiler — see
# conftest.py's `settings.knowledge_compiler = "deterministic"` default, no
# LLM involved, so the gate itself is what's under test).
# ---------------------------------------------------------------------------


def test_wiki_gate_docling_quality_compiles_preview_quality_skips(
    golden_extractions: dict[str, Any],
    runtime_db: Any,
) -> None:
    docling_outcome = golden_extractions["simple"]
    docling_chunks = chunk_pages_structured(docling_outcome.pages, target_chars=600)

    preview_pages = extract_pdf_text(str(FIXTURES_DIR / "simple.pdf"))
    preview_chunks = chunk_pages_structured(preview_pages, target_chars=600)

    docling_document = register_document(
        runtime_db,
        DocumentRegistration(
            user_id=USER_ID,
            filename="simple-docling.pdf",
            mime_type="application/pdf",
            storage_path=f".anima/documents/{USER_ID}/simple-docling.pdf",
            sha256=_sha("simple-docling"),
            size_bytes=1024,
        ),
    )
    replace_document_chunks(
        runtime_db,
        document_id=docling_document.id,
        chunks=docling_chunks,
        parse_quality=PARSE_QUALITY_DOCLING,
    )

    preview_document = register_document(
        runtime_db,
        DocumentRegistration(
            user_id=USER_ID,
            filename="simple-preview.pdf",
            mime_type="application/pdf",
            storage_path=f".anima/documents/{USER_ID}/simple-preview.pdf",
            sha256=_sha("simple-preview"),
            size_bytes=1024,
        ),
    )
    replace_document_chunks(
        runtime_db,
        document_id=preview_document.id,
        chunks=preview_chunks,
        parse_quality=PARSE_QUALITY_PREVIEW,
    )

    # Mirrors the real gate expression in pdf_workflow.py's "embedded" state:
    # `compile_knowledge=document.parse_quality == PARSE_QUALITY_DOCLING`.
    sync_document_source(
        runtime_db,
        document=preview_document,
        compile_knowledge=preview_document.parse_quality == PARSE_QUALITY_DOCLING,
    )
    preview_concept_count = runtime_db.scalar(select(func.count(RuntimeKnowledgeConcept.id)))
    assert preview_concept_count == 0, (
        "preview-quality ingest must not compile any knowledge concepts "
        f"(found {preview_concept_count})"
    )

    sync_document_source(
        runtime_db,
        document=docling_document,
        compile_knowledge=docling_document.parse_quality == PARSE_QUALITY_DOCLING,
    )
    docling_concept_count = runtime_db.scalar(select(func.count(RuntimeKnowledgeConcept.id)))
    assert docling_concept_count > 0, (
        "docling-quality ingest should compile at least one knowledge concept"
    )
