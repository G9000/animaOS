from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from threading import Lock

from anima_server.config import settings
from anima_server.db.session import (
    ensure_user_database,
    get_user_database_path,
    get_user_session_factory,
)
from anima_server.models import SoulOwner
from anima_server.services.auth import (
    create_user,
    normalize_username,
    serialize_soul_owner,
    serialize_user,
)
from anima_server.services.core import (
    allocate_user_id,
    ensure_core_manifest,
    get_manifest_path,
    get_owner_user_id,
    is_provisioned,
)
from anima_server.services.corefs.account_profile import (
    read_unlocked_account_profile,
    serialize_account_profile,
)
from anima_server.services.corefs.catalog_reader import CatalogReadError
from anima_server.services.corefs.transactions import serialized_credential_transaction
from anima_server.services.corefs.types import PayloadScope

logger = logging.getLogger(__name__)

_bootstrap_lock = Lock()
_bootstrapped_roots: set[str] = set()


class InvalidCredentialsError(ValueError):
    """Authentication failed because the supplied credentials were invalid."""


def ensure_per_user_databases_ready() -> None:
    ensure_core_manifest()
    if not settings.database_url.startswith("sqlite"):
        return

    data_root_key = str(settings.data_dir.resolve())
    with _bootstrap_lock:
        if data_root_key in _bootstrapped_roots:
            return

        _bootstrapped_roots.add(data_root_key)


def list_user_ids() -> list[int]:
    ensure_per_user_databases_ready()
    owner_user_id = get_owner_user_id()
    if owner_user_id is None or not get_user_database_path(owner_user_id).is_file():
        return []
    from anima_server.services.core import has_supported_soul_keyslots

    if not has_supported_soul_keyslots():
        return []
    return [owner_user_id]


@serialized_credential_transaction
def register_account(
    *,
    username: str,
    password: str,
    display_name: str,
    agent_name: str = "Anima",
    user_directive: str = "",
    relationship: str = "companion",
    persona_template: str = "default",
    agent_type: str = "companion",
) -> tuple[dict[str, object], dict[str, bytes], str, object | None]:
    """Register an account and return its unlocked runtime key material."""
    manifest = ensure_core_manifest()
    pending_initial = manifest.get("initial_registration_state") == "pending"
    if is_provisioned() and not pending_initial:
        raise ValueError("Core is already provisioned")
    normalized = normalize_username(username)
    if not normalized:
        raise ValueError("Username is required")

    from anima_server.services.corefs.keyslots import (
        prepare_initial_manifest_roots,
        unlock_manifest_key_hierarchy,
    )
    from anima_server.services.corefs.types import KeyslotStatus, WrappingPath
    from anima_server.services.recovery import generate_recovery_phrase
    from anima_server.services.sessions import set_sqlcipher_key

    recovery_phrase = generate_recovery_phrase()
    if pending_initial:
        user_id = get_owner_user_id()
        if user_id is None:
            raise ValueError("Initial pending registration owner is unavailable")
        roots = unlock_manifest_key_hierarchy(
            credential=password,
            wrapping_path=WrappingPath.PASSWORD,
            status=KeyslotStatus.PENDING,
            generation=1,
        )
        sqlcipher_raw_key = roots.sqlcipher_key
        if sqlcipher_raw_key is None or set(roots.frks) != {1}:
            raise ValueError("Initial pending registration roots are incomplete")
        set_sqlcipher_key(sqlcipher_raw_key)
        initial_frk = roots.frks[1]
    else:
        user_id = allocate_user_id()
        sqlcipher_raw_key = _maybe_generate_sqlcipher_key(password, user_id)
        initial_frk = None
    initial_frk = prepare_initial_manifest_roots(
        user_id=user_id,
        password=password,
        recovery_phrase=recovery_phrase,
        sqlcipher_key=sqlcipher_raw_key,
        frk=initial_frk,
    )
    factory = ensure_user_database(user_id)
    corefs_keys: object | None = None

    with factory() as db:
        if db.get(SoulOwner, user_id) is not None:
            raise ValueError("Initial registration is committed; sign in to finish activation")
        user, deks = create_user(
            db,
            username=normalized,
            password=password,
            display_name=display_name,
            agent_name=agent_name,
            user_directive=user_directive,
            relationship=relationship,
            persona_template=persona_template,
            agent_type=agent_type,
            user_id=user_id,
            commit=False,
        )
        from dataclasses import replace
        from datetime import datetime

        user = replace(
            user,
            created_at=datetime.fromisoformat(
                str(ensure_core_manifest()["initial_registration_created_at"])
            ),
        )

        if sqlcipher_raw_key is not None:
            from anima_server.services.corefs.greenfield import (
                activate_initial_account_content,
                bootstrap_account_content,
            )
            from anima_server.services.corefs.keyslots import provision_initial_key_hierarchy

            corefs_keys = provision_initial_key_hierarchy(
                db,
                user_id=user_id,
                password=password,
                recovery_phrase=recovery_phrase,
                sqlcipher_key=sqlcipher_raw_key,
                deks=deks,
                initial_frk=initial_frk,
                prepare_content=lambda keys: bootstrap_account_content(
                    user_id=user_id,
                    corefs_keys=keys,
                    profile=user,
                    activate=False,
                ),
                activate_content=lambda keys: activate_initial_account_content(
                    user_id=user_id,
                    corefs_keys=keys,
                ),
            )

    if corefs_keys is None:
        raise ValueError("Supported Core registration requires an encrypted key hierarchy")
    return serialize_user(user), deks, recovery_phrase, corefs_keys


