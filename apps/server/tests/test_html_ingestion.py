from __future__ import annotations

import pytest
from anima_server.services.ingestion.adapters.text import (
    STRUCTURED_MARKDOWN_ARTIFACT_KIND,
)
from anima_server.services.ingestion.adapters.web import (
    RAW_HTML_ARTIFACT_KIND,
    ingest_html_content,
    ingest_web_capture,
    reextract_source_html,
)

pytest_plugins = ("conftest_runtime",)

ARTICLE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <title>Relay Maintenance Guide - Pump Site</title>
  <link rel="canonical" href="https://example.com/articles/relay-maintenance" />
  <meta name="author" content="Dana Fixit" />
</head>
<body>
  <nav>
    <ul><li><a href="/">Home</a></li><li><a href="/products">Browse products</a></li></ul>
  </nav>
  <article>
    <h1>Relay Maintenance Guide</h1>
    <p>Relays must be inspected before every checkpoint restart to avoid cascade faults.</p>
    <h2>Inspection Steps</h2>
    <p>Open the relay housing and check the contact pads for pitting or discoloration.</p>
    <p>Measure coil resistance and compare it against the nameplate rating.</p>
    <h2>Replacement Intervals</h2>
    <p>Replace relays every 10,000 cycles or after any cascade fault event.</p>
  </article>
  <footer>
    <p>Copyright 2026 Pump Site. All rights reserved. Subscribe to our newsletter!</p>
  </footer>
</body>
</html>"""


def test_web_capture_html_mode_extracts_structured_spans(runtime_db) -> None:
    source, artifacts, spans = ingest_web_capture(
        runtime_db,
        user_id=3,
        url="https://example.com/articles/relay-maintenance?utm=x",
        html=ARTICLE_HTML,
    )

    assert source.kind == "web_capture"
    assert source.media_type == "text/html"
    assert source.title == "Relay Maintenance Guide"
    assert source.metadata_json["extractor"] == "trafilatura"
    assert source.metadata_json["author"] == "Dana Fixit"
    assert (
        source.metadata_json["canonical_url"]
        == "https://example.com/articles/relay-maintenance"
    )

    artifact_kinds = [artifact.artifact_kind for artifact in artifacts]
    assert artifact_kinds == [RAW_HTML_ARTIFACT_KIND, STRUCTURED_MARKDOWN_ARTIFACT_KIND]
    raw_artifact = artifacts[0]
    assert raw_artifact.content_text == ARTICLE_HTML.strip()

    section_paths = {
        span.metadata_json.get("section_path")
        for span in spans
        if span.span_kind == "section"
    }
    assert (
        "Relay Maintenance Guide > Inspection Steps" in section_paths
    )
    assert (
        "Relay Maintenance Guide > Replacement Intervals" in section_paths
    )

    all_text = "\n".join(span.content_text for span in spans)
    assert "cascade faults" in all_text
    assert "Browse products" not in all_text  # nav boilerplate stripped
    assert "newsletter" not in all_text  # footer boilerplate stripped


def test_web_capture_requires_exactly_one_input_mode(runtime_db) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        ingest_web_capture(
            runtime_db,
            user_id=3,
            url="https://example.com/page",
            readable_text="text",
            html="<html><body><p>hi</p></body></html>",
        )
    with pytest.raises(ValueError, match="exactly one"):
        ingest_web_capture(
            runtime_db,
            user_id=3,
            url="https://example.com/page",
        )


def test_web_capture_readable_text_mode_keeps_working(runtime_db) -> None:
    source, artifacts, spans = ingest_web_capture(
        runtime_db,
        user_id=3,
        url="https://example.com/legacy",
        readable_text="Intro paragraph.\n\nSecond paragraph.",
        title="Legacy Capture",
    )

    assert source.media_type == "text/plain"
    assert [artifact.artifact_kind for artifact in artifacts] == ["readable_text"]
    assert [span.locator_json["paragraph_index"] for span in spans] == [0, 1]


def test_web_capture_html_rejects_boilerplate_only_page(runtime_db) -> None:
    with pytest.raises(ValueError, match="No readable article content"):
        ingest_web_capture(
            runtime_db,
            user_id=3,
            url="https://example.com/empty",
            html="<html><body></body></html>",
        )


def test_html_upload_ingests_with_html_source_kind(runtime_db) -> None:
    source, artifacts, spans = ingest_html_content(
        runtime_db,
        user_id=3,
        html=ARTICLE_HTML,
        filename="../saved article.html",
    )

    assert source.kind == "html"
    assert source.source_uri == "html://saved article.html"
    assert source.metadata_json["filename"] == "saved article.html"
    assert "url" not in source.metadata_json
    assert [artifact.artifact_kind for artifact in artifacts] == [
        RAW_HTML_ARTIFACT_KIND,
        STRUCTURED_MARKDOWN_ARTIFACT_KIND,
    ]
    assert any(span.span_kind == "section" for span in spans)


def test_reextract_replaces_spans_idempotently(runtime_db) -> None:
    source, _artifacts, spans = ingest_web_capture(
        runtime_db,
        user_id=3,
        url="https://example.com/articles/relay-maintenance",
        html=ARTICLE_HTML,
    )
    original_span_ids = [span.id for span in spans]

    reextracted_source, artifacts, respans = reextract_source_html(
        runtime_db,
        user_id=3,
        source_id=source.id,
    )

    assert reextracted_source.id == source.id
    assert [artifact.artifact_kind for artifact in artifacts] == [
        RAW_HTML_ARTIFACT_KIND,
        STRUCTURED_MARKDOWN_ARTIFACT_KIND,
    ]
    # Unchanged extraction output keeps the same span rows.
    assert [span.id for span in respans] == original_span_ids
    assert [span.content_hash for span in respans] == [
        span.content_hash for span in spans
    ]


def test_reextract_rejects_missing_source_and_missing_raw_html(runtime_db) -> None:
    with pytest.raises(LookupError):
        reextract_source_html(runtime_db, user_id=3, source_id=999_999)

    source, _artifacts, _spans = ingest_web_capture(
        runtime_db,
        user_id=3,
        url="https://example.com/text-only",
        readable_text="Pre-extracted capture without raw HTML.",
    )
    with pytest.raises(ValueError, match="raw HTML"):
        reextract_source_html(runtime_db, user_id=3, source_id=source.id)
