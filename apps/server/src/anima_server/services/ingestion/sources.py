from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models.runtime import RuntimeKnowledgeBundleRun, RuntimeSource
from anima_server.services.corefs.sealed_runtime import (
    runtime_private_exact_lookup_value,
    seal_runtime_fields,
)
from anima_server.services.ingestion.adapters.base import IngestionAdapter
from anima_server.services.ingestion.artifacts import replace_source_artifacts_and_spans
from anima_server.services.ingestion.models import SourceIdentity
from anima_server.services.ingestion.retrieval import EmbeddingFn


def register_source(db: Session, identity: SourceIdentity) -> RuntimeSource:
    from anima_server.services.corefs.asset_authority import (
        require_legacy_asset_mutation_allowed,
    )

    require_legacy_asset_mutation_allowed(identity.user_id)
    source_uri = runtime_private_exact_lookup_value(
        db,
        owner_id=identity.user_id,
        value=identity.source_uri,
        namespace="runtime_source.source_uri",
    )
    existing = db.scalar(
        select(RuntimeSource).where(
            RuntimeSource.user_id == identity.user_id,
            RuntimeSource.kind == identity.kind,
            RuntimeSource.source_uri == source_uri,
            RuntimeSource.content_hash == identity.content_hash,
        )
    )
    if existing is not None:
        return existing

    source = RuntimeSource(
        user_id=identity.user_id,
        kind=identity.kind,
        source_uri=source_uri,
        content_hash=identity.content_hash,
        title=None,
        media_type=None,
        status="registered",
        metadata_json=None,
    )
    seal_runtime_fields(
        db,
        row=source,
        row_type="runtime_source",
        owner_id=identity.user_id,
        payload={
            "source_uri": identity.source_uri,
            "title": identity.title,
            "media_type": identity.media_type,
            "metadata_json": _copy_metadata(identity.metadata_json),
        },
        placeholders={
            "source_uri": source_uri,
            "title": None,
            "media_type": None,
            "metadata_json": None,
        },
    )
    return source


def set_source_status(
    db: Session,
    *,
    source: RuntimeSource,
    status: str,
    indexed: bool = False,
) -> RuntimeSource:
    now = datetime.now(UTC)
    source.status = status
    source.updated_at = now
    if indexed:
        source.indexed_at = now
    db.add(source)
    db.flush()
    return source


def start_bundle_run(
    db: Session,
    *,
    user_id: int,
    run_type: str,
    source_id: int | None = None,
    input_json: dict[str, object] | None = None,
) -> RuntimeKnowledgeBundleRun:
    now = datetime.now(UTC)
    run = RuntimeKnowledgeBundleRun(
        user_id=user_id,
        run_type=run_type,
        status="running",
        source_id=source_id,
        input_json=None,
        result_json=None,
        error_json=None,
        started_at=now,
    )
    seal_runtime_fields(
        db,
        row=run,
        row_type="runtime_knowledge_bundle_run",
        owner_id=user_id,
        payload={
            "input_json": _copy_metadata(input_json),
            "result_json": None,
            "error_json": None,
        },
        placeholders={
            "input_json": None,
            "result_json": None,
            "error_json": None,
        },
    )
    return run


def complete_bundle_run(
    db: Session,
    *,
    run: RuntimeKnowledgeBundleRun,
    result_json: dict[str, object] | None = None,
) -> RuntimeKnowledgeBundleRun:
    run.status = "completed"
    run.completed_at = datetime.now(UTC)
    seal_runtime_fields(
        db,
        row=run,
        row_type="runtime_knowledge_bundle_run",
        owner_id=run.user_id,
        payload={
            "input_json": run.input_json,
            "result_json": _copy_metadata(result_json),
            "error_json": run.error_json,
        },
        placeholders={
            "input_json": None,
            "result_json": None,
            "error_json": None,
        },
    )
    return run


def fail_bundle_run(
    db: Session,
    *,
    run: RuntimeKnowledgeBundleRun,
    exc: Exception,
) -> RuntimeKnowledgeBundleRun:
    run.status = "failed"
    run.completed_at = datetime.now(UTC)
    seal_runtime_fields(
        db,
        row=run,
        row_type="runtime_knowledge_bundle_run",
        owner_id=run.user_id,
        payload={
            "input_json": run.input_json,
            "result_json": run.result_json,
            "error_json": {
                "message": str(exc),
                "type": type(exc).__name__,
            },
        },
        placeholders={
            "input_json": None,
            "result_json": None,
            "error_json": None,
        },
    )
    return run


def ingest_with_adapter(
    db: Session,
    *,
    adapter: IngestionAdapter,
    identity: SourceIdentity,
    embedding_fn: EmbeddingFn | None = None,
) -> tuple[RuntimeSource, RuntimeKnowledgeBundleRun]:
    source = register_source(db, identity)
    set_source_status(db, source=source, status="extracting")
    run = start_bundle_run(
        db,
        user_id=identity.user_id,
        run_type=f"adapter:{adapter.name}",
        source_id=source.id,
        input_json={"source_uri": identity.source_uri, "kind": identity.kind},
    )

    try:
        result = adapter.extract(identity)
        artifacts, spans = replace_source_artifacts_and_spans(
            db,
            source=source,
            artifacts=result.artifacts,
            spans=result.spans,
            embedding_fn=embedding_fn,
            compile_knowledge=True,
        )
    except Exception as exc:
        set_source_status(db, source=source, status="failed")
        fail_bundle_run(db, run=run, exc=exc)
        return source, run

    result_json: dict[str, object] = {
        "artifacts": len(artifacts),
        "spans": len(spans),
        "adapter": adapter.name,
    }
    complete_bundle_run(db, run=run, result_json=result_json)
    return source, run


def _copy_metadata(
    metadata_json: dict[str, object] | None,
) -> dict[str, object] | None:
    if metadata_json is None:
        return None
    return dict(metadata_json)
