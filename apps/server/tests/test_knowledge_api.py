from __future__ import annotations

from anima_server.services.corefs.diary_migration import (
    read_prepared_writing_body,
    read_prepared_writing_snapshot,
)
from anima_server.services.corefs.knowledge_authority import decode_knowledge_document
from anima_server.services.sessions import unlock_session_store
from conftest import managed_test_client


def _register(client, *, username: str) -> tuple[int, dict[str, str]]:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pw123456", "name": username},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    return int(payload["id"]), {"x-anima-unlock": str(payload["unlockToken"])}


def test_greenfield_knowledge_sources_are_corefs_authoritative(monkeypatch) -> None:
    from anima_server.services.corefs import migration as corefs_migration

    monkeypatch.setattr(
        corefs_migration,
        "schedule_unlocked_rebuild",
        lambda *_args, **_kwargs: False,
    )
    with managed_test_client("anima-knowledge-greenfield-") as client:
        user_id, headers = _register(client, username="knowledge-greenfield")
        session = unlock_session_store.resolve(headers["x-anima-unlock"])
        assert session is not None
        assert session.content_authority is not None
        assert session.content_authority["state"] == "authoritative"

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
        assert payload["source"]["sourceUri"] == "markdown://portable.md"
        assert payload["compileRun"]["runType"] == "compile:derived"
        assert [span["spanKind"] for span in payload["spans"]] == ["structured_markdown"]

        source_id = int(payload["source"]["id"])
        object_uri = payload["artifacts"][0]["metadata"]["objectUri"]
        stable_id = object_uri.rsplit("/", 1)[-1]
        item = next(
            candidate
            for candidate in read_prepared_writing_snapshot(session=session).objects
            if candidate.stable_id == stable_id
        )
        document = decode_knowledge_document(read_prepared_writing_body(session=session, item=item))
        assert document.original_content == markdown
        assert "zephyrblade" in document.content

        listed = client.get(f"/api/knowledge/sources?userId={user_id}", headers=headers)
        assert listed.status_code == 200, listed.text
        assert [source["id"] for source in listed.json()["sources"]] == [source_id]

        searched = client.get(
            f"/api/knowledge/search?userId={user_id}&q=zephyrblade", headers=headers
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
        assert "Offline re-extraction source" in reextracted.json()["artifacts"][0]["contentText"]


def test_knowledge_source_validation_rejects_empty_and_ambiguous_input() -> None:
    with managed_test_client("anima-knowledge-validation-") as client:
        user_id, headers = _register(client, username="knowledge-validation")
        empty = client.post(
            "/api/knowledge/sources/text",
            headers=headers,
            json={
                "userId": user_id,
                "filename": "empty.txt",
                "title": "Empty",
                "content": "   ",
            },
        )
        assert empty.status_code == 422

        ambiguous = client.post(
            "/api/knowledge/sources/web-capture",
            headers=headers,
            json={
                "userId": user_id,
                "url": "https://example.test/ambiguous",
                "html": "<p>raw</p>",
                "readableText": "also supplied",
                "fetch": False,
            },
        )
        assert ambiguous.status_code == 422


def test_knowledge_endpoints_require_matching_unlock_session() -> None:
    with managed_test_client("anima-knowledge-auth-") as client:
        user_id, _headers = _register(client, username="knowledge-auth")
        assert client.get(f"/api/knowledge/sources?userId={user_id}").status_code == 401
        assert (
            client.get(
                f"/api/knowledge/sources?userId={user_id + 1}",
                headers={"x-anima-unlock": "invalid"},
            ).status_code
            == 401
        )
