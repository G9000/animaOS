from __future__ import annotations

from anima_server.db.runtime import get_runtime_session_factory
from anima_server.models.runtime import RuntimeKnowledgeBundleRun, RuntimeSource
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
        assert [span["spanKind"] for span in payload["spans"]] == ["heading", "paragraph"]
        assert payload["compileRun"]["status"] == "completed"

        with get_runtime_session_factory()() as runtime_db:
            run = runtime_db.get(RuntimeKnowledgeBundleRun, payload["compileRun"]["id"])
            assert run is not None
            assert run.run_type == "compiler:queued"
            assert run.source_id == payload["source"]["id"]


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


def test_text_source_endpoint_rejects_empty_content() -> None:
    with managed_test_client("anima-knowledge-empty-") as client:
        user_id, headers = _register(client, username="knowledge-empty")

        response = client.post(
            "/api/knowledge/sources/text",
            headers=headers,
            json={"userId": user_id, "filename": "empty.txt", "content": "  \n\t"},
        )

        assert response.status_code == 422
