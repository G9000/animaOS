from __future__ import annotations

import hashlib

import pytest
from anima_server.services.ingestion.adapters import text, web
from corefs_ingestion_test_support import seed_captured_text_projection
from sqlalchemy import func, select

pytest_plugins = ("conftest_runtime",)


@pytest.mark.parametrize(
    "module,names",
    [
        (text, ("ingest_text_content", "ingest_markdown_content", "_ingest_content")),
        (
            web,
            ("ingest_web_capture", "ingest_html_content", "reextract_source_html", "_ingest_html"),
        ),
    ],
)
def test_runtime_primary_capture_ingestors_are_retired(module, names):
    assert not any(hasattr(module, name) for name in names)


def test_simulated_capture_fixture_binds_owner_and_hashes_original_bytes(runtime_db):
    original = "  A private UTF8 source: café.\n\nSecond paragraph.\n"
    one, artifacts, _ = seed_captured_text_projection(
        runtime_db, user_id=1, content=original, filename="owner.txt", compile_knowledge=False
    )
    two, _, _ = seed_captured_text_projection(
        runtime_db, user_id=2, content=original, filename="owner.txt", compile_knowledge=False
    )
    assert one.source_uri.startswith("corefs://object/")
    assert one.source_uri != two.source_uri
    assert (
        one.content_hash == two.content_hash == hashlib.sha256(original.encode("utf-8")).hexdigest()
    )
    assert artifacts[0].content_hash == hashlib.sha256(original.strip().encode("utf-8")).hexdigest()
    assert runtime_db.info["test_only_authored_sources"][
        (1, one.source_uri)
    ].original_bytes == original.encode("utf-8")


def test_current_capture_apis_preserve_native_originals_without_runtime_source_rows(monkeypatch):
    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.models.runtime import RuntimeSource, RuntimeSourceArtifact, RuntimeSourceSpan
    from anima_server.services.corefs import migration
    from anima_server.services.corefs.catalog_reader import (
        read_prepared_writing_body,
        read_prepared_writing_snapshot,
    )
    from anima_server.services.corefs.knowledge_authority import (
        canonical_knowledge_document,
        decode_knowledge_document,
    )
    from anima_server.services.sessions import unlock_session_store
    from conftest import managed_test_client

    monkeypatch.setattr(migration, "schedule_unlocked_rebuild", lambda *_args, **_kwargs: False)
    original_html = "<html><head><title>Native relay capture</title></head><body><article><h1>Relay care</h1><p>Inspect every relay carefully before the checkpoint restart and replace damaged contact pads.</p></article></body></html>"
    with managed_test_client("current-native-capture-") as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": "capture", "password": "current-password", "name": "Capture owner"},
        )
        assert registered.status_code == 201, registered.text
        user_id = registered.json()["id"]
        headers = {"x-anima-unlock": registered.json()["unlockToken"]}
        text_content = "Private current text evidence."
        markdown_content = "# Native markdown\n\nPreserved private original evidence."
        writes = [
            (
                client.post(
                    "/api/knowledge/sources/text",
                    headers=headers,
                    json={"userId": user_id, "content": text_content, "filename": "capture.txt"},
                ),
                text_content,
            ),
            (
                client.post(
                    "/api/knowledge/sources/markdown",
                    headers=headers,
                    json={"userId": user_id, "content": markdown_content, "filename": "capture.md"},
                ),
                markdown_content,
            ),
            (
                client.post(
                    "/api/knowledge/sources/web-capture",
                    headers=headers,
                    json={
                        "userId": user_id,
                        "url": "https://example.test/native-relay",
                        "html": original_html,
                        "fetch": False,
                    },
                ),
                original_html,
            ),
            (
                client.post(
                    "/api/knowledge/sources/html",
                    headers=headers,
                    data={"userId": str(user_id)},
                    files={"file": ("capture.html", original_html.encode("utf-8"), "text/html")},
                ),
                original_html,
            ),
        ]
        session = unlock_session_store.resolve(headers["x-anima-unlock"])
        assert session is not None
        source_ids = []
        for response, original in writes:
            assert response.status_code == 201, response.text
            source_ids.append(response.json()["source"]["id"])
            uri = response.json()["artifacts"][0]["metadata"]["objectUri"]
            assert uri.startswith("corefs://object/")
            item = next(
                item
                for item in read_prepared_writing_snapshot(session=session).objects
                if item.stable_id == uri.rsplit("/", 1)[1]
            )
            body = read_prepared_writing_body(session=session, item=item)
            assert hashlib.sha256(body).hexdigest() == item.content_hash
            document = decode_knowledge_document(body)
            assert document.original_content == original
        assert (
            client.post(
                "/api/knowledge/sources/text",
                headers=headers,
                json={"userId": user_id + 1, "content": "Wrong owner"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/knowledge/sources/text", json={"userId": user_id, "content": "Locked capture"}
            ).status_code
            == 401
        )
        with get_runtime_session_factory()() as db:
            for model in (RuntimeSource, RuntimeSourceArtifact, RuntimeSourceSpan):
                assert db.scalar(select(func.count(model.id)).where(model.user_id == user_id)) == 0
        assert client.post("/api/auth/logout", headers=headers).status_code == 200
        login = client.post(
            "/api/auth/login", json={"username": "capture", "password": "current-password"}
        )
        assert login.status_code == 200, login.text
        reopened = unlock_session_store.resolve(login.json()["unlockToken"])
        for source_id, (_, original) in zip(source_ids, writes, strict=True):
            selected = canonical_knowledge_document(session=reopened, source_id=source_id)
            assert selected is not None
            assert selected[1].original_content == original
