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
