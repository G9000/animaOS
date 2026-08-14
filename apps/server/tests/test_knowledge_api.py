from __future__ import annotations

import hashlib
import zipfile
from io import BytesIO

from anima_server.db.runtime import get_runtime_session_factory
from anima_server.models.runtime import (
    RuntimeKnowledgeBundleRun,
    RuntimeKnowledgeConcept,
    RuntimeKnowledgeConceptSource,
    RuntimeSource,
    RuntimeSourceArtifact,
    RuntimeSourceSpan,
)
from anima_server.services.corefs import logical
from anima_server.services.corefs.cutover import (
    approve_validation_cutover,
    begin_migration,
    publish_validation_readonly,
    reconcile_cutover_authority,
)
from anima_server.services.corefs.diary_migration import (
    read_prepared_writing_body,
    read_prepared_writing_snapshot,
)
from anima_server.services.corefs.knowledge_authority import decode_knowledge_document
from anima_server.services.sessions import unlock_session_store
from conftest import managed_test_client
from sqlalchemy import select


def _register(client, *, username: str) -> tuple[int, dict[str, str]]:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pw123456", "name": username},
    )
    assert response.status_code == 201
    payload = response.json()
    return int(payload["id"]), {"x-anima-unlock": str(payload["unlockToken"])}


def test_markdown_source_endpoint_creates_spans_and_compile_run() -> None:
    with managed_test_client("anima-knowledge-markdown-") as client:
        user_id, headers = _register(client, username="knowledge-md")

        response = client.post(
            "/api/knowledge/sources/markdown",
            headers=headers,
            json={
                "userId": user_id,
                "filename": "../notes.md",
                "title": "Knowledge Notes",
                "content": "# Heading\n\nParagraph body.",
                "compile": True,
            },
        )

        assert response.status_code == 201
        payload = response.json()
        assert payload["source"]["kind"] == "markdown"
        assert payload["source"]["sourceUri"] == "markdown://notes.md"
        assert [span["spanKind"] for span in payload["spans"]] == [
            "heading",
            "paragraph",
            "section",
        ]
        assert payload["compileRun"]["status"] == "completed"
        assert payload["compileRun"]["runType"] == "compile:initial"

        with get_runtime_session_factory()() as runtime_db:
            run = runtime_db.get(RuntimeKnowledgeBundleRun, payload["compileRun"]["id"])
            assert run is not None
            assert run.run_type == "compile:initial"
            assert run.source_id == payload["source"]["id"]
            concept = runtime_db.scalar(select(RuntimeKnowledgeConcept))
            assert concept is not None
            assert concept.concept_type == "source_summary"
            assert concept.metadata_json["compiled_from_source_id"] == payload["source"]["id"]
            assert runtime_db.scalar(select(RuntimeKnowledgeConceptSource)) is not None


