from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

import pytest
from anima_server.api.routes import knowledge as knowledge_route
from anima_server.services.corefs.asset_migration import (
    PortableKnowledgeSource,
    build_portable_asset_shadow,
)
from anima_server.services.corefs.indexer import (
    CoreFSKnowledgeSourceProjection,
    CoreFSProgressiveIndex,
)


def test_web_capture_preserves_raw_html_and_exact_normalized_snapshot() -> None:
    raw_html = b"<html><body><h1>Private source</h1></body></html>"
    normalized = b"# Private source\n"
    shadow = build_portable_asset_shadow(
        user_id=7,
        knowledge_sources=[
            PortableKnowledgeSource(
                legacy_id="41:raw_html",
                name="capture.raw.html",
                content_type="text/html; charset=utf-8",
                data=raw_html,
                created_at="2026-08-13T00:00:00.000000+00:00",
                updated_at="2026-08-13T00:00:00.000000+00:00",
                metadata={
                    "sourceUri": "https://example.test/private",
                    "artifactKind": "raw_html",
                    "extractorVersion": "html-v1",
                },
            ),
            PortableKnowledgeSource(
                legacy_id="41:normalized_markdown",
                name="capture.normalized.md",
                content_type="text/markdown; charset=utf-8",
                data=normalized,
                created_at="2026-08-13T00:00:00.000000+00:00",
                updated_at="2026-08-13T00:00:00.000000+00:00",
                metadata={
                    "sourceUri": "https://example.test/private",
                    "artifactKind": "normalized_markdown",
                    "extractorVersion": "html-v1",
                },
            ),
        ],
    )

    sources = [item for item in shadow.objects if item.descriptor.kind == "knowledge-source"]
    assert len(sources) == 2
    by_kind = {item.descriptor.metadata["artifactKind"]: item for item in sources}
    assert by_kind["raw_html"].body == raw_html
    assert by_kind["normalized_markdown"].body == normalized
    assert by_kind["raw_html"].descriptor.content_sha256 == hashlib.sha256(
        raw_html
    ).hexdigest()
    assert by_kind["normalized_markdown"].descriptor.content_sha256 == hashlib.sha256(
        normalized
    ).hexdigest()
    assert not any(
        item.descriptor.kind in {"document-chunk", "source-span", "knowledge-concept"}
        for item in shadow.objects
    )


def test_source_identity_conflict_fails_closed() -> None:
    first = PortableKnowledgeSource(
        legacy_id="same",
        name="one.txt",
        content_type="text/plain; charset=utf-8",
        data=b"one",
        created_at="2026-08-13T00:00:00.000000+00:00",
        updated_at="2026-08-13T00:00:00.000000+00:00",
    )
    second = PortableKnowledgeSource(
        legacy_id="same",
        name="two.txt",
        content_type="text/plain; charset=utf-8",
        data=b"two",
        created_at="2026-08-13T00:00:00.000000+00:00",
        updated_at="2026-08-13T00:00:00.000000+00:00",
    )

    try:
        build_portable_asset_shadow(user_id=7, knowledge_sources=[first, second])
    except ValueError as exc:
        assert "conflicting" in str(exc).lower()
    else:
        raise AssertionError("conflicting knowledge source identity was silently selected")


@pytest.mark.asyncio
async def test_runtime_deleted_knowledge_search_uses_unlock_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = CoreFSProgressiveIndex("knowledge-core")
    index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
    index.replace_knowledge_source_projection(
        CoreFSKnowledgeSourceProjection(
            stable_id="01K2B00V8H0E4W4KCNCM3TWB1Q",
            source_id=41,
            artifact_id=42,
            artifact_kind="structured_markdown",
            source_kind="web_capture",
            source_uri="https://example.test/private",
            source_title="Private source",
            source_media_type="text/html",
            filename="capture.normalized.md",
            content_text="# Private source\n\nOffline relay procedure.",
            content_sha256="a" * 64,
        )
    )

    async def unlocked(_request: object, _user_id: int) -> None:
        return None

    monkeypatch.setattr(knowledge_route, "require_unlocked_user_async", unlocked)
    monkeypatch.setattr(knowledge_route, "_active_knowledge_index", lambda _user_id: index)
    runtime_db = MagicMock()

    result = await knowledge_route.search_knowledge(
        request=MagicMock(),
        userId=7,
        q="offline relay",
        limit=20,
        runtime_db=runtime_db,
    )

    assert result["concepts"] == [
        {
            "id": 41,
            "slug": "corefs-source-41",
            "title": "Private source",
            "description": "# Private source Offline relay procedure.",
            "conceptType": "source_summary",
            "status": "active",
            "metadata": {"authority": "corefs", "derived": True},
        }
    ]
    assert result["evidenceSpans"] == [
        {
            "id": 42,
            "sourceId": 41,
            "sourceTitle": "Private source",
            "sourceUri": "https://example.test/private",
            "spanKind": "structured_markdown",
            "locator": {"corefsObjectId": "01K2B00V8H0E4W4KCNCM3TWB1Q"},
            "contentText": "# Private source\n\nOffline relay procedure.",
            "metadata": {"authority": "corefs"},
        }
    ]
    runtime_db.execute.assert_not_called()
    runtime_db.scalars.assert_not_called()
