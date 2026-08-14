from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models.corefs_runtime import CoreFSMigrationJournal
from anima_server.services.core import get_core_id
from anima_server.services.corefs.cutover import (
    CutoverState,
    approve_validation_cutover,
    begin_migration,
    publish_validation_readonly,
    read_cutover_record,
    rollback_cutover,
)

CONVERTER_ID = "pcf008-cutover-orchestrator-v1"
_ERROR_DIGEST_DOMAIN = b"anima-pcf008-migration-error-v1\x00"
_SOURCE_ID_DOMAIN = b"anima-pcf008-migration-source-v1\x00"


class MigrationOrchestrationError(RuntimeError):
    pass


class MigrationState(StrEnum):
    PREFLIGHT = "preflight"
    FROZEN = "frozen"
    CONVERTING = "converting"
    VERIFYING = "verifying"
    AWAITING_ACCEPTANCE = "awaiting_acceptance"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class MigrationConversionResult:
    generation: int
    catalog_hash: str
    source_hash: str
    migrated_count: int


@dataclass(frozen=True, slots=True)
class MigrationStatus:
    state: MigrationState
    generation: int | None
    catalog_hash: str | None
    source_hash: str | None
    migrated_count: int
    error_code: str | None


Preflight = Callable[[], None]
Converter = Callable[[], MigrationConversionResult]
Verifier = Callable[[MigrationConversionResult], None]
BoundaryHook = Callable[[str], None]