def test_post_cutover_knowledge_sources_use_only_corefs(monkeypatch) -> None:
    from anima_server.services.corefs import migration as corefs_migration

    monkeypatch.setattr(
        corefs_migration,
        "schedule_unlocked_rebuild",
        lambda *_args, **_kwargs: False,
    )
    with managed_test_client("anima-knowledge-corefs-") as client:
        user_id, headers = _register(client, username="knowledge-corefs")
        token = headers["x-anima-unlock"]
        session = unlock_session_store.resolve(token)
        assert session is not None
        selected = session.corefs_session.validation_snapshot(session.corefs_keys)
        begin_migration()
        publish_validation_readonly(
            generation=int(selected["generation"]),
            catalog_hash=str(selected["catalogHash"]),
        )
        approve_validation_cutover()
        logical.execute_mutation_v1(
            corefs_session=session.corefs_session,
            keys=session.corefs_keys,
            selected=logical.CoreFsValidationSnapshot(
                int(selected["generation"]),
                str(selected["catalogHash"]),
            ),
            principal="user",
            mutation={"operation": "mkdir", "path": "Knowledge activation proof"},
        )
        marker = reconcile_cutover_authority(
            corefs_session=session.corefs_session,
            keys=session.corefs_keys,
        )
        assert marker is not None
        object.__setattr__(session, "content_authority", marker)

        markdown = "# Canonical Knowledge\n\nA portable zephyrblade reference."
        created = client.post(
            "/api/knowledge/sources/markdown",
            headers=headers,
            json={
                "userId": user_id,
                "filename": "../portable.md",
                "title": "Portable Reference",
                "content": markdown,
                "compile": True,
            },
        )
        assert created.status_code == 201, created.text
        payload = created.json()
        assert payload["source"]["kind"] == "markdown"
        assert payload["source"]["sourceUri"] == "markdown://portable.md"
        assert payload["compileRun"]["status"] == "completed"
        assert payload["compileRun"]["runType"] == "compile:derived"
        source_id = int(payload["source"]["id"])
        object_uri = payload["artifacts"][0]["metadata"]["objectUri"]
        stable_id = object_uri.rsplit("/", 1)[-1]
        item = next(
            candidate
            for candidate in read_prepared_writing_snapshot(session=session).objects
            if candidate.stable_id == stable_id
        )
        document = decode_knowledge_document(
            read_prepared_writing_body(session=session, item=item)
        )
        assert document.original_content == markdown
        assert "zephyrblade" in document.content

        listed = client.get(
            f"/api/knowledge/sources?userId={user_id}",
            headers=headers,
        )
        assert listed.status_code == 200, listed.text
        assert [source["id"] for source in listed.json()["sources"]] == [source_id]
        fetched = client.get(
            f"/api/knowledge/sources/{source_id}?userId={user_id}",
            headers=headers,
        )
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["source"]["sourceUri"] == "markdown://portable.md"
        searched = client.get(
            f"/api/knowledge/search?userId={user_id}&q=zephyrblade",
            headers=headers,
        )
        assert searched.status_code == 200, searched.text
        assert searched.json()["evidenceSpans"][0]["sourceId"] == source_id
        compiled = client.post(
            f"/api/knowledge/sources/{source_id}/compile?userId={user_id}",
            headers=headers,
        )
        assert compiled.status_code == 202, compiled.text
        assert compiled.json()["compileRun"]["runType"] == "compile:derived"

        captured = client.post(
            "/api/knowledge/sources/web-capture",
            headers=headers,
            json={
                "userId": user_id,
                "url": "https://example.test/reference",
                "html": (
                    "<html><head><title>Portable HTML</title></head>"
                    "<body><article><h1>Portable HTML</h1>"
                    "<p>Offline re-extraction source.</p></article></body></html>"
                ),
                "fetch": False,
                "compile": False,
            },
        )
        assert captured.status_code == 201, captured.text
        captured_id = int(captured.json()["source"]["id"])
        reextracted = client.post(
            f"/api/knowledge/sources/{captured_id}/reextract?userId={user_id}",
            headers=headers,
        )
        assert reextracted.status_code == 200, reextracted.text
        assert reextracted.json()["source"]["id"] == captured_id
        assert "Offline re-extraction source" in reextracted.json()["artifacts"][0][
            "contentText"
        ]

        okf_zip = BytesIO()
        with zipfile.ZipFile(okf_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "concepts/portable-alpha.md",
                "---\ntype: note\ntitle: Portable Alpha\n---\n\n"
                "Alpha links to [Beta](portable-beta.md).\n",
            )
            archive.writestr(
                "concepts/portable-beta.md",
                "---\ntype: claim\ntitle: Portable Beta\n---\n\n"
                "Beta remains offline.\n",
            )
        imported = client.post(
            f"/api/knowledge/import?userId={user_id}",
            headers=headers,
            files={
                "file": (
                    "portable.zip",
                    okf_zip.getvalue(),
                    "application/zip",
                )
            },
        )
        assert imported.status_code == 201, imported.text
        assert imported.json() == {"conceptCount": 2, "linkCount": 1}
        repeated_import = client.post(
            f"/api/knowledge/import?userId={user_id}",
            headers=headers,
            files={
                "file": (
                    "portable.zip",
                    okf_zip.getvalue(),
                    "application/zip",
                )
            },
        )
        assert repeated_import.status_code == 201, repeated_import.text
        assert len(
            client.get(
                f"/api/knowledge/sources?userId={user_id}",
                headers=headers,
            ).json()["sources"]
        ) == 4
        exported = client.get(
            f"/api/knowledge/export?userId={user_id}",
            headers=headers,
        )
        assert exported.status_code == 200, exported.text
        with zipfile.ZipFile(BytesIO(exported.content)) as archive:
            assert "concepts/portable-alpha.md" in archive.namelist()
            assert "concepts/portable-beta.md" in archive.namelist()
            assert "Alpha links to [Beta](portable-beta.md)." in archive.read(
                "concepts/portable-alpha.md"
            ).decode()
        linted = client.post(
            "/api/knowledge/lint",
            headers=headers,
            json={"userId": user_id},
        )
        assert linted.status_code == 200, linted.text
        assert linted.json() == {"findings": []}

        with get_runtime_session_factory()() as runtime_db:
            assert runtime_db.scalar(select(RuntimeSource).limit(1)) is None
            assert runtime_db.scalar(select(RuntimeSourceArtifact).limit(1)) is None
            assert runtime_db.scalar(select(RuntimeSourceSpan).limit(1)) is None
            assert runtime_db.scalar(select(RuntimeKnowledgeConcept).limit(1)) is None


