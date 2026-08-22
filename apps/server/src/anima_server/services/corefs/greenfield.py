"""Automatic bootstrap for the first supported portable Core release."""

from __future__ import annotations

from typing import Any

from anima_server.config import settings
from anima_server.db.runtime import get_runtime_session_factory
from anima_server.db.session import get_user_session_factory
from anima_server.services.corefs.authority import (
    activate_content_authority,
    reconcile_content_authority,
)


class GreenfieldBootstrapError(RuntimeError):
    pass


def bootstrap_greenfield_content(session: Any) -> dict[str, object]:
    """Prepare the current-release catalog and activate it before login returns."""
    if (
        session.corefs_session is None
        or session.corefs_keys is None
        or session.runtime_index is None
    ):
        raise GreenfieldBootstrapError(
            "The first portable Core release requires encrypted CoreFS key material."
        )

    authority = reconcile_content_authority(
        corefs_session=session.corefs_session,
        keys=session.corefs_keys,
    )
    if authority is not None:
        object.__setattr__(session, "content_authority", authority)
        return authority

    from anima_server.services.corefs.asset_migration import (
        prepare_portable_content_validation_catalog,
    )

    try:
        runtime_factory = get_runtime_session_factory()
        with (
            get_user_session_factory(int(session.user_id))() as soul_db,
            runtime_factory() as runtime_db,
        ):
            prepared, _conversations, _assets = prepare_portable_content_validation_catalog(
                session=session,
                soul_db=soul_db,
                runtime_db=runtime_db,
                transcripts_dir=settings.data_dir / "transcripts",
            )
        authority = activate_content_authority(
            corefs_session=session.corefs_session,
            keys=session.corefs_keys,
            generation=prepared.generation,
            catalog_hash=prepared.catalog_hash,
        )
    except Exception as exc:
        raise GreenfieldBootstrapError("ANIMA CORE greenfield bootstrap failed closed.") from exc
    object.__setattr__(session, "content_authority", authority)
    return authority