@serialized_credential_transaction
def authenticate_account(
    username: str, password: str
) -> tuple[dict[str, object], dict[str, bytes], object | None]:
    normalized = normalize_username(username)
    if not normalized:
        raise InvalidCredentialsError("Invalid credentials")

    manifest_path = get_manifest_path()
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    )
    from anima_server.services.corefs.keyslots import (
        derive_active_corefs_subkeys,
        manifest_has_versioned_key_hierarchy,
        unlock_manifest_key_hierarchy,
    )

    if manifest_has_versioned_key_hierarchy(manifest):
        from cryptography.exceptions import InvalidTag

        from anima_server.services.corefs.credentials import (
            finalize_pending_password_generation,
        )
        from anima_server.services.corefs.types import WrappingPath
        from anima_server.services.sessions import set_sqlcipher_key

        try:
            roots = unlock_manifest_key_hierarchy(
                credential=password,
                wrapping_path=WrappingPath.PASSWORD,
            )
            if roots.scope is PayloadScope.FS or roots.sqlcipher_key is None:
                raise ValueError("filesystem-only credentials cannot unlock an agent")
            set_sqlcipher_key(roots.sqlcipher_key)
            account_user_id = get_owner_user_id()
            if account_user_id is None:
                raise ValueError("versioned Core is missing its opaque owner binding")
            corefs_keys = (
                derive_active_corefs_subkeys(manifest, roots.frks)
                if roots.scope is PayloadScope.FULL
                else None
            )
            with _authenticated_account_soul(
                account_user_id,
                corefs_keys,
                credential=password,
                wrapping_path=WrappingPath.PASSWORD,
                scope=roots.scope,
                owner_id=roots.owner_id,
                expected_username=normalized,
            ) as (db, unlocked):
                user = db.get(SoulOwner, account_user_id)
                if user is None:
                    raise ValueError("versioned owner is missing from the Soul database")
                finalize_pending_password_generation(db, user, password=password)
                corefs_keys = (
                    derive_active_corefs_subkeys(manifest, unlocked.frks)
                    if roots.scope is PayloadScope.FULL
                    else None
                )
                profile = (
                    read_unlocked_account_profile(
                        user_id=account_user_id,
                        corefs_keys=corefs_keys,
                    )
                    if corefs_keys is not None
                    else None
                )
                if profile is not None:
                    if normalize_username(profile.username) != normalized:
                        raise ValueError("encrypted account profile does not match login")
                    response = serialize_account_profile(profile)
                else:
                    response = serialize_soul_owner(user)
                if manifest.get("initial_registration_state") == "pending":
                    from anima_server.services.corefs.keyslots import (
                        activate_initial_manifest_roots,
                    )

                    activate_initial_manifest_roots()
                return response, unlocked.soul_domains, corefs_keys
        except (CatalogReadError, InvalidTag, KeyError, TypeError, ValueError) as exc:
            raise InvalidCredentialsError(
                f"versioned key hierarchy authentication failed: {exc}"
            ) from exc

    raise InvalidCredentialsError(
        "Unsupported pre-release credential hierarchy; restore a supported encrypted Core."
    )