def test_markdown_source_endpoint_compile_keeps_span_topics_active() -> None:
    with managed_test_client("anima-knowledge-markdown-compile-topics-") as client:
        user_id, headers = _register(client, username="knowledge-md-compile-topics")

        response = client.post(
            "/api/knowledge/sources/markdown",
            headers=headers,
            json={
                "userId": user_id,
                "filename": "release-notes.md",
                "title": "Release Notes",
                "content": "# Alpha\n\nFirst note.\n\n## Beta\n\nSecond note.",
                "compile": True,
            },
        )

        assert response.status_code == 201
        payload = response.json()

        with get_runtime_session_factory()() as runtime_db:
            concepts = list(
                runtime_db.scalars(select(RuntimeKnowledgeConcept)).all()
            )
            active_topics = [
                concept
                for concept in concepts
                if concept.concept_type == "topic" and concept.status == "active"
            ]
            active_summaries = [
                concept
                for concept in concepts
                if concept.concept_type == "source_summary"
                and concept.status == "active"
            ]

        evidence_spans = [
            span for span in payload["spans"] if span["spanKind"] != "section"
        ]
        assert len(active_topics) == len(evidence_spans)
        assert len(active_summaries) == 1


def test_markdown_source_endpoint_compile_reuses_adapter_compile_run() -> None:
    with managed_test_client("anima-knowledge-markdown-single-compile-") as client:
        user_id, headers = _register(client, username="knowledge-md-single-compile")

        response = client.post(
            "/api/knowledge/sources/markdown",
            headers=headers,
            json={
                "userId": user_id,
                "filename": "single-compile.md",
                "title": "Single Compile",
                "content": "# Heading\n\nParagraph body.",
                "compile": True,
            },
        )

        assert response.status_code == 201
        payload = response.json()

        with get_runtime_session_factory()() as runtime_db:
            compile_runs = list(
                runtime_db.scalars(
                    select(RuntimeKnowledgeBundleRun)
                    .where(RuntimeKnowledgeBundleRun.source_id == payload["source"]["id"])
                    .order_by(RuntimeKnowledgeBundleRun.id)
                ).all()
            )

        assert len(compile_runs) == 1
        assert payload["compileRun"]["id"] == compile_runs[0].id
        assert [run.run_type for run in compile_runs] == ["compile:initial"]


def test_source_endpoint_compile_false_skips_knowledge_compile() -> None:
    with managed_test_client("anima-knowledge-compile-false-") as client:
        user_id, headers = _register(client, username="knowledge-compile-false")

        text_response = client.post(
            "/api/knowledge/sources/text",
            headers=headers,
            json={
                "userId": user_id,
                "filename": "plain.txt",
                "title": "Plain Text",
                "content": "Plain text evidence.",
                "compile": False,
            },
        )
        markdown_response = client.post(
            "/api/knowledge/sources/markdown",
            headers=headers,
            json={
                "userId": user_id,
                "filename": "notes.md",
                "title": "Markdown Notes",
                "content": "# Notes\n\nMarkdown evidence.",
            },
        )
        web_response = client.post(
            "/api/knowledge/sources/web-capture",
            headers=headers,
            json={
                "userId": user_id,
                "url": "https://example.com/compile-false",
                "title": "Compile False Web",
                "readableText": "Captured web evidence.",
                "compile": False,
            },
        )

        assert text_response.status_code == 201
        assert markdown_response.status_code == 201
        assert web_response.status_code == 201
        assert "compileRun" not in text_response.json()
        assert "compileRun" not in markdown_response.json()
        assert "compileRun" not in web_response.json()

        with get_runtime_session_factory()() as runtime_db:
            assert runtime_db.scalar(select(RuntimeKnowledgeBundleRun)) is None
            assert runtime_db.scalar(select(RuntimeKnowledgeConcept)) is None


