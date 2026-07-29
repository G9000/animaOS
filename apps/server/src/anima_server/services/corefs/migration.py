from __future__ import annotations

from anima_server.services.corefs import logical
from anima_server.services.corefs.indexer import ReadinessState
from anima_server.services.sessions import UnlockSession


def reconcile_authenticated_catalog(
    session: UnlockSession,
) -> logical.CoreFsValidationSnapshot:
    """Publish navigation readiness from an authenticated native catalog."""
    if (
        session.runtime_index is None
        or session.corefs_session is None
        or session.corefs_keys is None
    ):
        raise ValueError("CoreFS reconciliation requires an unlocked session")
    selected = logical.select_validation_snapshot(
        corefs_session=session.corefs_session,
        keys=session.corefs_keys,
    )
    snapshot = session.runtime_index.snapshot()
    if snapshot.catalog_generation != selected.generation or snapshot.state in {
        ReadinessState.OPENING_CORE,
        ReadinessState.VALIDATING_CORE,
    }:
        session.runtime_index.begin_catalog()
        session.runtime_index.publish_catalog(
            catalog_generation=selected.generation,
            families={},
        )
    return selected
