from __future__ import annotations

import sqlite3
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace

import pytest
from anima_server.config import settings
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models.corefs_runtime import CoreFSMigrationJournal
from anima_server.services.core import ensure_core_manifest, get_core_id, set_owner_user_id
from anima_server.services.corefs import asset_migration, soul_relocation
from anima_server.services.corefs.cutover import CutoverState, read_cutover_record
from anima_server.services.corefs.orchestration import (
    MigrationConversionResult,
    MigrationCoordinator,
    MigrationOrchestrationError,
    MigrationState,
    run_portable_content_migration,
)
from anima_server.services.corefs.soul_relocation import active_soul_database_path
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

_CATALOG_HASH = "a" * 64
_SOURCE_HASH = "b" * 64


class _Crash(BaseException):
    pass


class _NativeSession:
    def authoritative_cutover_v1(self, _keys: object):
        return None


@pytest.fixture()
def runtime_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Session, None, None]:
    monkeypatch.setattr(settings, "data_dir", tmp_path / ".anima")
    ensure_core_manifest()
    engine = create_engine("sqlite://")
    RuntimeBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        yield db
    engine.dispose()


def _coordinator(runtime_db: Session) -> MigrationCoordinator:
    return MigrationCoordinator(
        runtime_db,
        core_id=get_core_id(),
        local_instance_id="local-instance",
        legacy_user_id=7,
    )


def _result() -> MigrationConversionResult:
    return MigrationConversionResult(
        generation=9,
        catalog_hash=_CATALOG_HASH,
        source_hash=_SOURCE_HASH,
        migrated_count=41,
    )


def _run(coordinator: MigrationCoordinator, **kwargs):
    return coordinator.run(
        preflight=lambda: None,
        converter=_result,
        verifier=lambda result: None,
        **kwargs,
    )