def test_compile_source_endpoint_invokes_compiler_for_existing_source() -> None:
    with managed_test_client("anima-knowledge-compile-existing-") as client:
        user_id, headers = _register(client, username="knowledge-compile-existing")
        source_response = client.post(
            "/api/knowledge/sources/text",
            headers=headers,
            json={
                "userId": user_id,
                "filename": "notes.txt",
                "title": "Existing Notes",
                "content": "Compiler evidence.",
            },
        )
        source_id = source_response.json()["source"]["id"]

        response = client.post(
            f"/api/knowledge/sources/{source_id}/compile?userId={user_id}",
            headers=headers,
        )

        assert response.status_code == 202
        payload = response.json()
        assert payload["compileRun"]["status"] == "completed"
        assert payload["compileRun"]["runType"] == "compile:initial"
        with get_runtime_session_factory()() as runtime_db:
            concept = runtime_db.scalar(select(RuntimeKnowledgeConcept))
            assert concept is not None
            assert concept.title == "Existing Notes"
            assert "Compiler evidence." in concept.body_markdown


def test_web_capture_endpoint_preserves_canonical_metadata() -> None:
    with managed_test_client("anima-knowledge-web-") as client:
        user_id, headers = _register(client, username="knowledge-web")

        response = client.post(
            "/api/knowledge/sources/web-capture",
            headers=headers,
            json={
                "userId": user_id,
                "url": "https://example.com/wiki?x=1",
                "canonicalUrl": "https://example.com/wiki",
                "title": "Example Wiki",
                "readableText": "First paragraph.\n\nSecond paragraph.",
            },
        )

        assert response.status_code == 201
        payload = response.json()
        assert payload["source"]["kind"] == "web_capture"
        assert payload["source"]["sourceUri"] == "https://example.com/wiki?x=1"
        assert payload["source"]["metadata"]["canonical_url"] == "https://example.com/wiki"
        assert [span["locator"]["paragraph_index"] for span in payload["spans"]] == [0, 1]

        with get_runtime_session_factory()() as runtime_db:
            source = runtime_db.scalar(
                select(RuntimeSource).where(RuntimeSource.id == payload["source"]["id"])
            )
            assert source is not None
            assert source.metadata_json["canonical_url"] == "https://example.com/wiki"


def test_web_capture_endpoint_rejects_urls_longer_than_source_uri_column() -> None:
    with managed_test_client("anima-knowledge-web-long-url-") as client:
        user_id, headers = _register(client, username="knowledge-web-long-url")
        long_url = "https://example.com/" + ("a" * 1005)

        response = client.post(
            "/api/knowledge/sources/web-capture",
            headers=headers,
            json={
                "userId": user_id,
                "url": long_url,
                "title": "Long URL",
                "readableText": "Readable body.",
            },
        )

        assert len(long_url) > 1024
        assert response.status_code == 422


def test_text_source_endpoint_rejects_empty_content() -> None:
    with managed_test_client("anima-knowledge-empty-") as client:
        user_id, headers = _register(client, username="knowledge-empty")

        response = client.post(
            "/api/knowledge/sources/text",
            headers=headers,
            json={"userId": user_id, "filename": "empty.txt", "content": "  \n\t"},
        )

        assert response.status_code == 422


def test_lists_sources_concepts_and_reads_concept_citations() -> None:
    with managed_test_client("anima-knowledge-list-") as client:
        user_id, headers = _register(client, username="knowledge-list")
        with get_runtime_session_factory()() as runtime_db:
            source, concept = _seed_source_concept(runtime_db, user_id=user_id)
            source_id = source.id
            concept_id = concept.id
            runtime_db.commit()

        sources_response = client.get(
            f"/api/knowledge/sources?userId={user_id}",
            headers=headers,
        )
        concepts_response = client.get(
            f"/api/knowledge/concepts?userId={user_id}",
            headers=headers,
        )
        concept_response = client.get(
            f"/api/knowledge/concepts/{concept_id}?userId={user_id}",
            headers=headers,
        )

        assert sources_response.status_code == 200
        assert sources_response.json()["sources"][0]["id"] == source_id
        assert concepts_response.status_code == 200
        assert concepts_response.json()["concepts"][0]["id"] == concept_id
        assert (
            concepts_response.json()["concepts"][0]["description"]
            == "A compiled citation concept."
        )
        assert concept_response.status_code == 200
        payload = concept_response.json()
        assert payload["id"] == concept_id
        assert payload["description"] == "A compiled citation concept."
        assert payload["citations"][0]["sourceId"] == source_id
        assert payload["citations"][0]["contentText"] == "Citation evidence."


