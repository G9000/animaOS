"""TEST ONLY derived-cache producers for compiler and retrieval unit fixtures.

These builders simulate authenticated authored bytes using owner-bound Core URIs.
They do not write an actual Core, assert live cryptographic authentication, or
provide any production ingestion/restore path. Native capture tests cover that.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

from anima_server.models.runtime import RuntimeSource
from anima_server.services.corefs.catalog_reader import stable_opaque_id
from anima_server.services.ingestion.adapters.text import (
    STRUCTURED_MARKDOWN_ARTIFACT_KIND,
    _content_hash,
    _paragraph_spans,
    _sanitize_filename,
    _structured_markdown_spans,
)
from anima_server.services.ingestion.adapters.web import (
    _extraction_metadata,
    _html_artifacts_and_spans,
    _normalize_url,
)
from anima_server.services.ingestion.artifacts import replace_source_artifacts_and_spans
from anima_server.services.ingestion.html_extract import extract_html_article
from anima_server.services.ingestion.models import SourceArtifactInput, SourceIdentity
from anima_server.services.ingestion.sources import register_source
from anima_server.services.ingestion.structured import parse_markdown_structure
from sqlalchemy import select


@dataclass(frozen=True)
class SimulatedAuthoredSource:
    user_id: int
    uri: str
    original_bytes: bytes
    media_type: str

    @property
    def content_hash(self):
        return hashlib.sha256(self.original_bytes).hexdigest()


def simulated_authored_source_uri(*, user_id, kind, origin, original_bytes):
    digest = hashlib.sha256(original_bytes).hexdigest()
    stable_id = stable_opaque_id("test-only-authored-source", f"{user_id}:{kind}:{origin}:{digest}")
    return f"corefs://object/{stable_id}"


def register_simulated_authored_source(
    db, *, user_id, kind, origin, original_bytes, title, media_type, metadata
):
    uri = simulated_authored_source_uri(
        user_id=user_id, kind=kind, origin=origin, original_bytes=original_bytes
    )
    authored = SimulatedAuthoredSource(user_id, uri, original_bytes, media_type)
    db.info.setdefault("test_only_authored_sources", {})[(user_id, uri)] = authored
    session = SimpleNamespace(user_id=user_id, test_only_simulated_authentication=True)

    def require_session(owner):
        assert owner == authored.user_id
        return session

    def open_source(*, session: object, object_uri, **_kwargs):
        assert session.user_id == authored.user_id
        assert object_uri == authored.uri
        return SimpleNamespace(
            content_sha256=authored.content_hash,
            size=len(authored.original_bytes),
            content_type=media_type,
        )

    with (
        patch(
            "anima_server.services.corefs.asset_authority.require_canonical_asset_session",
            require_session,
        ),
        patch("anima_server.services.corefs.asset_authority.open_corefs_byte_source", open_source),
    ):
        return register_source(
            db,
            SourceIdentity(
                user_id=user_id,
                kind=kind,
                source_uri=uri,
                content_hash=authored.content_hash,
                title=title,
                media_type=media_type,
                metadata_json=metadata,
            ),
        )


def seed_captured_text_projection(
    db, *, user_id, content, filename=None, title=None, embedding_fn=None, compile_knowledge=True
):
    return _seed_text_projection(
        db,
        user_id=user_id,
        kind="text",
        artifact_kind="plain_text",
        content=content,
        filename=filename,
        title=title,
        embedding_fn=embedding_fn,
        compile_knowledge=compile_knowledge,
    )


def seed_captured_markdown_projection(
    db, *, user_id, content, filename=None, title=None, embedding_fn=None, compile_knowledge=True
):
    return _seed_text_projection(
        db,
        user_id=user_id,
        kind="markdown",
        artifact_kind="markdown",
        content=content,
        filename=filename,
        title=title,
        embedding_fn=embedding_fn,
        compile_knowledge=compile_knowledge,
    )


def _seed_text_projection(
    db, *, user_id, kind, artifact_kind, content, filename, title, embedding_fn, compile_knowledge
):
    normalized = content.strip()
    if not normalized:
        raise ValueError("content must not be empty")
    name = _sanitize_filename(filename, default=f"{kind}.txt")
    metadata = {"filename": name}
    source = register_simulated_authored_source(
        db,
        user_id=user_id,
        kind=kind,
        origin=name,
        original_bytes=content.encode("utf-8"),
        title=title or name,
        media_type="text/markdown" if kind == "markdown" else "text/plain",
        metadata=metadata,
    )
    artifacts = [
        SourceArtifactInput(
            artifact_kind=artifact_kind,
            content_text=normalized,
            content_hash=_content_hash(normalized),
            metadata_json=metadata,
        )
    ]
    if kind == "markdown":
        document = parse_markdown_structure(normalized)
        canonical = document.to_markdown()
        artifacts.append(
            SourceArtifactInput(
                artifact_kind=STRUCTURED_MARKDOWN_ARTIFACT_KIND,
                content_text=canonical,
                content_hash=_content_hash(canonical),
                metadata_json={**metadata, "outline": document.outline()},
            )
        )
        spans = _structured_markdown_spans(artifact_kind, document)
    else:
        spans = _paragraph_spans(artifact_kind, normalized)
    return source, *replace_source_artifacts_and_spans(
        db,
        source=source,
        artifacts=artifacts,
        spans=spans,
        embedding_fn=embedding_fn,
        compile_knowledge=compile_knowledge,
    )


def seed_captured_web_projection(
    db,
    *,
    user_id,
    url,
    readable_text=None,
    html=None,
    title=None,
    canonical_url=None,
    embedding_fn=None,
    compile_knowledge=True,
):
    if (readable_text is None) == (html is None):
        raise ValueError("provide exactly one of readable_text or html")
    url = _normalize_url(url)
    canonical = _normalize_url(canonical_url) if canonical_url else None
    if html is not None:
        return _seed_html_projection(
            db,
            user_id=user_id,
            kind="web_capture",
            origin=url,
            html=html,
            title=title,
            canonical_url=canonical,
            embedding_fn=embedding_fn,
            compile_knowledge=compile_knowledge,
        )
    normalized = readable_text.strip()
    if not normalized:
        raise ValueError("content must not be empty")
    metadata = {"url": url, "canonical_url": canonical}
    source = register_simulated_authored_source(
        db,
        user_id=user_id,
        kind="web_capture",
        origin=url,
        original_bytes=readable_text.encode("utf-8"),
        title=title,
        media_type="text/plain",
        metadata=metadata,
    )
    artifacts = [
        SourceArtifactInput(
            artifact_kind="readable_text",
            content_text=normalized,
            content_hash=_content_hash(normalized),
            metadata_json=metadata,
        )
    ]
    return source, *replace_source_artifacts_and_spans(
        db,
        source=source,
        artifacts=artifacts,
        spans=_paragraph_spans("readable_text", normalized),
        embedding_fn=embedding_fn,
        compile_knowledge=compile_knowledge,
    )


def seed_captured_html_projection(
    db, *, user_id, html, filename=None, title=None, embedding_fn=None, compile_knowledge=True
):
    name = _sanitize_filename(filename, default="page.html")
    return _seed_html_projection(
        db,
        user_id=user_id,
        kind="html",
        origin=name,
        html=html,
        title=title,
        canonical_url=None,
        embedding_fn=embedding_fn,
        compile_knowledge=compile_knowledge,
        extra_metadata={"filename": name},
    )


def _seed_html_projection(
    db,
    *,
    user_id,
    kind,
    origin,
    html,
    title,
    canonical_url,
    embedding_fn,
    compile_knowledge,
    extra_metadata=None,
):
    normalized = html.strip()
    if not normalized:
        raise ValueError("content must not be empty")
    url = origin if kind == "web_capture" else None
    extraction = extract_html_article(normalized, url=url)
    base = dict(extra_metadata or {})
    if kind == "web_capture":
        base.update(url=origin, canonical_url=canonical_url or extraction.canonical_url)
    metadata = _extraction_metadata(base, extraction)
    artifacts, spans = _html_artifacts_and_spans(
        normalized, extraction=extraction, metadata=metadata
    )
    source = register_simulated_authored_source(
        db,
        user_id=user_id,
        kind=kind,
        origin=origin,
        original_bytes=html.encode("utf-8"),
        title=title or extraction.title,
        media_type="text/html",
        metadata=metadata,
    )
    return source, *replace_source_artifacts_and_spans(
        db,
        source=source,
        artifacts=artifacts,
        spans=spans,
        embedding_fn=embedding_fn,
        compile_knowledge=compile_knowledge,
    )


def reextract_simulated_html_projection(
    db, *, user_id, source_id, embedding_fn=None, compile_knowledge=False
):
    source = db.scalar(
        select(RuntimeSource).where(RuntimeSource.id == source_id, RuntimeSource.user_id == user_id)
    )
    if source is None:
        raise LookupError("Source not found.")
    authored = db.info.get("test_only_authored_sources", {}).get((user_id, source.source_uri))
    if authored is None or authored.media_type != "text/html":
        raise ValueError("Source has no simulated original raw HTML bytes.")
    if source.content_hash != authored.content_hash:
        raise ValueError("Simulated authored byte identity changed")
    html = authored.original_bytes.decode("utf-8").strip()
    extraction = extract_html_article(html, url=(source.metadata_json or {}).get("url"))
    metadata = _extraction_metadata(dict(source.metadata_json or {}), extraction)
    artifacts, spans = _html_artifacts_and_spans(html, extraction=extraction, metadata=metadata)
    return source, *replace_source_artifacts_and_spans(
        db,
        source=source,
        artifacts=artifacts,
        spans=spans,
        embedding_fn=embedding_fn,
        compile_knowledge=compile_knowledge,
    )