class MigrationCoordinator:
    def __init__(
        self,
        runtime_db: Session,
        *,
        core_id: str,
        local_instance_id: str,
        legacy_user_id: int,
    ) -> None:
        if not core_id or not local_instance_id or legacy_user_id < 0:
            raise MigrationOrchestrationError("migration coordinator identity is invalid")
        self.runtime_db = runtime_db
        self.core_id = core_id
        self.local_instance_id = local_instance_id
        self.legacy_user_id = legacy_user_id
        self.source_id_hash = hashlib.sha256(
            _SOURCE_ID_DOMAIN
            + core_id.encode("utf-8")
            + b"\x00"
            + local_instance_id.encode("utf-8")
            + b"\x00"
            + str(legacy_user_id).encode("ascii")
        ).hexdigest()

    def status(self) -> MigrationStatus | None:
        journal = self._journal()
        return self._status(journal) if journal is not None else None

    def run(
        self,
        *,
        preflight: Preflight,
        converter: Converter,
        verifier: Verifier,
        retry_failed: bool = False,
        boundary_hook: BoundaryHook | None = None,
    ) -> MigrationStatus:
        journal = self._journal()
        if journal is None:
            journal = CoreFSMigrationJournal(
                core_id=self.core_id,
                local_instance_id=self.local_instance_id,
                converter_id=CONVERTER_ID,
                source_id_hash=self.source_id_hash,
                migrated_count=0,
                status=MigrationState.PREFLIGHT.value,
            )
            self.runtime_db.add(journal)
            self.runtime_db.commit()
        state = _parse_state(journal.status)
        if state in {MigrationState.ACCEPTED, MigrationState.REJECTED}:
            return self._status(journal)
        if state is MigrationState.AWAITING_ACCEPTANCE:
            return self._status(journal)
        if state is MigrationState.FAILED:
            if not retry_failed:
                raise MigrationOrchestrationError(
                    "failed migration requires an explicit retry request"
                )
            self._advance(journal, MigrationState.PREFLIGHT)

        try:
            preflight()
            self._advance(journal, MigrationState.PREFLIGHT)
            _boundary(boundary_hook, "migration:after_preflight")

            cutover = read_cutover_record()
            if cutover.state is CutoverState.LEGACY_AUTHORITATIVE:
                begin_migration()
            elif cutover.state is not CutoverState.MIGRATING_WRITE_FROZEN:
                raise MigrationOrchestrationError(
                    "migration cannot resume from the current cutover state"
                )
            self._advance(journal, MigrationState.FROZEN)
            _boundary(boundary_hook, "migration:after_freeze")

            self._advance(journal, MigrationState.CONVERTING)
            _boundary(boundary_hook, "migration:after_converting_checkpoint")
            result = converter()
            _validate_conversion(result)
            self._advance(
                journal,
                MigrationState.VERIFYING,
                migrated_count=result.migrated_count,
            )
            _boundary(boundary_hook, "migration:after_converter")

            verifier(result)
            _boundary(boundary_hook, "migration:after_verification")
            publish_validation_readonly(
                generation=result.generation,
                catalog_hash=result.catalog_hash,
            )
            self._advance(
                journal,
                MigrationState.AWAITING_ACCEPTANCE,
                migrated_count=result.migrated_count,
            )
            _boundary(boundary_hook, "migration:after_awaiting_acceptance")
            return self._status(journal)
        except Exception as exc:
            journal.error_code = type(exc).__name__[:64]
            journal.error_digest = hashlib.sha256(
                _ERROR_DIGEST_DOMAIN + type(exc).__name__.encode("utf-8")
            ).hexdigest()
            journal.status = MigrationState.FAILED.value
            self.runtime_db.commit()
            raise

    def accept(self, *, boundary_hook: BoundaryHook | None = None) -> MigrationStatus:
        journal = self._required_journal(MigrationState.AWAITING_ACCEPTANCE)
        from anima_server.services.corefs.soul_relocation import (
            active_soul_database_path,
        )

        if active_soul_database_path(self.legacy_user_id) is None:
            raise MigrationOrchestrationError(
                "migration acceptance requires a verified active Soul relocation"
            )
        cutover = read_cutover_record()
        if cutover.validation_generation is None or cutover.validation_catalog_hash is None:
            raise MigrationOrchestrationError(
                "migration acceptance requires a verified validation head"
            )
        if cutover.state is CutoverState.CORE_FS_VALIDATION_READONLY:
            approve_validation_cutover()
        elif cutover.state is not CutoverState.CORE_FS_APPROVED_PENDING_FIRST_WRITE:
            raise MigrationOrchestrationError(
                "migration acceptance does not match the verified validation state"
            )
        _boundary(boundary_hook, "migration:after_cutover_approval")
        self._advance(journal, MigrationState.ACCEPTED)
        return self._status(journal)

    def reject(
        self,
        *,
        corefs_session: Any,
        keys: object,
        boundary_hook: BoundaryHook | None = None,
    ) -> MigrationStatus:
        journal = self._required_journal(MigrationState.AWAITING_ACCEPTANCE)
        from anima_server.services.corefs.soul_relocation import (
            rollback_owner_soul_database,
        )

        rollback_owner_soul_database(self.legacy_user_id)
        if read_cutover_record().state is not CutoverState.LEGACY_AUTHORITATIVE:
            rollback_cutover(corefs_session=corefs_session, keys=keys)
        _boundary(boundary_hook, "migration:after_cutover_rejection")
        self._advance(journal, MigrationState.REJECTED)
        return self._status(journal)

    def _required_journal(self, state: MigrationState) -> CoreFSMigrationJournal:
        journal = self._journal()
        if journal is None or _parse_state(journal.status) is not state:
            raise MigrationOrchestrationError(f"migration must be {state.value} for this operation")
        return journal

    def _journal(self) -> CoreFSMigrationJournal | None:
        journal = self.runtime_db.scalar(
            select(CoreFSMigrationJournal).where(
                CoreFSMigrationJournal.core_id == self.core_id,
                CoreFSMigrationJournal.local_instance_id == self.local_instance_id,
                CoreFSMigrationJournal.converter_id == CONVERTER_ID,
                CoreFSMigrationJournal.source_id_hash == self.source_id_hash,
            )
        )
        if journal is not None and (
            journal.source_checksum is not None or journal.target_checksum is not None
        ):
            journal.source_checksum = None
            journal.target_checksum = None
            self.runtime_db.commit()
        return journal

    def _advance(
        self,
        journal: CoreFSMigrationJournal,
        state: MigrationState,
        *,
        migrated_count: int | None = None,
    ) -> None:
        journal.status = state.value
        # Content-derived digests stay inside the encrypted Core and process
        # memory; plaintext Runtime progress never persists them.
        journal.source_checksum = None
        journal.target_checksum = None
        if migrated_count is not None:
            journal.migrated_count = migrated_count
        journal.error_code = None
        journal.error_digest = None
        self.runtime_db.commit()

    def _status(self, journal: CoreFSMigrationJournal) -> MigrationStatus:
        state = _parse_state(journal.status)
        cutover = read_cutover_record()
        return MigrationStatus(
            state=state,
            generation=cutover.validation_generation,
            catalog_hash=cutover.validation_catalog_hash,
            source_hash=None,
            migrated_count=journal.migrated_count,
            error_code=journal.error_code,
        )