def test_search_knowledge_endpoint_returns_matches_without_embeddings() -> None:
    with managed_test_client("anima-knowledge-search-") as client:
        user_id, headers = _register(client, username="knowledge-search")
        with get_runtime_session_factory()() as runtime_db:
            _seed_source_concept(runtime_db, user_id=user_id)
            runtime_db.commit()

        response = client.get(
            f"/api/knowledge/search?userId={user_id}&q=citation",
            headers=headers,
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["query"] == "citation"
        assert payload["concepts"][0]["title"] == "Citation Topic"
        assert payload["evidenceSpans"][0]["contentText"] == "Citation evidence."


def test_exports_and_imports_okf_bundle_zip() -> None:
    with managed_test_client("anima-knowledge-okf-") as client:
        user_id, headers = _register(client, username="knowledge-okf")
        with get_runtime_session_factory()() as runtime_db:
            _seed_source_concept(runtime_db, user_id=user_id)
            runtime_db.commit()

        export_response = client.get(
            f"/api/knowledge/export?userId={user_id}",
            headers=headers,
        )

        assert export_response.status_code == 200
        assert export_response.headers["content-type"] == "application/zip"
        bundle_zip = BytesIO(export_response.content)
        with zipfile.ZipFile(bundle_zip) as archive:
            assert "index.md" in archive.namelist()
            assert "concepts/topic-citation.md" in archive.namelist()

        bundle_zip.seek(0)
        import_response = client.post(
            f"/api/knowledge/import?userId={user_id}",
            headers=headers,
            files={"file": ("bundle.zip", bundle_zip.getvalue(), "application/zip")},
        )

        assert import_response.status_code == 201
        assert import_response.json()["conceptCount"] == 1


def test_import_okf_bundle_zip_returns_422_for_invalid_contents() -> None:
    with managed_test_client("anima-knowledge-invalid-okf-") as client:
        user_id, headers = _register(client, username="knowledge-invalid-okf")
        bundle_zip = BytesIO()
        with zipfile.ZipFile(bundle_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "concepts/ topic-unsafe.md",
                "---\n"
                "type: topic\n"
                "title: Unsafe topic\n"
                "---\n\n"
                "Body.\n",
            )

        response = client.post(
            f"/api/knowledge/import?userId={user_id}",
            headers=headers,
            files={"file": ("bundle.zip", bundle_zip.getvalue(), "application/zip")},
        )

        assert response.status_code == 422
        assert response.json()["error"] == "Invalid OKF bundle contents."


def _seed_source_concept(
    runtime_db,
    *,
    user_id: int,
) -> tuple[RuntimeSource, RuntimeKnowledgeConcept]:
    source = RuntimeSource(
        user_id=user_id,
        kind="text",
        source_uri="text://citation.txt",
        content_hash=_sha("Citation evidence."),
        title="Citation Source",
        media_type="text/plain",
        status="indexed",
    )
    runtime_db.add(source)
    runtime_db.flush()
    artifact = RuntimeSourceArtifact(
        user_id=user_id,
        source_id=source.id,
        artifact_kind="text",
        content_text="Citation evidence.",
        content_hash=_sha("Citation evidence."),
    )
    runtime_db.add(artifact)
    runtime_db.flush()
    span = RuntimeSourceSpan(
        user_id=user_id,
        source_id=source.id,
        artifact_id=artifact.id,
        span_kind="paragraph",
        locator_json={"paragraph_index": 0},
        locator_hash=RuntimeSourceSpan.compute_locator_hash({"paragraph_index": 0}),
        content_text="Citation evidence.",
        content_hash=_sha("Citation evidence."),
    )
    concept = RuntimeKnowledgeConcept(
        user_id=user_id,
        concept_type="topic",
        slug="topic-citation",
        title="Citation Topic",
        description="A compiled citation concept.",
        body_markdown="Compiled citation notes.",
        frontmatter_json={"type": "topic", "title": "Citation Topic"},
        content_hash=_sha("Compiled citation notes."),
        status="active",
    )
    runtime_db.add_all([span, concept])
    runtime_db.flush()
    runtime_db.add(
        RuntimeKnowledgeConceptSource(
            user_id=user_id,
            concept_id=concept.id,
            source_id=source.id,
            span_id=span.id,
            citation_label="S1",
            quote_text="Citation evidence.",
        )
    )
    runtime_db.flush()
    return source, concept


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


_ARTICLE_HTML = """<!DOCTYPE html>
<html lang="en">
<head><title>Relay Guide - Pump Site</title><meta name="author" content="Dana Fixit" /></head>
<body>
  <nav><ul><li><a href="/">Home</a></li><li><a href="/shop">Browse products</a></li></ul></nav>
  <article>
    <h1>Relay Guide</h1>
    <p>Relays must be inspected before every checkpoint restart to avoid cascade faults.</p>
    <h2>Inspection Steps</h2>
    <p>Open the relay housing and check the contact pads for pitting or discoloration.</p>
  </article>
  <footer><p>Copyright 2026 Pump Site. Subscribe to our newsletter!</p></footer>
</body>
</html>"""


def test_web_capture_endpoint_accepts_raw_html() -> None:
    with managed_test_client("anima-knowledge-web-html-") as client:
        user_id, headers = _register(client, username="knowledge-web-html")

        response = client.post(
            "/api/knowledge/sources/web-capture",
            headers=headers,
            json={
                "userId": user_id,
                "url": "https://example.com/relay-guide",
                "html": _ARTICLE_HTML,
            },
        )

        assert response.status_code == 201
        payload = response.json()
        assert payload["source"]["kind"] == "web_capture"
        assert payload["source"]["mediaType"] == "text/html"
        assert payload["source"]["title"] == "Relay Guide"
        assert [artifact["artifactKind"] for artifact in payload["artifacts"]] == [
            "raw_html",
            "structured_markdown",
        ]
        section_paths = {
            span["metadata"].get("section_path")
            for span in payload["spans"]
            if span["spanKind"] == "section"
        }
        assert "Relay Guide > Inspection Steps" in section_paths
        combined = "\n".join(span["contentText"] for span in payload["spans"])
        assert "Browse products" not in combined
        assert "newsletter" not in combined


def test_web_capture_endpoint_rejects_ambiguous_input_modes() -> None:
    with managed_test_client("anima-knowledge-web-modes-") as client:
        user_id, headers = _register(client, username="knowledge-web-modes")

        both = client.post(
            "/api/knowledge/sources/web-capture",
            headers=headers,
            json={
                "userId": user_id,
                "url": "https://example.com/page",
                "readableText": "Text.",
                "html": "<html><body><p>Text.</p></body></html>",
            },
        )
        neither = client.post(
            "/api/knowledge/sources/web-capture",
            headers=headers,
            json={
                "userId": user_id,
                "url": "https://example.com/page",
            },
        )

        assert both.status_code == 422
        assert neither.status_code == 422


def test_web_capture_endpoint_fetch_mode_forbidden_when_disabled() -> None:
    with managed_test_client("anima-knowledge-web-fetch-") as client:
        user_id, headers = _register(client, username="knowledge-web-fetch")

        response = client.post(
            "/api/knowledge/sources/web-capture",
            headers=headers,
            json={
                "userId": user_id,
                "url": "https://example.com/page",
                "fetch": True,
            },
        )

        assert response.status_code == 403


def test_html_upload_endpoint_validates_and_ingests() -> None:
    with managed_test_client("anima-knowledge-html-upload-") as client:
        user_id, headers = _register(client, username="knowledge-html-upload")

        response = client.post(
            "/api/knowledge/sources/html",
            headers=headers,
            data={"userId": str(user_id)},
            files={"file": ("saved article.html", _ARTICLE_HTML.encode(), "text/html")},
        )

        assert response.status_code == 201
        payload = response.json()
        assert payload["source"]["kind"] == "html"
        assert payload["source"]["sourceUri"] == "html://saved article.html"
        assert [artifact["artifactKind"] for artifact in payload["artifacts"]] == [
            "raw_html",
            "structured_markdown",
        ]

        rejected = client.post(
            "/api/knowledge/sources/html",
            headers=headers,
            data={"userId": str(user_id)},
            files={"file": ("doc.pdf", b"%PDF-1.7", "application/pdf")},
        )
        assert rejected.status_code == 400


def test_reextract_endpoint_replaces_spans_idempotently() -> None:
    with managed_test_client("anima-knowledge-reextract-") as client:
        user_id, headers = _register(client, username="knowledge-reextract")

        created = client.post(
            "/api/knowledge/sources/web-capture",
            headers=headers,
            json={
                "userId": user_id,
                "url": "https://example.com/relay-guide",
                "html": _ARTICLE_HTML,
            },
        )
        assert created.status_code == 201
        source_id = created.json()["source"]["id"]
        original_span_ids = [span["id"] for span in created.json()["spans"]]

        reextracted = client.post(
            f"/api/knowledge/sources/{source_id}/reextract?userId={user_id}",
            headers=headers,
        )
        assert reextracted.status_code == 200
        assert [span["id"] for span in reextracted.json()["spans"]] == original_span_ids

        text_capture = client.post(
            "/api/knowledge/sources/web-capture",
            headers=headers,
            json={
                "userId": user_id,
                "url": "https://example.com/text-only",
                "readableText": "Pre-extracted capture.",
            },
        )
        assert text_capture.status_code == 201
        no_raw_html = client.post(
            f"/api/knowledge/sources/{text_capture.json()['source']['id']}/reextract?userId={user_id}",
            headers=headers,
        )
        assert no_raw_html.status_code == 422


def test_reingest_with_compile_retries_failed_compile_run() -> None:
    with managed_test_client("anima-knowledge-compile-retry-") as client:
        user_id, headers = _register(client, username="knowledge-compile-retry")

        created = client.post(
            "/api/knowledge/sources/markdown",
            headers=headers,
            json={
                "userId": user_id,
                "filename": "retry.md",
                "title": "Retry",
                "content": "# Retry\n\nBody.",
                "compile": False,
            },
        )
        assert created.status_code == 201
        source_id = created.json()["source"]["id"]

        # Simulate a transient compiler failure recorded for this source.
        with get_runtime_session_factory()() as runtime_db:
            runtime_db.add(
                RuntimeKnowledgeBundleRun(
                    user_id=user_id,
                    run_type="compile:initial",
                    status="failed",
                    source_id=source_id,
                )
            )
            runtime_db.commit()

        retried = client.post(
            "/api/knowledge/sources/markdown",
            headers=headers,
            json={
                "userId": user_id,
                "filename": "retry.md",
                "title": "Retry",
                "content": "# Retry\n\nBody.",
                "compile": True,
            },
        )

        assert retried.status_code == 201
        payload = retried.json()
        # The failed run must not short-circuit the explicit compile request.
        assert payload["compileRun"]["status"] == "completed"

        # A completed run does short-circuit the next request (no duplicate).
        again = client.post(
            "/api/knowledge/sources/markdown",
            headers=headers,
            json={
                "userId": user_id,
                "filename": "retry.md",
                "title": "Retry",
                "content": "# Retry\n\nBody.",
                "compile": True,
            },
        )
        assert again.json()["compileRun"]["id"] == payload["compileRun"]["id"]


def test_reextract_recompiles_when_source_had_concepts() -> None:
    with managed_test_client("anima-knowledge-reextract-compile-") as client:
        user_id, headers = _register(client, username="knowledge-reextract-compile")

        created = client.post(
            "/api/knowledge/sources/web-capture",
            headers=headers,
            json={
                "userId": user_id,
                "url": "https://example.com/relay-guide",
                "html": _ARTICLE_HTML,
                "compile": True,
            },
        )
        assert created.status_code == 201
        source_id = created.json()["source"]["id"]
        assert created.json()["compileRun"]["status"] == "completed"

        reextracted = client.post(
            f"/api/knowledge/sources/{source_id}/reextract?userId={user_id}",
            headers=headers,
        )

        assert reextracted.status_code == 200
        payload = reextracted.json()
        # A compiled source recompiles after re-extraction so concepts and
        # citations track the fresh spans.
        assert payload["compileRun"]["runType"] == "compile:refresh"
        assert payload["compileRun"]["status"] == "completed"

        with get_runtime_session_factory()() as runtime_db:
            citations = list(
                runtime_db.scalars(
                    select(RuntimeKnowledgeConceptSource).where(
                        RuntimeKnowledgeConceptSource.source_id == source_id
                    )
                ).all()
            )
        assert citations


def test_reextract_rolls_back_when_refresh_compile_fails(monkeypatch) -> None:
    from anima_server.api.routes import knowledge as knowledge_routes
    from anima_server.services.ingestion.compiler import CompileResult

    with managed_test_client("anima-knowledge-reextract-rollback-") as client:
        user_id, headers = _register(client, username="knowledge-reextract-rollback")

        created = client.post(
            "/api/knowledge/sources/web-capture",
            headers=headers,
            json={
                "userId": user_id,
                "url": "https://example.com/relay-guide",
                "html": _ARTICLE_HTML,
                "compile": True,
            },
        )
        assert created.status_code == 201
        source_id = created.json()["source"]["id"]

        with get_runtime_session_factory()() as runtime_db:
            citations_before = len(
                list(
                    runtime_db.scalars(
                        select(RuntimeKnowledgeConceptSource).where(
                            RuntimeKnowledgeConceptSource.source_id == source_id
                        )
                    ).all()
                )
            )
        assert citations_before > 0

        async def failing_compile(*args, **kwargs):
            return CompileResult(status="failed", run_id=1)

        monkeypatch.setattr(
            knowledge_routes, "compile_source_knowledge_auto", failing_compile
        )

        response = client.post(
            f"/api/knowledge/sources/{source_id}/reextract?userId={user_id}",
            headers=headers,
        )

        # The re-extraction rolled back: no half-state with concepts but no
        # citations.
        assert response.status_code == 502
        with get_runtime_session_factory()() as runtime_db:
            citations_after = len(
                list(
                    runtime_db.scalars(
                        select(RuntimeKnowledgeConceptSource).where(
                            RuntimeKnowledgeConceptSource.source_id == source_id
                        )
                    ).all()
                )
            )
        assert citations_after == citations_before


def test_web_capture_fetch_rejects_overlong_redirect_url(monkeypatch) -> None:
    from anima_server.api.routes import knowledge as knowledge_routes

    with managed_test_client("anima-knowledge-fetch-longurl-") as client:
        user_id, headers = _register(client, username="knowledge-fetch-longurl")

        def fake_fetch(url: str):
            return "https://example.com/" + "a" * 1200, "<html><body><article><h1>T</h1><p>Body.</p></article></body></html>"

        monkeypatch.setattr(knowledge_routes, "fetch_capture_html", fake_fetch)

        response = client.post(
            "/api/knowledge/sources/web-capture",
            headers=headers,
            json={
                "userId": user_id,
                "url": "https://example.com/start",
                "fetch": True,
            },
        )

        # A redirect landing on an overlong URL is a controlled 422, not a
        # database length error.
        assert response.status_code == 422
        assert "longer than" in response.json()["error"]


def test_knowledge_search_excludes_section_spans() -> None:
    with managed_test_client("anima-knowledge-search-sections-") as client:
        user_id, headers = _register(client, username="knowledge-search-sections")

        created = client.post(
            "/api/knowledge/sources/markdown",
            headers=headers,
            json={
                "userId": user_id,
                "filename": "sections.md",
                "title": "Sections",
                "content": "# Relay Guide\n\nUnique zephyrblade paragraph body.",
                "compile": False,
            },
        )
        assert created.status_code == 201
        assert any(
            span["spanKind"] == "section" for span in created.json()["spans"]
        )

        response = client.get(
            f"/api/knowledge/search?userId={user_id}&q=zephyrblade",
            headers=headers,
        )

        assert response.status_code == 200
        kinds = {span["spanKind"] for span in response.json()["evidenceSpans"]}
        # The paragraph evidence matches; the parent section span (which
        # duplicates the same text) must not appear or displace it.
        assert "paragraph" in kinds
        assert "section" not in kinds


def _retire_one_concept(concept_slug_contains: str) -> int:
    """Retire the first concept whose slug contains *concept_slug_contains*."""
    with get_runtime_session_factory()() as runtime_db:
        concept = next(
            concept
            for concept in runtime_db.scalars(select(RuntimeKnowledgeConcept)).all()
            if concept_slug_contains in concept.slug
        )
        concept.status = "inactive"
        runtime_db.add(concept)
        runtime_db.commit()
        return concept.id


def test_knowledge_search_excludes_retired_concepts() -> None:
    with managed_test_client("anima-knowledge-search-retired-") as client:
        user_id, headers = _register(client, username="knowledge-search-retired")

        created = client.post(
            "/api/knowledge/sources/markdown",
            headers=headers,
            json={
                "userId": user_id,
                "filename": "retired.md",
                "title": "Quorvex Notes",
                "content": "# Quorvex Notes\n\nQuorvex paragraph body.",
                "compile": True,
            },
        )
        assert created.status_code == 201
        retired_id = _retire_one_concept("span")

        response = client.get(
            f"/api/knowledge/search?userId={user_id}&q=quorvex",
            headers=headers,
        )

        assert response.status_code == 200
        # A refresh compile retires superseded pages as "inactive"; retrieval
        # and lint scope to "active", so search must not resurrect them.
        assert retired_id not in {concept["id"] for concept in response.json()["concepts"]}


def test_concepts_listing_excludes_retired_concepts_unless_requested() -> None:
    with managed_test_client("anima-knowledge-concepts-retired-") as client:
        user_id, headers = _register(client, username="knowledge-concepts-retired")

        created = client.post(
            "/api/knowledge/sources/markdown",
            headers=headers,
            json={
                "userId": user_id,
                "filename": "retired-list.md",
                "title": "Zalbrix Notes",
                "content": "# Zalbrix Notes\n\nZalbrix paragraph body.",
                "compile": True,
            },
        )
        assert created.status_code == 201
        retired_id = _retire_one_concept("span")

        listed = client.get(f"/api/knowledge/concepts?userId={user_id}", headers=headers)
        assert listed.status_code == 200
        assert retired_id not in {concept["id"] for concept in listed.json()["concepts"]}

        # Retired pages stay reachable for auditing, but only on request.
        including = client.get(
            f"/api/knowledge/concepts?userId={user_id}&includeRetired=true",
            headers=headers,
        )
        assert including.status_code == 200
        assert retired_id in {concept["id"] for concept in including.json()["concepts"]}
