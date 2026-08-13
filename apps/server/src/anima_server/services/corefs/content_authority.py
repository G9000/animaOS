"""Process-local publication of authenticated CoreFS content authority."""

from __future__ import annotations

import re
from typing import Any

from anima_server.services.corefs.cutover import reconcile_cutover_authority

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def authenticated_content_authority(
    session: Any,
    *,
    family: str,
) -> dict[str, object] | None:
    """Refresh an already-active forward-only session from native authority.

    A missing session marker stays missing so ordinary domain requests cannot
    become the irreversible first cutover mutation.
    """
    if not _marker_allows(getattr(session, "content_authority", None), family=family):
        return None
    marker = reconcile_cutover_authority(
        corefs_session=session.corefs_session,
        keys=session.corefs_keys,
    )
    if not _marker_allows(marker, family=family):
        raise RuntimeError("Authenticated CoreFS content authority is invalid.")
    object.__setattr__(session, "content_authority", marker)
    return marker


def publish_content_authority_after_mutation(
    session: Any,
    *,
    generation: int,
    catalog_hash: str,
) -> dict[str, object]:
    """Publish one trusted native mutation result to local user sessions."""
    current = getattr(session, "content_authority", None)
    if not _marker_allows(current, family=None):
        raise RuntimeError("Authenticated CoreFS content authority is invalid.")
    marker = dict(current)
    marker["generation"] = generation
    marker["catalogHash"] = catalog_hash
    if not _marker_allows(marker, family=None):
        raise RuntimeError("Authenticated CoreFS mutation authority is invalid.")

    from anima_server.services.sessions import active_unlock_sessions

    for active in active_unlock_sessions(int(session.user_id)):
        object.__setattr__(active, "content_authority", dict(marker))
    object.__setattr__(session, "content_authority", dict(marker))
    return marker


def invalidate_active_catalog_indexes(user_id: int) -> None:
    from anima_server.services.sessions import active_runtime_indexes

    for index in active_runtime_indexes(user_id):
        index.begin_catalog()


def _marker_allows(marker: object, *, family: str | None) -> bool:
    if not isinstance(marker, dict):
        return False
    families = marker.get("families")
    generation = marker.get("generation")
    catalog_hash = marker.get("catalogHash")
    return (
        marker.get("version") == 1
        and marker.get("state") == "cutover_complete"
        and marker.get("legacyRollbackDisabled") is True
        and isinstance(families, list)
        and (family is None or family in families)
        and isinstance(generation, int)
        and not isinstance(generation, bool)
        and generation > 0
        and isinstance(catalog_hash, str)
        and _SHA256_HEX.fullmatch(catalog_hash) is not None
        and marker.get("cutoverEpoch") is not None
    )