def run_portable_content_migration(
    *,
    session: Any,
    soul_db: Session,
    runtime_db: Session,
    transcripts_dir: Path,
    retry_failed: bool = False,
    boundary_hook: BoundaryHook | None = None,
) -> MigrationStatus:
    if (
        session.corefs_session is None
        or session.corefs_keys is None
        or session.runtime_index is None
    ):
        raise MigrationOrchestrationError("portable migration requires an unlocked Core")
    coordinator = MigrationCoordinator(
        runtime_db,
        core_id=session.runtime_index.core_id,
        local_instance_id=session.runtime_index.local_instance_id,
        legacy_user_id=session.user_id,
    )

    def preflight() -> None:
        if session.runtime_index.core_id != get_core_id():
            raise MigrationOrchestrationError("migration Core identity changed")
        if soul_db.in_transaction():
            raise MigrationOrchestrationError("Soul transaction must be closed before migration")
        if not transcripts_dir.parent.exists():
            raise MigrationOrchestrationError("legacy transcript parent is unavailable")

    def convert() -> MigrationConversionResult:
        from anima_server.services.corefs.asset_migration import (
            prepare_portable_content_validation_catalog,
        )

        result, _conversations, _assets = prepare_portable_content_validation_catalog(
            session=session,
            soul_db=soul_db,
            runtime_db=runtime_db,
            transcripts_dir=transcripts_dir,
        )
        from anima_server.services.corefs.soul_relocation import (
            relocate_owner_soul_database,
        )

        relocate_owner_soul_database(session.user_id)
        return MigrationConversionResult(
            generation=result.generation,
            catalog_hash=result.catalog_hash,
            source_hash=result.source_hash,
            migrated_count=sum(result.source_counts.values()),
        )

    def verify(result: MigrationConversionResult) -> None:
        head = session.corefs_session.validation_snapshot(session.corefs_keys)
        if head != {
            "generation": result.generation,
            "catalogHash": result.catalog_hash,
        }:
            raise MigrationOrchestrationError(
                "native validation head does not match the migration result"
            )

    return coordinator.run(
        preflight=preflight,
        converter=convert,
        verifier=verify,
        retry_failed=retry_failed,
        boundary_hook=boundary_hook,
    )


def _validate_conversion(result: MigrationConversionResult) -> None:
    if (
        isinstance(result.generation, bool)
        or result.generation <= 0
        or not _is_sha256(result.catalog_hash)
        or not _is_sha256(result.source_hash)
        or isinstance(result.migrated_count, bool)
        or result.migrated_count < 0
    ):
        raise MigrationOrchestrationError("migration converter result is invalid")


def _parse_state(value: str) -> MigrationState:
    try:
        return MigrationState(value)
    except ValueError as exc:
        raise MigrationOrchestrationError("migration journal state is invalid") from exc


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _boundary(hook: BoundaryHook | None, name: str) -> None:
    if hook is not None:
        hook(name)
