from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from anima_server.config import settings
from anima_server.services.core import ensure_core_manifest, get_manifest_path, update_core_manifest
from anima_server.services.corefs.cutover import (
    CONTENT_AUTHORITY_FAMILIES,
    CutoverState,
    CutoverStateError,
    approve_validation_cutover,
    begin_migration,
    publish_validation_readonly,
    read_cutover_record,
    reconcile_cutover_authority,
    rollback_cutover,
)
from anima_server.services.sessions import UnlockSessionStore

_CATALOG_HASH = "a" * 64
_COMMITTED_HASH = "b" * 64


class _NativeSession:
    def __init__(self, marker: dict[str, object] | None = None) -> None:
        self.marker = marker
        self.closed = False

    def begin_close(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    def authoritative_cutover_v1(self, _keys: object) -> dict[str, object] | None:
        return self.marker


@pytest.fixture(autouse=True)
def _isolated_core(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path / ".anima")
    ensure_core_manifest()


def _marker(*, epoch: int = 41, generation: int = 8) -> dict[str, object]:
    return {
        "version": 1,
        "legacyRollbackDisabled": True,
        "cutoverEpoch": epoch,
        "generation": generation,
        "catalogHash": _COMMITTED_HASH,
    }


def _prepare_pending() -> int:
    assert begin_migration().state is CutoverState.MIGRATING_WRITE_FROZEN
    validation = publish_validation_readonly(
        generation=7,
        catalog_hash=_CATALOG_HASH,
    )
    assert validation.state is CutoverState.CORE_FS_VALIDATION_READONLY
    pending = approve_validation_cutover()
    assert pending.state is CutoverState.CORE_FS_APPROVED_PENDING_FIRST_WRITE
    assert pending.cutover_epoch is not None
    return pending.cutover_epoch


def test_cutover_state_progression_records_stable_validation_and_epoch() -> None:
    epoch = _prepare_pending()
    assert epoch > 0
    assert read_cutover_record().validation_generation == 7
    assert read_cutover_record().validation_catalog_hash == _CATALOG_HASH
    assert read_cutover_record().cutover_epoch == epoch

    raw = json.loads(get_manifest_path().read_text(encoding="utf-8"))["corefs_cutover"]
    assert raw == {
        "version": 1,
        "state": "corefs-approved-pending-first-write",
        "validationGeneration": 7,
        "validationCatalogHash": _CATALOG_HASH,
        "cutoverEpoch": epoch,
    }


@pytest.mark.parametrize(
    "advance",
    [
        lambda: begin_migration(),
        lambda: (
            begin_migration(),
            publish_validation_readonly(generation=7, catalog_hash=_CATALOG_HASH),
        ),
        lambda: _prepare_pending(),
    ],
)
def test_rollback_is_available_in_every_pre_marker_state(advance) -> None:
    advance()
    rolled_back = rollback_cutover(corefs_session=_NativeSession(), keys=object())
    assert rolled_back.state is CutoverState.LEGACY_AUTHORITATIVE
    assert read_cutover_record() == rolled_back


def test_authenticated_head_recovers_crash_before_manifest_finalization() -> None:
    _prepare_pending()
    authority = reconcile_cutover_authority(
        corefs_session=_NativeSession(_marker()),
        keys=object(),
    )

    assert authority == {
        "version": 1,
        "state": "cutover_complete",
        "legacyRollbackDisabled": True,
        "cutoverEpoch": 41,
        "generation": 8,
        "catalogHash": _COMMITTED_HASH,
        "families": list(CONTENT_AUTHORITY_FAMILIES),
    }
    recovered = read_cutover_record()
    assert recovered.state is CutoverState.CORE_FS_AUTHORITATIVE_FORWARD_ONLY
    assert recovered.cutover_epoch == 41
    assert recovered.authoritative_generation == 8
    assert recovered.authoritative_catalog_hash == _COMMITTED_HASH


def test_unlock_session_publishes_only_authenticated_forward_authority() -> None:
    _prepare_pending()
    native = _NativeSession(_marker(epoch=51, generation=9))
    store = UnlockSessionStore(
        corefs_session_factory=lambda: native,
        runtime_index_factory=lambda _keys, _sqlcipher_key: None,
    )
    token = store.create(1, {"memory": b"m" * 32}, corefs_keys=object())
    session = store.resolve(token)
    assert session is not None
    assert session.content_authority is not None
    assert session.content_authority["cutoverEpoch"] == 51
    assert session.content_authority["generation"] == 9
    assert read_cutover_record().state is CutoverState.CORE_FS_AUTHORITATIVE_FORWARD_ONLY
    store.revoke(token)
    assert native.closed


def test_marker_permanently_rejects_legacy_rollback() -> None:
    _prepare_pending()
    native = _NativeSession(_marker())
    reconcile_cutover_authority(corefs_session=native, keys=object())
    with pytest.raises(CutoverStateError, match="permanently disabled"):
        rollback_cutover(corefs_session=native, keys=object())


def test_forward_only_manifest_without_authenticated_head_fails_closed() -> None:
    _prepare_pending()
    reconcile_cutover_authority(
        corefs_session=_NativeSession(_marker()),
        keys=object(),
    )
    with pytest.raises(CutoverStateError, match="no authenticated"):
        reconcile_cutover_authority(
            corefs_session=_NativeSession(),
            keys=object(),
        )


@pytest.mark.parametrize(
    "bad_marker",
    [
        {**_marker(), "legacyRollbackDisabled": False},
        {**_marker(), "catalogHash": "not-a-hash"},
        {**_marker(), "generation": True},
        {**_marker(), "extra": "ambiguous"},
    ],
)
def test_malformed_or_ambiguous_native_marker_never_grants_authority(bad_marker) -> None:
    _prepare_pending()
    with pytest.raises(CutoverStateError, match="marker"):
        reconcile_cutover_authority(
            corefs_session=_NativeSession(bad_marker),
            keys=object(),
        )
    assert read_cutover_record().state is CutoverState.CORE_FS_APPROVED_PENDING_FIRST_WRITE


def test_manifest_cannot_claim_forward_authority_without_complete_identity() -> None:
    def corrupt(manifest: dict[str, object]) -> None:
        manifest["corefs_cutover"] = {
            "version": 1,
            "state": "corefs-authoritative-forward-only",
            "cutoverEpoch": 1,
        }

    update_core_manifest(corrupt)
    with pytest.raises(CutoverStateError, match="invalid shape"):
        read_cutover_record()


def test_invalid_transition_cannot_skip_validation() -> None:
    with pytest.raises(CutoverStateError, match="cannot approve"):
        approve_validation_cutover()
    assert read_cutover_record().state is CutoverState.LEGACY_AUTHORITATIVE


def test_pending_cutover_fails_closed_without_native_authentication() -> None:
    _prepare_pending()
    fake = SimpleNamespace(
        content_authority={
            "version": 1,
            "state": "cutover_complete",
            "legacyRollbackDisabled": True,
        }
    )
    with pytest.raises(CutoverStateError, match="authentication is unavailable"):
        reconcile_cutover_authority(corefs_session=fake, keys=object())
    assert read_cutover_record().state is CutoverState.CORE_FS_APPROVED_PENDING_FIRST_WRITE