@serialized_credential_transaction
def recover_account_with_phrase(
    phrase: str,
    new_password: str,
    *,
    scope: PayloadScope = PayloadScope.FULL,
) -> tuple[dict[str, object], dict[str, bytes], object | None]:
    """Recover an account using the recovery phrase and set a new password.

    Returns user data, Soul DEKs, and active CoreFS subkeys on success.
    """
    from cryptography.exceptions import InvalidTag

    from anima_server.services.corefs.keyslots import (
        derive_active_corefs_subkeys,
        manifest_has_versioned_key_hierarchy,
        unlock_manifest_key_hierarchy,
    )
    from anima_server.services.corefs.types import WrappingPath
    from anima_server.services.sessions import set_sqlcipher_key

    manifest_path = get_manifest_path()
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    )
    if manifest_has_versioned_key_hierarchy(manifest):
        if scope is PayloadScope.FS:
            raise ValueError("CoreFS-only recovery cannot start an agent session")
        try:
            roots = unlock_manifest_key_hierarchy(
                credential=phrase,
                wrapping_path=WrappingPath.RECOVERY,
                expected_scope=scope,
            )
            if roots.sqlcipher_key is None:
                raise ValueError("versioned recovery is missing the Soul root")
            set_sqlcipher_key(roots.sqlcipher_key)
            user_id = get_owner_user_id()
            if user_id is None:
                raise ValueError("versioned recovery is missing its owner binding")
            corefs_keys = (
                derive_active_corefs_subkeys(manifest, roots.frks)
                if roots.scope is PayloadScope.FULL
                else None
            )
            with _authenticated_account_soul(
                user_id,
                corefs_keys,
                credential=phrase,
                wrapping_path=WrappingPath.RECOVERY,
                scope=roots.scope,
                owner_id=roots.owner_id,
            ) as (db, _unlocked):
                from anima_server.services.corefs.credentials import (
                    recover_password_credential_generation,
                )

                user = db.get(SoulOwner, user_id)
                if user is None:
                    raise ValueError("User not found")
                deks = recover_password_credential_generation(
                    db,
                    user,
                    recovery_phrase=phrase,
                    new_password=new_password,
                    scope=scope,
                )
                corefs_keys = (
                    derive_active_corefs_subkeys(manifest, roots.frks)
                    if roots.scope is PayloadScope.FULL
                    else None
                )
                profile = (
                    read_unlocked_account_profile(user_id=user_id, corefs_keys=corefs_keys)
                    if corefs_keys is not None
                    else None
                )
                if profile is None:
                    return serialize_soul_owner(user), deks, None
                return serialize_account_profile(profile), deks, corefs_keys
        except (InvalidTag, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Versioned recovery failed: {exc}") from None

    raise ValueError(
        "Unsupported pre-release recovery hierarchy; restore a supported encrypted Core."
    )


# ---------------------------------------------------------------------------
# Unified passphrase helpers
# ---------------------------------------------------------------------------


def _maybe_generate_sqlcipher_key(password: str, user_id: int) -> bytes | None:
    """Generate and store a wrapped SQLCipher key if unified mode is active.

    Called during registration when no ANIMA_CORE_PASSPHRASE env var is set.
    The SQLCipher key is random (high entropy), wrapped with the user's
    password-derived KEK, and stored in the manifest.

    Returns the raw key if one was generated, or None in env var mode.
    """
    if settings.core_passphrase.strip():
        raise ValueError("Supported Core registration requires password keyslots")

    import os

    from anima_server.services.crypto import KEY_LENGTH
    from anima_server.services.sessions import set_sqlcipher_key

    raw_key = os.urandom(KEY_LENGTH)
    set_sqlcipher_key(raw_key)
    return raw_key


@contextmanager
def _authenticated_account_soul(
    user_id: int,
    corefs_keys: object | None,
    *,
    credential: str,
    wrapping_path: object,
    scope: PayloadScope,
    owner_id: str,
    expected_username: str | None = None,
):
    from anima_server.db.session import get_session_factory, get_user_database_url
    from anima_server.services.corefs.keyslots import unlock_key_hierarchy
    from anima_server.services.corefs.soul_provenance import (
        authenticated_soul_evolution,
        authenticated_soul_only_evolution,
    )
    from anima_server.services.corefs.soul_store import verified_supported_schema_recovery_copy

    # Authenticate every retained Soul domain on the original schema before any
    # destructive forward upgrade. This factory deliberately does not migrate.
    with get_session_factory(get_user_database_url(user_id))() as source_db:
        unlocked = unlock_key_hierarchy(
            source_db, credential=credential, wrapping_path=wrapping_path, scope=scope
        )
        if unlocked.owner_id != owner_id:
            raise ValueError("Soul keyslot owner differs from authenticated manifest owner")
    proof_context = (
        authenticated_soul_evolution(user_id=user_id, corefs_keys=corefs_keys)
        if corefs_keys is not None
        else authenticated_soul_only_evolution(user_id=user_id, owner_id=owner_id)
    )
    with proof_context as proof:
        if (
            expected_username is not None
            and proof.profile is not None
            and normalize_username(proof.profile.username) != expected_username
        ):
            raise ValueError("encrypted account profile does not match login")
        with (
            verified_supported_schema_recovery_copy(user_id=user_id),
            get_user_session_factory(user_id)() as db,
        ):
            yield db, unlocked