def test_orchestration_persists_verified_acceptance_without_private_runtime_data(
    runtime_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(soul_relocation, "active_soul_database_path", lambda _user_id: Path("soul.db"))
    coordinator = _coordinator(runtime_db)
    status = _run(coordinator)

    assert status.state is MigrationState.AWAITING_ACCEPTANCE
    assert status.generation == 9
    assert status.catalog_hash == _CATALOG_HASH
    assert status.source_hash is None
    assert status.migrated_count == 41
    assert read_cutover_record().state is CutoverState.CORE_FS_VALIDATION_READONLY

    journal = runtime_db.scalar(select(CoreFSMigrationJournal))
    assert journal is not None
    assert journal.status == "awaiting_acceptance"
    assert journal.source_checksum is None
    assert journal.target_checksum is None
    assert journal.error_code is None
    assert journal.error_digest is None
    assert "retained memory" not in repr(journal.__dict__)

    # Older local builds wrote content-derived digests into these nullable
    # Runtime fields. Any resumed coordinator scrubs them before returning.
    journal.source_checksum = _SOURCE_HASH
    journal.target_checksum = _CATALOG_HASH
    runtime_db.commit()
    resumed = coordinator.status()
    assert resumed is not None
    assert resumed.catalog_hash == _CATALOG_HASH
    assert resumed.source_hash is None
    assert journal.source_checksum is None
    assert journal.target_checksum is None

    accepted = coordinator.accept()
    assert accepted.state is MigrationState.ACCEPTED
    assert read_cutover_record().state is CutoverState.CORE_FS_APPROVED_PENDING_FIRST_WRITE
    assert _run(coordinator).state is MigrationState.ACCEPTED


@pytest.mark.parametrize(
    "boundary,expected_state",
    [
        ("migration:after_preflight", MigrationState.PREFLIGHT),
        ("migration:after_freeze", MigrationState.FROZEN),
        ("migration:after_converting_checkpoint", MigrationState.CONVERTING),
        ("migration:after_converter", MigrationState.VERIFYING),
        ("migration:after_verification", MigrationState.VERIFYING),
        ("migration:after_awaiting_acceptance", MigrationState.AWAITING_ACCEPTANCE),
    ],
)
def test_every_migration_checkpoint_resumes_idempotently(
    runtime_db: Session,
    boundary: str,
    expected_state: MigrationState,
) -> None:
    coordinator = _coordinator(runtime_db)

    def crash(name: str) -> None:
        if name == boundary:
            raise _Crash

    with pytest.raises(_Crash):
        _run(coordinator, boundary_hook=crash)
    assert coordinator.status() is not None
    assert coordinator.status().state is expected_state  # type: ignore[union-attr]

    resumed = _run(coordinator)
    assert resumed.state is MigrationState.AWAITING_ACCEPTANCE
    assert resumed.generation == 9
    assert read_cutover_record().state is CutoverState.CORE_FS_VALIDATION_READONLY


def test_failed_migration_records_only_error_class_and_requires_explicit_retry(
    runtime_db: Session,
) -> None:
    coordinator = _coordinator(runtime_db)
    secret = "private diary sentence must not persist"

    def fail() -> MigrationConversionResult:
        raise ValueError(secret)

    with pytest.raises(ValueError, match="private diary"):
        coordinator.run(
            preflight=lambda: None,
            converter=fail,
            verifier=lambda _result: None,
        )
    journal = runtime_db.scalar(select(CoreFSMigrationJournal))
    assert journal is not None
    assert journal.status == "failed"
    assert journal.error_code == "ValueError"
    assert journal.error_digest is not None
    assert secret not in repr(journal.__dict__)

    with pytest.raises(MigrationOrchestrationError, match="explicit retry"):
        _run(coordinator)
    assert _run(coordinator, retry_failed=True).state is MigrationState.AWAITING_ACCEPTANCE


def test_accept_and_reject_recover_crash_after_manifest_transition(
    runtime_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(soul_relocation, "active_soul_database_path", lambda _user_id: Path("soul.db"))
    coordinator = _coordinator(runtime_db)
    _run(coordinator)

    def crash_accept(name: str) -> None:
        if name == "migration:after_cutover_approval":
            raise _Crash

    with pytest.raises(_Crash):
        coordinator.accept(boundary_hook=crash_accept)
    assert read_cutover_record().state is CutoverState.CORE_FS_APPROVED_PENDING_FIRST_WRITE
    assert coordinator.status().state is MigrationState.AWAITING_ACCEPTANCE  # type: ignore[union-attr]
    assert coordinator.accept().state is MigrationState.ACCEPTED


def test_acceptance_fails_closed_without_verified_soul_relocation(
    runtime_db: Session,
) -> None:
    coordinator = _coordinator(runtime_db)
    _run(coordinator)

    with pytest.raises(MigrationOrchestrationError, match="active Soul relocation"):
        coordinator.accept()

    assert read_cutover_record().state is CutoverState.CORE_FS_VALIDATION_READONLY
    assert coordinator.status().state is MigrationState.AWAITING_ACCEPTANCE  # type: ignore[union-attr]


def test_rejection_is_reversible_and_replayable(
    runtime_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        soul_relocation,
        "rollback_owner_soul_database",
        lambda _user_id: False,
    )
    coordinator = _coordinator(runtime_db)
    _run(coordinator)

    def crash_reject(name: str) -> None:
        if name == "migration:after_cutover_rejection":
            raise _Crash

    with pytest.raises(_Crash):
        coordinator.reject(
            corefs_session=_NativeSession(),
            keys=object(),
            boundary_hook=crash_reject,
        )
    assert read_cutover_record().state is CutoverState.LEGACY_AUTHORITATIVE
    assert coordinator.status().state is MigrationState.AWAITING_ACCEPTANCE  # type: ignore[union-attr]
    rejected = coordinator.reject(corefs_session=_NativeSession(), keys=object())
    assert rejected.state is MigrationState.REJECTED


@pytest.mark.parametrize(
    "result",
    [
        MigrationConversionResult(0, _CATALOG_HASH, _SOURCE_HASH, 1),
        MigrationConversionResult(1, "bad", _SOURCE_HASH, 1),
        MigrationConversionResult(1, _CATALOG_HASH, "bad", 1),
        MigrationConversionResult(1, _CATALOG_HASH, _SOURCE_HASH, -1),
    ],
)
def test_invalid_converter_result_never_publishes_validation(
    runtime_db: Session,
    result: MigrationConversionResult,
) -> None:
    coordinator = _coordinator(runtime_db)
    with pytest.raises(MigrationOrchestrationError, match="converter result"):
        coordinator.run(
            preflight=lambda: None,
            converter=lambda: result,
            verifier=lambda _result: None,
        )
    assert read_cutover_record().state is CutoverState.MIGRATING_WRITE_FROZEN


def test_production_orchestration_relocates_and_rejects_soul_atomically(
    runtime_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = 0
    set_owner_user_id(user_id)
    legacy = settings.data_dir / "users" / str(user_id) / "anima.db"
    legacy.parent.mkdir(parents=True)
    connection = sqlite3.connect(legacy)
    try:
        connection.execute("CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO alembic_version VALUES ('head')")
        connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
        connection.execute("INSERT INTO users VALUES (?, 'owner')", (user_id,))
        connection.commit()
    finally:
        connection.close()

    class NativeSession:
        def validation_snapshot(self, _keys: object) -> dict[str, object]:
            return {"generation": 9, "catalogHash": _CATALOG_HASH}

        def authoritative_cutover_v1(self, _keys: object):
            return None

    session = SimpleNamespace(
        user_id=user_id,
        corefs_session=NativeSession(),
        corefs_keys=object(),
        runtime_index=SimpleNamespace(
            core_id=get_core_id(),
            local_instance_id="local-instance",
        ),
    )
    converted = SimpleNamespace(
        generation=9,
        catalog_hash=_CATALOG_HASH,
        source_hash=_SOURCE_HASH,
        source_counts={"account": 1},
    )
    monkeypatch.setattr(
        asset_migration,
        "prepare_portable_content_validation_catalog",
        lambda **_kwargs: (converted, object(), object()),
    )
    transcripts = settings.data_dir / "transcripts"
    transcripts.mkdir()
    soul_engine = create_engine(f"sqlite:///{legacy.as_posix()}")
    soul_factory = sessionmaker(bind=soul_engine)
    try:
        with soul_factory() as soul_db:
            status = run_portable_content_migration(
                session=session,
                soul_db=soul_db,
                runtime_db=runtime_db,
                transcripts_dir=transcripts,
            )
        assert status.state is MigrationState.AWAITING_ACCEPTANCE
        assert active_soul_database_path(user_id) == (settings.data_dir / "soul/soul.db")

        coordinator = MigrationCoordinator(
            runtime_db,
            core_id=get_core_id(),
            local_instance_id="local-instance",
            legacy_user_id=user_id,
        )
        rejected = coordinator.reject(
            corefs_session=session.corefs_session,
            keys=session.corefs_keys,
        )
        assert rejected.state is MigrationState.REJECTED
        assert active_soul_database_path(user_id) is None
        assert read_cutover_record().state is CutoverState.LEGACY_AUTHORITATIVE
    finally:
        soul_engine.dispose()
