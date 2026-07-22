from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import shutil
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models import (
    AgentExperience,
    AgentMessage,
    AgentRun,
    AgentSkill,
    AgentStep,
    AgentThread,
    DreamJournal,
    EmotionalSignal,
    ExperienceClusterState,
    ForesightSignal,
    InitiativeLog,
    KGEntity,
    KGRelation,
    LatentTrace,
    MemoryClaim,
    MemoryClaimEvidence,
    MemoryEpisode,
    MemoryItem,
    MemoryItemEvidence,
    ReconsolidationLog,
    SelfModelBlock,
    SoulKeyslot,
    Task,
    TendencyContribution,
    User,
    UserKey,
    UserProfileField,
    UserProfileFieldEvidence,
)
from anima_server.services import anima_core_bindings
from anima_server.services.core import update_core_manifest
from anima_server.services.crypto import (
    AUTH_TAG_LENGTH,
    IV_LENGTH,
    KEY_LENGTH,
    SALT_LENGTH,
    VAULT_ARGON2_MEMORY_COST_KIB,
    VAULT_ARGON2_PARALLELISM,
    VAULT_ARGON2_TIME_COST,
    decrypt_text_with_dek,
    derive_argon2id_key,
)
from anima_server.services.data_crypto import ef as encrypt_field_for_user
from anima_server.services.data_crypto import resolve_domain
from anima_server.services.sessions import get_active_deks

vault_logger = logging.getLogger(__name__)


def _load_capsule_bindings():
    read_capsule = anima_core_bindings.rust_read_capsule
    write_capsule = anima_core_bindings.rust_write_capsule
    if read_capsule is None or write_capsule is None:
        vault_logger.warning(
            "Capsule bindings are unavailable; anima_core capsule bindings are missing.",
        )
        return None, None

    return read_capsule, write_capsule


_anima_core_read_capsule, _anima_core_write_capsule = _load_capsule_bindings()


VAULT_VERSION = 2
VAULT_FORMAT_JSON = "vault_json"
VAULT_FORMAT_CAPSULE = "anima_capsule"
_MAX_VAULT_TIME_COST = 10
_MAX_VAULT_MEMORY_COST_KIB = 2_097_152
_MAX_VAULT_PARALLELISM = 8

_PORTABLE_MANIFEST_AUTHORITY_FIELDS = (
    "version",
    "schema_version",
    "core_id",
    "created_at",
    "next_user_id",
    "owner_id",
    "owner_user_id",
    "owner_binding",
    "user_index",
    "sqlcipher_kdf_salt",
    "wrapped_sqlcipher_key",
    "recovery_sqlcipher_key",
    "keyslots_version",
    "keyslots",
    "active_password_credential_generation",
    "active_recovery_credential_generation",
    "pending_recovery_credential",
    "frk_rotation",
)
_VERSIONED_KEY_HIERARCHY_FIELDS = (
    "core_id",
    "owner_id",
    "keyslots_version",
    "keyslots",
    "active_password_credential_generation",
    "active_recovery_credential_generation",
    "pending_recovery_credential",
    "frk_rotation",
)

# ---------------------------------------------------------------------------
# Vault version migration
# ---------------------------------------------------------------------------
# Each migrator transforms a *decrypted* payload dict from version N to N+1.
# Only the inner payload is migrated — the outer envelope is handled separately.


def _migrate_v1_to_v2(payload: dict[str, Any]) -> dict[str, Any]:
    """v1 → v2: no structural changes to inner payload; version bump only."""
    payload["version"] = 2
    return payload


VAULT_MIGRATORS: dict[int, Any] = {
    1: _migrate_v1_to_v2,
}


def _migrate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Run migration chain from payload's version up to VAULT_VERSION."""
    version = payload.get("version", 1)
    if version > VAULT_VERSION:
        raise ValueError(
            f"Vault version {version} is newer than supported ({VAULT_VERSION}). "
            "Upgrade your software to import this vault."
        )
    while version < VAULT_VERSION:
        migrator = VAULT_MIGRATORS.get(version)
        if migrator is None:
            raise ValueError(f"No migration path from vault version {version} to {version + 1}.")
        payload = migrator(payload)
        version = payload.get("version", version + 1)
    return payload


_MEMORY_TABLES = frozenset(
    {
        "memoryItemEvidence",
        "memoryItems",
        "memoryEpisodes",
        "userProfileFields",
        "userProfileFieldEvidence",
        "kgEntities",
        "kgRelations",
        "selfModelBlocks",
        "emotionalSignals",
        "foresightSignals",
        "agentExperiences",
        "experienceClusterState",
        "agentSkills",
        "latentTraces",
        "memoryClaims",
        "tendencyContributions",
        "reconsolidationLog",
        "initiativeLog",
        "dreamJournal",
    }
)

_IDENTITY_TABLES = frozenset(
    {
        "soulKeyslots",
        "users",
        "userKeys",
    }
)

# NOTE: After P2, new conversations are stored in the runtime PostgreSQL
# database (RuntimeThread/RuntimeMessage).  The soul-DB agent_threads and
# agent_messages tables are legacy and will be empty for new deployments.
# Chat history is ephemeral by design (compacted away), so omitting it
# from vault exports is intentional.
_CONVERSATION_TABLES = frozenset(
    {
        "agentThreads",
        "agentRuns",
        "agentSteps",
        "agentMessages",
        "tasks",
    }
)

_CAPSULE_CARD_TABLES = frozenset(
    {
        "soulKeyslots",
        "users",
        "userKeys",
        "memoryItems",
        "memoryItemEvidence",
        "userProfileFields",
        "userProfileFieldEvidence",
        "selfModelBlocks",
        "emotionalSignals",
        "foresightSignals",
        "agentExperiences",
        "experienceClusterState",
        "agentSkills",
        "latentTraces",
        "memoryClaims",
        "tendencyContributions",
        "reconsolidationLog",
        "initiativeLog",
        "dreamJournal",
    }
)

_CAPSULE_FRAME_TABLES = frozenset(
    {
        "memoryEpisodes",
        "tasks",
        "agentThreads",
        "agentRuns",
        "agentSteps",
        "agentMessages",
    }
)

_CAPSULE_GRAPH_TABLES = frozenset(
    {
        "kgEntities",
        "kgRelations",
    }
)


def _decrypt_field_value(
    value: str | None,
    deks: dict[str, bytes] | None,
    *,
    table: str = "",
    field: str = "",
    user_id: int | None = None,
) -> str | None:
    """Decrypt a field-level encrypted value for vault export.

    Returns plaintext so the vault envelope is the only encryption layer.
    Resolves the correct domain DEK from the table name.
    """
    if value is None or deks is None:
        return value
    domain = resolve_domain(table)
    dek = deks.get(domain)
    if dek is None:
        return value
    aad = f"{table}:{user_id}:{field}".encode() if table and user_id is not None and field else None
    return decrypt_text_with_dek(value, dek, aad=aad)


def _re_encrypt_field_value(
    value: str | None,
    user_id: int,
    *,
    table: str = "",
    field: str = "",
) -> str | None:
    """Re-encrypt a plaintext value with the importing user's active DEK."""
    if value is None:
        return value
    return encrypt_field_for_user(user_id, value, table=table, field=field)


def _validate_vault_format(transfer_format: str) -> str:
    if transfer_format not in {VAULT_FORMAT_JSON, VAULT_FORMAT_CAPSULE}:
        raise ValueError(
            "Unsupported vault format. Expected 'vault_json' or 'anima_capsule'."
        )
    return transfer_format


def _build_vault_payload(
    db: Session,
    *,
    user_id: int | None = None,
    scope: str = "full",
) -> dict[str, Any]:
    deks: dict[str, bytes] | None = None
    if user_id is not None:
        deks = get_active_deks(user_id)

    full_snapshot = export_database_snapshot(db, user_id=user_id, deks=deks)

    if scope == "memories":
        snapshot = {
            key: value
            for key, value in full_snapshot.items()
            if key in _MEMORY_TABLES or key in _IDENTITY_TABLES
        }
    else:
        snapshot = full_snapshot

    return {
        "version": VAULT_VERSION,
        "createdAt": datetime.now(UTC).isoformat(),
        "scope": scope,
        "database": snapshot,
        "manifest": _read_manifest_snapshot(),
        "userFiles": read_data_snapshot(user_id=user_id) if scope == "full" else {},
    }


def _serialize_capsule_section(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _deserialize_capsule_section(
    sections: dict[str, bytes],
    section_name: str,
) -> dict[str, Any]:
    raw = sections.get(section_name)
    if raw is None:
        return {}

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Capsule section '{section_name}' is not valid JSON.") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"Capsule section '{section_name}' must decode to an object.")

    return payload


def _payload_to_capsule_sections(payload: dict[str, Any]) -> dict[str, bytes]:
    database = payload.get("database")
    if not isinstance(database, dict):
        raise ValueError("Vault payload is missing the database snapshot.")

    sections: dict[str, bytes] = {
        "cards": _serialize_capsule_section(
            {
                "database": {
                    table: database.get(table, [])
                    for table in sorted(_CAPSULE_CARD_TABLES)
                }
            }
        ),
        "frames": _serialize_capsule_section(
            {
                "database": {
                    table: database.get(table, [])
                    for table in sorted(_CAPSULE_FRAME_TABLES)
                }
            }
        ),
        "graph": _serialize_capsule_section(
            {
                "database": {
                    table: database.get(table, [])
                    for table in sorted(_CAPSULE_GRAPH_TABLES)
                }
            }
        ),
        "metadata": _serialize_capsule_section(
            {
                "version": payload.get("version", VAULT_VERSION),
                "createdAt": payload.get("createdAt"),
                "scope": payload.get("scope", "full"),
                "manifest": payload.get("manifest", {}),
                "userFiles": payload.get("userFiles", {}),
            }
        ),
    }

    return sections


def _capsule_sections_to_payload(sections: dict[str, bytes]) -> dict[str, Any]:
    metadata = _deserialize_capsule_section(sections, "metadata")
    if not metadata:
        raise ValueError("Capsule is missing the metadata section.")

    database: dict[str, Any] = {}
    for section_name in ("cards", "frames", "graph"):
        section_payload = _deserialize_capsule_section(sections, section_name)
        if not section_payload:
            continue

        section_database = section_payload.get("database", section_payload)
        if not isinstance(section_database, dict):
            raise ValueError(
                f"Capsule section '{section_name}' is missing a database payload."
            )

        for table_name, table_payload in section_database.items():
            if table_name in database:
                raise ValueError(
                    f"Capsule contains duplicate table data for '{table_name}'."
                )
            database[table_name] = table_payload

    return {
        "version": metadata.get("version", VAULT_VERSION),
        "createdAt": metadata.get("createdAt"),
        "scope": metadata.get("scope", "full"),
        "database": database,
        "manifest": metadata.get("manifest", {}),
        "userFiles": metadata.get("userFiles", {}),
    }


def _write_capsule_bytes(sections: dict[str, bytes], passphrase: str) -> bytes:
    if _anima_core_write_capsule is None:
        raise ValueError(
            "anima_core capsule export is unavailable in this environment."
        )

    try:
        capsule = _anima_core_write_capsule(
            sections,
            password=passphrase.encode("utf-8"),
        )
    except TypeError as exc:
        raise ValueError(
            "Installed anima_core does not support encrypted capsule export."
        ) from exc
    except Exception as exc:
        raise ValueError("Failed to build anima capsule.") from exc

    if not isinstance(capsule, (bytes, bytearray)):
        raise ValueError("anima_core returned an invalid capsule payload.")

    return bytes(capsule)


def _read_capsule_sections(data: bytes, passphrase: str) -> dict[str, bytes]:
    if _anima_core_read_capsule is None:
        raise ValueError(
            "anima_core capsule import is unavailable in this environment."
        )

    try:
        sections = _anima_core_read_capsule(
            data,
            password=passphrase.encode("utf-8"),
        )
    except TypeError as exc:
        raise ValueError(
            "Installed anima_core does not support encrypted capsule import."
        ) from exc
    except Exception as exc:
        raise ValueError(
            "Failed to read anima capsule. Check the passphrase and payload."
        ) from exc

    if not isinstance(sections, dict):
        raise ValueError("anima_core returned an invalid capsule payload.")

    normalized: dict[str, bytes] = {}
    for key, value in sections.items():
        if not isinstance(key, str) or not isinstance(value, (bytes, bytearray)):
            raise ValueError("anima_core returned malformed capsule sections.")
        normalized[key] = bytes(value)

    return normalized


def export_vault(
    db: Session,
    passphrase: str,
    *,
    user_id: int | None = None,
    scope: str = "full",
    transfer_format: str = VAULT_FORMAT_JSON,
) -> dict[str, Any]:
    transfer_format = _validate_vault_format(transfer_format)
    payload = _build_vault_payload(db, user_id=user_id, scope=scope)
    date_stamp = datetime.now().date().isoformat()

    if transfer_format == VAULT_FORMAT_CAPSULE:
        capsule = _write_capsule_bytes(
            _payload_to_capsule_sections(payload),
            passphrase,
        )
        vault = base64.b64encode(capsule).decode("ascii")
        filename = f"anima-vault-{date_stamp}.anima"
    else:
        plaintext = json.dumps(payload)
        aad = f"anima-vault:v{VAULT_VERSION}:{scope}".encode()
        envelope = encrypt_string(plaintext, passphrase, aad=aad)
        vault = json.dumps(envelope)
        filename = f"anima-vault-{date_stamp}.vault.json"

    return {
        "filename": filename,
        "vault": vault,
        "size": len(vault.encode("utf-8")),
        "format": transfer_format,
    }


def _clear_runtime_proactive_state(
    user_id: int, runtime_factory: Callable[..., Session] | None = None
) -> None:
    """Delete a user's IL3 RUNTIME-tier proactive state (pending initiatives +
    drive pressures) after a vault import. This state is ephemeral/rebuildable
    and never carried in the vault, so pre-import rows must not survive a
    restore and surface stale proactive messages. Best-effort: a cold/headless
    transfer may have no runtime store, so any failure is logged and swallowed
    rather than failing the import. ``runtime_factory`` is an injection seam
    for tests; production resolves the process runtime session factory."""
    try:
        from anima_server.models.runtime_consciousness import (
            DriveStateRow,
            PendingInitiative,
        )

        if runtime_factory is None:
            from anima_server.db.runtime import get_runtime_session_factory

            runtime_factory = get_runtime_session_factory()
        with runtime_factory() as runtime_db:
            runtime_db.query(PendingInitiative).filter(
                PendingInitiative.user_id == user_id
            ).delete()
            runtime_db.query(DriveStateRow).filter(
                DriveStateRow.user_id == user_id
            ).delete()
            runtime_db.commit()
    except Exception:
        vault_logger.warning(
            "Could not clear runtime proactive state on import for user %s",
            user_id,
            exc_info=True,
        )


def import_vault(
    db: Session,
    vault: str,
    passphrase: str,
    *,
    user_id: int | None = None,
    transfer_format: str = VAULT_FORMAT_JSON,
) -> dict[str, Any]:
    """Import an encrypted vault into the current database context."""
    transfer_format = _validate_vault_format(transfer_format)

    if transfer_format == VAULT_FORMAT_CAPSULE:
        try:
            capsule = base64.b64decode(vault, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Capsule payload is not valid base64.") from exc

        payload = _capsule_sections_to_payload(
            _read_capsule_sections(capsule, passphrase)
        )
    else:
        try:
            envelope = json.loads(vault)
        except json.JSONDecodeError as exc:
            raise ValueError("Vault payload is not valid JSON.") from exc

        plaintext = decrypt_string(envelope, passphrase)

        try:
            payload = json.loads(plaintext)
        except json.JSONDecodeError as exc:
            raise ValueError("Vault payload is not valid JSON.") from exc

    payload = _migrate_payload(payload)

    database = payload.get("database")
    if not isinstance(database, dict):
        raise ValueError("Vault payload is missing the database snapshot.")

    user_files = payload.get("userFiles")
    if user_files is None:
        user_files = {}
    if not isinstance(user_files, dict):
        raise ValueError("Vault payload user files are invalid.")

    vault_scope = payload.get("scope", "full")

    vault_manifest = payload.get("manifest")
    current_manifest = _read_manifest_snapshot()
    rebind_to_current_hierarchy = (
        user_id is not None
        and (
            not isinstance(vault_manifest, dict)
            or not _manifest_key_hierarchy_matches(current_manifest, vault_manifest)
        )
    )
    if rebind_to_current_hierarchy:
        _rebind_snapshot_key_hierarchy(
            db,
            database,
            user_id=user_id,
            current_manifest=current_manifest,
        )

    # Re-encrypt plaintext fields with importing user's DEK before restoring
    if user_id is not None:
        _re_encrypt_snapshot_fields(database, user_id)

    restore_database_snapshot(db, database, scope=vault_scope)
    # The restore replaced the soul-store InitiativeLog rows, but IL3 also has
    # RUNTIME-tier proactive state (PendingInitiative — the pollable message
    # text + its initiative_log_id — and DriveStateRow pressures) that the
    # vault deliberately treats as ephemeral and never imports. Left in place,
    # a pre-import PendingInitiative could still be served by the /initiatives
    # endpoint after reauth, pointing at provenance that no longer exists or at
    # a restored, unrelated log. Clear that runtime proactive state so it can't
    # outlive the import. Guarded: cold/headless transfers may have no runtime
    # store initialized, and it's rebuildable, so a failure here is non-fatal.
    if user_id is not None:
        _clear_runtime_proactive_state(user_id)
    write_data_snapshot(user_files, user_id=user_id)

    # Cold transfers and exact same-hierarchy restores carry the exported
    # manifest authority. Authenticated imports into another hierarchy keep
    # the destination authority that was used to re-encrypt the snapshot.
    if isinstance(vault_manifest, dict) and not rebind_to_current_hierarchy:
        _restore_manifest_identity(vault_manifest)

    # Rebuild vector index from imported embeddings
    _rebuild_vector_indices(db, database)
    _invalidate_restored_memory_indexes(database, fallback_user_id=user_id)

    restored_memory_files = sum(
        1
        for path in user_files
        if isinstance(path, str) and "/memory/" in path and path.endswith(".md")
    )
    restored_users = len(database.get("users", []))

    return {
        "restoredUsers": restored_users,
        "restoredMemoryFiles": restored_memory_files,
        "requiresReauth": True,
        "format": transfer_format,
    }


def _invalidate_restored_memory_indexes(
    database: dict[str, Any],
    *,
    fallback_user_id: int | None,
) -> None:
    memory_items = database.get("memoryItems", database.get("memory_items", []))
    user_ids: set[int] = set()
    if isinstance(memory_items, list):
        for record in memory_items:
            if not isinstance(record, dict):
                continue
            try:
                user_ids.add(int(record["user_id"]))
            except (KeyError, TypeError, ValueError):
                continue
    memory_item_evidence = database.get("memoryItemEvidence", [])
    if isinstance(memory_item_evidence, list):
        for record in memory_item_evidence:
            if not isinstance(record, dict):
                continue
            try:
                user_ids.add(int(record["user_id"]))
            except (KeyError, TypeError, ValueError):
                continue
    if not user_ids and fallback_user_id is not None:
        user_ids.add(fallback_user_id)
    if not user_ids:
        return

    try:
        from anima_server.services.agent.memory_store import invalidate_memory_retrieval_indexes

        for restored_user_id in user_ids:
            invalidate_memory_retrieval_indexes(restored_user_id)
    except Exception:
        vault_logger.debug("Memory retrieval invalidation skipped during vault import", exc_info=True)


def encrypt_string(
    plaintext: str,
    passphrase: str,
    *,
    aad: bytes | None = None,
) -> dict[str, Any]:
    salt = random_bytes(SALT_LENGTH)
    iv = random_bytes(IV_LENGTH)
    key = derive_argon2id_key(
        passphrase,
        salt,
        time_cost=VAULT_ARGON2_TIME_COST,
        memory_cost_kib=VAULT_ARGON2_MEMORY_COST_KIB,
        parallelism=VAULT_ARGON2_PARALLELISM,
    )
    encrypted = AESGCM(key).encrypt(iv, plaintext.encode("utf-8"), aad)
    ciphertext, tag = encrypted[:-AUTH_TAG_LENGTH], encrypted[-AUTH_TAG_LENGTH:]
    ciphertext_b64 = base64.b64encode(ciphertext).decode("ascii")
    integrity_hash = hashlib.sha256(ciphertext_b64.encode("ascii")).hexdigest()
    envelope: dict[str, Any] = {
        "version": VAULT_VERSION,
        "createdAt": datetime.now(UTC).isoformat(),
        "payloadVersion": VAULT_VERSION,
        "kdf": {
            "name": "argon2id",
            "timeCost": VAULT_ARGON2_TIME_COST,
            "memoryCostKiB": VAULT_ARGON2_MEMORY_COST_KIB,
            "parallelism": VAULT_ARGON2_PARALLELISM,
            "keyLength": KEY_LENGTH,
            "salt": base64.b64encode(salt).decode("ascii"),
        },
        "cipher": {
            "name": "aes-256-gcm",
            "iv": base64.b64encode(iv).decode("ascii"),
            "tag": base64.b64encode(tag).decode("ascii"),
        },
        "ciphertext": ciphertext_b64,
        "checksum": {
            "algorithm": "sha256",
            "hash": integrity_hash,
        },
    }
    if aad is not None:
        envelope["aad_b64"] = base64.b64encode(aad).decode("ascii")
    return envelope


def decrypt_string(envelope: dict[str, Any], passphrase: str) -> str:
    version = envelope.get("version")
    if not isinstance(version, int) or version < 1:
        raise ValueError(f"Unsupported vault version: {version}.")
    if version > VAULT_VERSION:
        raise ValueError(
            f"Vault version {version} is newer than supported ({VAULT_VERSION}). "
            "Upgrade your software to import this vault.",
        )

    kdf = envelope.get("kdf")
    cipher = envelope.get("cipher")
    ciphertext_b64 = envelope.get("ciphertext")
    if (
        not isinstance(kdf, dict)
        or not isinstance(cipher, dict)
        or not isinstance(ciphertext_b64, str)
        or kdf.get("name") != "argon2id"
        or cipher.get("name") != "aes-256-gcm"
    ):
        raise ValueError("Vault payload format is invalid.")

    # Pre-decryption checksum check (accept legacy "integrity" envelopes too)
    checksum = envelope.get("checksum")
    if not isinstance(checksum, dict):
        checksum = envelope.get("integrity")
    if isinstance(checksum, dict) and checksum.get("algorithm") == "sha256":
        expected = checksum.get("hash", "")
        actual = hashlib.sha256(ciphertext_b64.encode("ascii")).hexdigest()
        if actual != expected:
            raise ValueError("Vault integrity check failed — ciphertext may be corrupted.")

    # Recover AAD from envelope (backwards-compatible: None if absent)
    aad: bytes | None = None
    aad_b64 = envelope.get("aad_b64")
    if isinstance(aad_b64, str):
        aad = base64.b64decode(aad_b64)

    try:
        time_cost = int(kdf["timeCost"])
        memory_cost_kib = int(kdf["memoryCostKiB"])
        parallelism = int(kdf["parallelism"])
        key_length = int(kdf["keyLength"])
        _validate_vault_kdf_params(
            time_cost=time_cost,
            memory_cost_kib=memory_cost_kib,
            parallelism=parallelism,
            key_length=key_length,
        )
        salt = base64.b64decode(str(kdf["salt"]))
        iv = base64.b64decode(str(cipher["iv"]))
        tag = base64.b64decode(str(cipher["tag"]))
        ciphertext = base64.b64decode(ciphertext_b64)
        key = derive_argon2id_key(
            passphrase,
            salt,
            time_cost=time_cost,
            memory_cost_kib=memory_cost_kib,
            parallelism=parallelism,
            key_length=key_length,
        )
        plaintext = AESGCM(key).decrypt(iv, ciphertext + tag, aad)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Failed to decrypt vault. Check the passphrase and payload.") from exc

    return plaintext.decode("utf-8")


def _validate_vault_kdf_params(
    *,
    time_cost: int,
    memory_cost_kib: int,
    parallelism: int,
    key_length: int,
) -> None:
    if time_cost > _MAX_VAULT_TIME_COST:
        raise ValueError("Vault KDF timeCost exceeds maximum allowed value of 10.")
    if memory_cost_kib > _MAX_VAULT_MEMORY_COST_KIB:
        raise ValueError("Vault KDF memoryCostKiB exceeds maximum allowed value of 2097152.")
    if parallelism > _MAX_VAULT_PARALLELISM:
        raise ValueError("Vault KDF parallelism exceeds maximum allowed value of 8.")
    if key_length != 32:
        raise ValueError("Vault KDF keyLength must be exactly 32.")


def export_database_snapshot(
    db: Session,
    *,
    user_id: int | None = None,
    deks: dict[str, bytes] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    def _scoped(query, model):  # type: ignore[no-untyped-def]
        if user_id is not None and hasattr(model, "user_id"):
            return query.where(model.user_id == user_id)
        return query

    if user_id is not None:
        users = [
            serialize_user_record(u)
            for u in db.scalars(select(User).where(User.id == user_id)).all()
        ]
    else:
        users = [serialize_user_record(u) for u in db.scalars(select(User)).all()]
    user_keys = [
        serialize_user_key_record(user_key)
        for user_key in db.scalars(_scoped(select(UserKey), UserKey)).all()
    ]
    soul_keyslots = [
        serialize_soul_keyslot_record(keyslot)
        for keyslot in db.scalars(select(SoulKeyslot)).all()
    ]
    memory_items = [
        serialize_memory_item_record(item, deks=deks)
        for item in db.scalars(_scoped(select(MemoryItem), MemoryItem)).all()
    ]
    memory_item_evidence = [
        serialize_memory_item_evidence_record(row, deks=deks)
        for row in db.scalars(_scoped(select(MemoryItemEvidence), MemoryItemEvidence)).all()
    ]
    user_profile_fields = [
        serialize_user_profile_field_record(field, deks=deks)
        for field in db.scalars(_scoped(select(UserProfileField), UserProfileField)).all()
    ]
    user_profile_field_evidence = [
        serialize_user_profile_field_evidence_record(evidence, deks=deks)
        for evidence in db.scalars(
            _scoped(select(UserProfileFieldEvidence), UserProfileFieldEvidence)
        ).all()
    ]
    memory_episodes = [
        serialize_memory_episode_record(ep, deks=deks)
        for ep in db.scalars(_scoped(select(MemoryEpisode), MemoryEpisode)).all()
    ]
    kg_entities = [
        serialize_kg_entity_record(entity)
        for entity in db.scalars(_scoped(select(KGEntity), KGEntity)).all()
    ]
    kg_relations = [
        serialize_kg_relation_record(relation)
        for relation in db.scalars(_scoped(select(KGRelation), KGRelation)).all()
    ]
    tasks = [serialize_task_record(task) for task in db.scalars(_scoped(select(Task), Task)).all()]
    agent_threads = [
        serialize_agent_thread_record(t)
        for t in db.scalars(_scoped(select(AgentThread), AgentThread)).all()
    ]
    # Scope runs/steps/messages via user_id on runs, thread_id on steps/messages
    agent_runs = [
        serialize_agent_run_record(r) for r in db.scalars(_scoped(select(AgentRun), AgentRun)).all()
    ]
    # Build thread_id -> user_id map for message decryption
    _thread_user_map: dict[int, int] = {t["id"]: t["user_id"] for t in agent_threads}

    if user_id is not None:
        scoped_thread_ids = [t["id"] for t in agent_threads]
        agent_steps = (
            [
                serialize_agent_step_record(s)
                for s in db.scalars(
                    select(AgentStep).where(AgentStep.thread_id.in_(scoped_thread_ids))
                ).all()
            ]
            if scoped_thread_ids
            else []
        )
        agent_messages = (
            [
                serialize_agent_message_record(m, thread_user_map=_thread_user_map, deks=deks)
                for m in db.scalars(
                    select(AgentMessage).where(AgentMessage.thread_id.in_(scoped_thread_ids))
                ).all()
            ]
            if scoped_thread_ids
            else []
        )
    else:
        agent_steps = [serialize_agent_step_record(s) for s in db.scalars(select(AgentStep)).all()]
        agent_messages = [
            serialize_agent_message_record(m, thread_user_map=_thread_user_map, deks=deks)
            for m in db.scalars(select(AgentMessage)).all()
        ]
    self_model_blocks = [
        serialize_self_model_block_record(b, deks=deks)
        for b in db.scalars(_scoped(select(SelfModelBlock), SelfModelBlock)).all()
    ]
    emotional_signals = [
        serialize_emotional_signal_record(s, deks=deks)
        for s in db.scalars(_scoped(select(EmotionalSignal), EmotionalSignal)).all()
    ]
    foresight_signals = [
        serialize_foresight_signal_record(s, deks=deks)
        for s in db.scalars(_scoped(select(ForesightSignal), ForesightSignal)).all()
    ]
    agent_experiences = [
        serialize_agent_experience_record(e, deks=deks)
        for e in db.scalars(_scoped(select(AgentExperience), AgentExperience)).all()
    ]
    experience_cluster_state = [
        serialize_experience_cluster_state_record(state)
        for state in db.scalars(
            _scoped(select(ExperienceClusterState), ExperienceClusterState)
        ).all()
    ]
    agent_skills = [
        serialize_agent_skill_record(skill, deks=deks)
        for skill in db.scalars(_scoped(select(AgentSkill), AgentSkill)).all()
    ]
    latent_traces = [
        serialize_latent_trace_record(trace)
        for trace in db.scalars(_scoped(select(LatentTrace), LatentTrace)).all()
    ]
    memory_claims = [
        serialize_memory_claim_record(claim, deks=deks)
        for claim in db.scalars(_scoped(select(MemoryClaim), MemoryClaim)).all()
    ]
    tendency_contributions = [
        serialize_tendency_contribution_record(row)
        for row in db.scalars(
            _scoped(select(TendencyContribution), TendencyContribution)
        ).all()
    ]
    reconsolidation_log = [
        serialize_reconsolidation_log_record(row)
        for row in db.scalars(
            _scoped(select(ReconsolidationLog), ReconsolidationLog)
        ).all()
    ]
    initiative_log = [
        serialize_initiative_log_record(row, deks=deks)
        for row in db.scalars(_scoped(select(InitiativeLog), InitiativeLog)).all()
    ]
    dream_journal = [
        serialize_dream_journal_record(row, deks=deks)
        for row in db.scalars(_scoped(select(DreamJournal), DreamJournal)).all()
    ]
    return {
        "users": users,
        "userKeys": user_keys,
        "soulKeyslots": soul_keyslots,
        "memoryItems": memory_items,
        "memoryItemEvidence": memory_item_evidence,
        "userProfileFields": user_profile_fields,
        "userProfileFieldEvidence": user_profile_field_evidence,
        "memoryEpisodes": memory_episodes,
        "kgEntities": kg_entities,
        "kgRelations": kg_relations,
        "tasks": tasks,
        "selfModelBlocks": self_model_blocks,
        "emotionalSignals": emotional_signals,
        "foresightSignals": foresight_signals,
        "agentExperiences": agent_experiences,
        "experienceClusterState": experience_cluster_state,
        "agentSkills": agent_skills,
        "latentTraces": latent_traces,
        "memoryClaims": memory_claims,
        "tendencyContributions": tendency_contributions,
        "reconsolidationLog": reconsolidation_log,
        "initiativeLog": initiative_log,
        "dreamJournal": dream_journal,
        "agentThreads": agent_threads,
        "agentRuns": agent_runs,
        "agentSteps": agent_steps,
        "agentMessages": agent_messages,
    }


def restore_database_snapshot(
    db: Session,
    snapshot: dict[str, Any],
    *,
    scope: str = "full",
) -> None:
    users_payload = snapshot.get("users")
    user_keys_payload = snapshot.get("userKeys")
    if not isinstance(users_payload, list) or not isinstance(user_keys_payload, list):
        raise ValueError("Vault database snapshot is missing users or userKeys.")
    soul_keyslots_payload = snapshot.get("soulKeyslots", [])
    if not isinstance(soul_keyslots_payload, list):
        raise ValueError("Vault Soul keyslot snapshot is invalid.")
    restores_soul_keyslots = "soulKeyslots" in snapshot

    memory_items_payload = snapshot.get("memoryItems", [])
    memory_item_evidence_payload = snapshot.get("memoryItemEvidence", [])
    user_profile_fields_payload = snapshot.get("userProfileFields", [])
    user_profile_field_evidence_payload = snapshot.get("userProfileFieldEvidence", [])
    memory_episodes_payload = snapshot.get("memoryEpisodes", [])
    kg_entities_payload = snapshot.get("kgEntities", [])
    kg_relations_payload = snapshot.get("kgRelations", [])
    tasks_payload = snapshot.get("tasks", [])
    self_model_blocks_payload = snapshot.get("selfModelBlocks", [])
    emotional_signals_payload = snapshot.get("emotionalSignals", [])
    foresight_signals_payload = snapshot.get("foresightSignals", [])
    agent_experiences_payload = snapshot.get("agentExperiences", [])
    experience_cluster_state_payload = snapshot.get("experienceClusterState", [])
    agent_skills_payload = snapshot.get("agentSkills", [])
    latent_traces_payload = snapshot.get("latentTraces", [])
    memory_claims_payload = snapshot.get("memoryClaims", [])
    tendency_contributions_payload = snapshot.get("tendencyContributions", [])
    reconsolidation_log_payload = snapshot.get("reconsolidationLog", [])
    initiative_log_payload = snapshot.get("initiativeLog", [])
    dream_journal_payload = snapshot.get("dreamJournal", [])
    agent_threads_payload = snapshot.get("agentThreads", [])
    agent_runs_payload = snapshot.get("agentRuns", [])
    agent_steps_payload = snapshot.get("agentSteps", [])
    agent_messages_payload = snapshot.get("agentMessages", [])

    # For memories-only imports, only clear tables that were exported
    is_full = scope == "full"

    try:
        # IL6: like TendencyContribution, this must go before MemoryItem
        # (bulk deletes bypass ORM cascade and SQLite FKs are not enforced,
        # so a later-deleted memory_items row would leave orphaned log rows
        # referencing it, or a reused id could reattach to a stale log).
        db.query(ReconsolidationLog).delete()
        db.query(TendencyContribution).delete()
        # IL3 provenance log: same reused-id/stale-row hazard as the other
        # provenance ledgers above — without this, restoring into a DB that
        # already has initiative_log rows either raises
        # "UNIQUE constraint failed: initiative_log.id" (id collision) or
        # leaves stale rows behind (no FK to scrub them via cascade).
        db.query(InitiativeLog).delete()
        # IL7 dream journal: same reused-id/stale-row hazard as the ledgers above.
        db.query(DreamJournal).delete()
        # Bulk deletes bypass ORM cascade and SQLite FKs are not enforced
        # (no foreign_keys pragma), so claim evidence must be deleted
        # explicitly BEFORE its claims — mirroring MemoryItemEvidence and
        # UserProfileFieldEvidence below. Otherwise stale encrypted
        # evidence survives import and can reattach to reused claim ids.
        db.query(MemoryClaimEvidence).delete()
        db.query(MemoryClaim).delete()
        db.query(LatentTrace).delete()
        db.query(AgentSkill).delete()
        db.query(ExperienceClusterState).delete()
        db.query(AgentExperience).delete()
        db.query(ForesightSignal).delete()
        db.query(EmotionalSignal).delete()
        db.query(SelfModelBlock).delete()
        db.query(KGRelation).delete()
        db.query(KGEntity).delete()
        db.query(UserProfileFieldEvidence).delete()
        db.query(UserProfileField).delete()
        db.query(MemoryItemEvidence).delete()
        if is_full:
            db.query(AgentStep).delete()
            db.query(AgentMessage).delete()
            db.query(AgentRun).delete()
            db.query(AgentThread).delete()
            db.query(Task).delete()
        db.query(MemoryEpisode).delete()
        db.query(MemoryItem).delete()
        if restores_soul_keyslots:
            db.query(SoulKeyslot).delete()
        db.query(UserKey).delete()
        db.query(User).delete()

        for record in users_payload:
            if not isinstance(record, dict):
                raise ValueError("Vault user record is invalid.")
            db.add(
                User(
                    id=int(record["id"]),
                    username=str(record["username"]),
                    password_hash=str(record["password_hash"]),
                    display_name=str(record["display_name"]),
                    gender=coerce_optional_str(record.get("gender")),
                    age=coerce_optional_int(record.get("age")),
                    birthday=coerce_optional_str(record.get("birthday")),
                    created_at=parse_optional_datetime(record.get("created_at")),
                    updated_at=parse_optional_datetime(record.get("updated_at")),
                )
            )

        for record in user_keys_payload:
            if not isinstance(record, dict):
                raise ValueError("Vault user key record is invalid.")
            db.add(
                UserKey(
                    id=int(record["id"]),
                    user_id=int(record["user_id"]),
                    domain=str(record.get("domain", "memories")),
                    kdf_salt=str(record["kdf_salt"]),
                    kdf_time_cost=int(record["kdf_time_cost"]),
                    kdf_memory_cost_kib=int(record["kdf_memory_cost_kib"]),
                    kdf_parallelism=int(record["kdf_parallelism"]),
                    kdf_key_length=int(record["kdf_key_length"]),
                    wrap_iv=str(record["wrap_iv"]),
                    wrap_tag=str(record["wrap_tag"]),
                    wrapped_dek=str(record["wrapped_dek"]),
                    created_at=parse_optional_datetime(record.get("created_at")),
                    updated_at=parse_optional_datetime(record.get("updated_at")),
                )
            )

        for record in soul_keyslots_payload:
            if not isinstance(record, dict):
                raise ValueError("Vault Soul keyslot record is invalid.")
            db.add(
                SoulKeyslot(
                    id=int(record["id"]),
                    owner_id=str(record["owner_id"]),
                    domain=str(record["domain"]),
                    wrapping_path=str(record["wrapping_path"]),
                    key_version=int(record["key_version"]),
                    credential_generation=int(record["credential_generation"]),
                    status=str(record["status"]),
                    kdf_algorithm=str(record["kdf_algorithm"]),
                    wrap_algorithm=str(record["wrap_algorithm"]),
                    envelope_version=int(record["envelope_version"]),
                    kdf_salt=str(record["kdf_salt"]),
                    kdf_time_cost=int(record["kdf_time_cost"]),
                    kdf_memory_cost_kib=int(record["kdf_memory_cost_kib"]),
                    kdf_parallelism=int(record["kdf_parallelism"]),
                    kdf_key_length=int(record["kdf_key_length"]),
                    wrap_iv=str(record["wrap_iv"]),
                    wrap_tag=str(record["wrap_tag"]),
                    wrapped_dek=str(record["wrapped_dek"]),
                    created_at=parse_optional_datetime(record.get("created_at")),
                    updated_at=parse_optional_datetime(record.get("updated_at")),
                )
            )

        for record in foresight_signals_payload:
            if not isinstance(record, dict):
                continue
            db.add(
                ForesightSignal(
                    id=int(record["id"]),
                    user_id=int(record["user_id"]),
                    content=str(record["content"]),
                    evidence=str(record["evidence"]),
                    relative_text=coerce_optional_str(record.get("relative_text")),
                    start_date=parse_optional_date(record.get("start_date")),
                    end_date=parse_optional_date(record.get("end_date")),
                    duration_days=coerce_optional_int(record.get("duration_days")),
                    status=str(record.get("status", "active")),
                    confidence=float(record.get("confidence", 0.8)),
                    source_thread_id=coerce_optional_int(record.get("source_thread_id")),
                    source_message_ids_json=record.get("source_message_ids_json"),
                    observed_at=parse_optional_datetime(record.get("observed_at")),
                    last_seen_at=parse_optional_datetime(record.get("last_seen_at")),
                    created_at=parse_optional_datetime(record.get("created_at")),
                    updated_at=parse_optional_datetime(record.get("updated_at")),
                )
            )

        restored_experience_ids: set[int] = set()
        experience_superseded_links: list[tuple[int, int]] = []
        for record in agent_experiences_payload:
            if not isinstance(record, dict):
                continue
            experience_id = int(record["id"])
            superseded_by = coerce_optional_int(record.get("superseded_by"))
            restored_experience_ids.add(experience_id)
            if superseded_by is not None:
                experience_superseded_links.append((experience_id, superseded_by))
            db.add(
                AgentExperience(
                    id=experience_id,
                    user_id=int(record["user_id"]),
                    task_intent=str(record["task_intent"]),
                    approach=str(record["approach"]),
                    quality_score=float(record.get("quality_score", 0.5)),
                    source_thread_id=coerce_optional_int(record.get("source_thread_id")),
                    source_run_id=coerce_optional_int(record.get("source_run_id")),
                    tool_names_json=record.get("tool_names_json"),
                    turn_count=int(record.get("turn_count", 1)),
                    embedding_json=record.get("embedding_json"),
                    cluster_id=coerce_optional_str(record.get("cluster_id")),
                    superseded_by=None,
                    created_at=parse_optional_datetime(record.get("created_at")),
                    updated_at=parse_optional_datetime(record.get("updated_at")),
                )
            )

        db.flush()

        for experience_id, superseded_by in experience_superseded_links:
            if superseded_by not in restored_experience_ids:
                continue
            experience = db.get(AgentExperience, experience_id)
            if experience is not None:
                experience.superseded_by = superseded_by

        for record in experience_cluster_state_payload:
            if not isinstance(record, dict):
                continue
            db.add(
                ExperienceClusterState(
                    id=int(record["id"]),
                    user_id=int(record["user_id"]),
                    state_json=record.get("state_json", {}),
                    created_at=parse_optional_datetime(record.get("created_at")),
                    updated_at=parse_optional_datetime(record.get("updated_at")),
                )
            )

        restored_skill_ids: set[int] = set()
        skill_superseded_links: list[tuple[int, int]] = []
        for record in agent_skills_payload:
            if not isinstance(record, dict):
                continue
            skill_id = int(record["id"])
            superseded_by = coerce_optional_int(record.get("superseded_by"))
            restored_skill_ids.add(skill_id)
            if superseded_by is not None:
                skill_superseded_links.append((skill_id, superseded_by))
            db.add(
                AgentSkill(
                    id=skill_id,
                    user_id=int(record["user_id"]),
                    cluster_id=str(record["cluster_id"]),
                    name=str(record["name"]),
                    description=str(record["description"]),
                    content=str(record["content"]),
                    confidence=float(record.get("confidence", 0.5)),
                    experience_count=int(record.get("experience_count", 0)),
                    last_refined_at=parse_optional_datetime(record.get("last_refined_at")),
                    embedding_json=record.get("embedding_json"),
                    superseded_by=None,
                    created_at=parse_optional_datetime(record.get("created_at")),
                    updated_at=parse_optional_datetime(record.get("updated_at")),
                )
            )

        db.flush()

        for skill_id, superseded_by in skill_superseded_links:
            if superseded_by not in restored_skill_ids:
                continue
            skill = db.get(AgentSkill, skill_id)
            if skill is not None:
                skill.superseded_by = superseded_by

        for record in latent_traces_payload:
            if not isinstance(record, dict):
                continue
            db.add(
                LatentTrace(
                    id=int(record["id"]),
                    user_id=int(record["user_id"]),
                    topic_key=str(record["topic_key"]),
                    kind=str(record.get("kind", "observation")),
                    weight=float(record.get("weight", 0.0)),
                    evidence_refs=record.get("evidence_refs"),
                    first_seen=parse_optional_datetime(record.get("first_seen")),
                    last_seen=parse_optional_datetime(record.get("last_seen")),
                )
            )

        for record in memory_items_payload:
            if not isinstance(record, dict):
                continue
            db.add(
                MemoryItem(
                    id=int(record["id"]),
                    user_id=int(record["user_id"]),
                    content=str(record["content"]),
                    category=str(record["category"]),
                    importance=int(record.get("importance", 3)),
                    source=str(record.get("source", "extraction")),
                    superseded_by=coerce_optional_int(record.get("superseded_by")),
                    reference_count=int(record.get("reference_count", 0)),
                    last_referenced_at=parse_optional_datetime(record.get("last_referenced_at")),
                    embedding_json=record.get("embedding_json"),
                    embedding_checksum=coerce_optional_str(record.get("embedding_checksum")),
                    memory_class=str(record.get("memory_class", "casual")),
                    emotional_salience=float(record.get("emotional_salience") or 0.0),
                    stability_class=str(record.get("stability_class", "stable")),
                    decay_class=str(record.get("decay_class", "standard")),
                    relationship_proximity=float(record.get("relationship_proximity") or 0.0),
                    evidence_strength=float(
                        0.8
                        if record.get("evidence_strength") is None
                        else record.get("evidence_strength")
                    ),
                    evolves_from_item_id=coerce_optional_int(record.get("evolves_from_item_id")),
                    evolution_kind=coerce_optional_str(record.get("evolution_kind")),
                    distilled_at=parse_optional_datetime(record.get("distilled_at")),
                    reconsolidation_drift=float(record.get("reconsolidation_drift") or 0.0),
                    created_at=parse_optional_datetime(record.get("created_at")),
                    updated_at=parse_optional_datetime(record.get("updated_at")),
                )
            )

        db.flush()

        for record in memory_item_evidence_payload:
            if not isinstance(record, dict):
                continue
            db.add(
                MemoryItemEvidence(
                    id=int(record["id"]),
                    user_id=int(record["user_id"]),
                    memory_item_id=int(record["memory_item_id"]),
                    source_kind=str(record.get("source_kind", "legacy_backfill")),
                    runtime_thread_id=coerce_optional_int(record.get("runtime_thread_id")),
                    runtime_message_id=coerce_optional_int(record.get("runtime_message_id")),
                    runtime_message_ids_json=record.get("runtime_message_ids_json"),
                    transcript_ref=coerce_optional_str(record.get("transcript_ref")),
                    sequence_id=coerce_optional_int(record.get("sequence_id")),
                    speaker=coerce_optional_str(record.get("speaker")),
                    observed_at=parse_optional_datetime(record.get("observed_at")),
                    source_created_at=parse_optional_datetime(record.get("source_created_at")),
                    confidence=float(record.get("confidence", 1.0)),
                    extractor=coerce_optional_str(record.get("extractor")),
                    evidence_text=str(record.get("evidence_text", "")),
                    metadata_json=record.get("metadata_json"),
                    created_at=parse_optional_datetime(record.get("created_at")),
                )
            )

        # IL5: memory_claims (all namespaces, not just "tendency") are
        # soul-store and now ride along in vault export/import — needed so
        # a distilled item's tendency claim (and its aggregate strength)
        # survive a wipe+import, which right-to-forget for already-distilled
        # items depends on (PRD §5). Claim EVIDENCE is still not part of
        # vault snapshots (unrelated to IL5 — tendency claims never have
        # evidence rows, see ``claims.upsert_tendency_claim``), so those FKs
        # cannot be restored safely for other claim kinds; superseded-claim
        # self-links are backfilled the same way profile fields are below.
        restored_claim_ids: set[int] = set()
        claim_superseded_links: list[tuple[int, int]] = []
        for record in memory_claims_payload:
            if not isinstance(record, dict):
                continue
            claim_id = int(record["id"])
            superseded_by_id = coerce_optional_int(record.get("superseded_by_id"))
            restored_claim_ids.add(claim_id)
            if superseded_by_id is not None:
                claim_superseded_links.append((claim_id, superseded_by_id))
            db.add(
                MemoryClaim(
                    id=claim_id,
                    user_id=int(record["user_id"]),
                    subject_type=str(record.get("subject_type", "user")),
                    namespace=str(record["namespace"]),
                    slot=str(record["slot"]),
                    # Value is already in its final form here: authenticated
                    # user-scoped imports re-encrypted it in
                    # _re_encrypt_snapshot_fields; full snapshots (no user_id)
                    # carry field values as-is, matching how memoryItems and
                    # profile fields restore. Re-encrypting inline would
                    # double-encrypt a full-restore claim.
                    value_text=str(record.get("value_text") or ""),
                    value_json=record.get("value_json"),
                    polarity=str(record.get("polarity", "positive")),
                    confidence=float(record.get("confidence", 0.8)),
                    status=str(record.get("status", "active")),
                    canonical_key=str(record["canonical_key"]),
                    source_kind=str(record.get("source_kind", "extraction")),
                    extractor=str(record.get("extractor", "regex")),
                    memory_item_id=coerce_optional_int(record.get("memory_item_id")),
                    superseded_by_id=None,
                    created_at=parse_optional_datetime(record.get("created_at")),
                    updated_at=parse_optional_datetime(record.get("updated_at")),
                )
            )

        db.flush()

        for claim_id, superseded_by_id in claim_superseded_links:
            if superseded_by_id not in restored_claim_ids:
                continue
            claim = db.get(MemoryClaim, claim_id)
            if claim is not None:
                claim.superseded_by_id = superseded_by_id

        for record in tendency_contributions_payload:
            if not isinstance(record, dict):
                continue
            db.add(
                TendencyContribution(
                    id=int(record["id"]),
                    user_id=int(record["user_id"]),
                    tombstone_item_id=int(record["tombstone_item_id"]),
                    tendency_claim_id=int(record["tendency_claim_id"]),
                    contribution_vector=record.get("contribution_vector") or {},
                    created_at=parse_optional_datetime(record.get("created_at")),
                )
            )

        for record in reconsolidation_log_payload:
            if not isinstance(record, dict):
                continue
            db.add(
                ReconsolidationLog(
                    id=int(record["id"]),
                    user_id=int(record["user_id"]),
                    memory_item_id=int(record["memory_item_id"]),
                    applied_at=parse_optional_datetime(record.get("applied_at")),
                    field=str(record["field"]),
                    old_value=float(record.get("old_value") or 0.0),
                    new_value=float(record.get("new_value") or 0.0),
                    eta=float(record.get("eta") or 0.0),
                )
            )

        for record in initiative_log_payload:
            if not isinstance(record, dict):
                continue
            db.add(
                InitiativeLog(
                    id=int(record["id"]),
                    user_id=int(record["user_id"]),
                    fired_at=parse_optional_datetime(record.get("fired_at")),
                    drive=str(record["drive"]),
                    pressure_snapshot=record.get("pressure_snapshot") or {},
                    gate_states=record.get("gate_states") or {},
                    generated_text=coerce_optional_str(record.get("generated_text")),
                    delivered=bool(record.get("delivered", False)),
                    answered=bool(record.get("answered", False)),
                    created_at=parse_optional_datetime(record.get("created_at")),
                )
            )

        for record in dream_journal_payload:
            if not isinstance(record, dict):
                continue
            db.add(
                DreamJournal(
                    id=int(record["id"]),
                    user_id=int(record["user_id"]),
                    dreamt_at=parse_optional_datetime(record.get("dreamt_at")),
                    narrative=str(record.get("narrative") or ""),
                    source_refs=record.get("source_refs") or {},
                    affect_delta=record.get("affect_delta") or {},
                    share_worthy=bool(record.get("share_worthy", False)),
                    surfaced=bool(record.get("surfaced", False)),
                    created_at=parse_optional_datetime(record.get("created_at")),
                )
            )

        db.flush()

        restored_profile_field_ids: set[int] = set()
        profile_superseded_links: list[tuple[int, int]] = []
        for record in user_profile_fields_payload:
            if not isinstance(record, dict):
                continue
            profile_field_id = int(record["id"])
            superseded_by_id = coerce_optional_int(record.get("superseded_by_id"))
            restored_profile_field_ids.add(profile_field_id)
            if superseded_by_id is not None:
                profile_superseded_links.append((profile_field_id, superseded_by_id))
            db.add(
                UserProfileField(
                    id=profile_field_id,
                    user_id=int(record["user_id"]),
                    category=str(record["category"]),
                    key=str(record["key"]),
                    value_text=str(record["value_text"]),
                    confidence=float(record.get("confidence", 0.8)),
                    status=str(record.get("status", "active")),
                    source_kind=str(record.get("source_kind", "extraction")),
                    source_memory_id=coerce_optional_int(record.get("source_memory_id")),
                    source_evidence_id=coerce_optional_int(record.get("source_evidence_id")),
                    source_claim_evidence_id=None,
                    superseded_by_id=None,
                    first_observed_at=parse_optional_datetime(
                        record.get("first_observed_at")
                    ),
                    last_observed_at=parse_optional_datetime(record.get("last_observed_at")),
                    created_at=parse_optional_datetime(record.get("created_at")),
                    updated_at=parse_optional_datetime(record.get("updated_at")),
                )
            )

        db.flush()

        # Claim evidence is not part of vault snapshots, so those FKs cannot be
        # restored safely. Profile self-links can be backfilled once every
        # profile row from the snapshot exists.
        for profile_field_id, superseded_by_id in profile_superseded_links:
            if superseded_by_id not in restored_profile_field_ids:
                continue
            profile_field = db.get(UserProfileField, profile_field_id)
            if profile_field is not None:
                profile_field.superseded_by_id = superseded_by_id

        db.flush()

        for record in user_profile_field_evidence_payload:
            if not isinstance(record, dict):
                continue
            db.add(
                UserProfileFieldEvidence(
                    id=int(record["id"]),
                    profile_field_id=int(record["profile_field_id"]),
                    user_id=int(record["user_id"]),
                    source_kind=str(record.get("source_kind", "extraction")),
                    source_memory_id=coerce_optional_int(record.get("source_memory_id")),
                    source_evidence_id=coerce_optional_int(record.get("source_evidence_id")),
                    source_claim_evidence_id=None,
                    runtime_thread_id=coerce_optional_int(record.get("runtime_thread_id")),
                    runtime_message_id=coerce_optional_int(record.get("runtime_message_id")),
                    evidence_text=str(record.get("evidence_text", "")),
                    observed_at=parse_optional_datetime(record.get("observed_at")),
                    created_at=parse_optional_datetime(record.get("created_at")),
                )
            )

        for record in kg_entities_payload:
            if not isinstance(record, dict):
                continue
            db.add(
                KGEntity(
                    id=int(record["id"]),
                    user_id=int(record["user_id"]),
                    name=str(record["name"]),
                    name_normalized=str(record["name_normalized"]),
                    entity_type=str(record.get("entity_type", "unknown")),
                    description=str(record.get("description", "")),
                    mentions=int(record.get("mentions", 1)),
                    aliases_json=record.get("aliases_json"),
                    embedding_json=record.get("embedding_json"),
                    embedding_checksum=coerce_optional_str(record.get("embedding_checksum")),
                    created_at=parse_optional_datetime(record.get("created_at")),
                    updated_at=parse_optional_datetime(record.get("updated_at")),
                )
            )

        db.flush()

        kg_relation_self_links: list[tuple[int, int | None, int | None]] = []
        restored_kg_relation_ids: set[int] = set()
        for record in kg_relations_payload:
            if not isinstance(record, dict):
                continue
            relation_id = int(record["id"])
            supersedes_relation_id = coerce_optional_int(
                record.get("supersedes_relation_id")
            )
            evolves_from_relation_id = coerce_optional_int(
                record.get("evolves_from_relation_id")
            )
            restored_kg_relation_ids.add(relation_id)
            kg_relation_self_links.append(
                (relation_id, supersedes_relation_id, evolves_from_relation_id)
            )
            db.add(
                KGRelation(
                    id=relation_id,
                    user_id=int(record["user_id"]),
                    source_id=int(record["source_id"]),
                    destination_id=int(record["destination_id"]),
                    relation_type=str(record["relation_type"]),
                    mentions=int(record.get("mentions", 1)),
                    source_memory_id=coerce_optional_int(record.get("source_memory_id")),
                    evidence_id=coerce_optional_int(record.get("evidence_id")),
                    observed_at=parse_optional_datetime(record.get("observed_at")),
                    valid_from=parse_optional_datetime(record.get("valid_from")),
                    valid_to=parse_optional_datetime(record.get("valid_to")),
                    confidence=float(
                        record.get("confidence")
                        if record.get("confidence") is not None
                        else 1.0
                    ),
                    status=str(record.get("status") or "active"),
                    supersedes_relation_id=None,
                    evolves_from_relation_id=None,
                    created_at=parse_optional_datetime(record.get("created_at")),
                    updated_at=parse_optional_datetime(record.get("updated_at")),
                )
            )

        db.flush()

        for relation_id, supersedes_relation_id, evolves_from_relation_id in (
            kg_relation_self_links
        ):
            relation = db.get(KGRelation, relation_id)
            if relation is None:
                continue
            if supersedes_relation_id in restored_kg_relation_ids:
                relation.supersedes_relation_id = supersedes_relation_id
            if evolves_from_relation_id in restored_kg_relation_ids:
                relation.evolves_from_relation_id = evolves_from_relation_id

        for record in memory_episodes_payload:
            if not isinstance(record, dict):
                continue
            db.add(
                MemoryEpisode(
                    id=int(record["id"]),
                    user_id=int(record["user_id"]),
                    thread_id=coerce_optional_int(record.get("thread_id")),
                    date=str(record["date"]),
                    time=coerce_optional_str(record.get("time")),
                    topics_json=record.get("topics_json"),
                    summary=str(record["summary"]),
                    emotional_arc=coerce_optional_str(record.get("emotional_arc")),
                    significance_score=int(record.get("significance_score", 3)),
                    turn_count=coerce_optional_int(record.get("turn_count")),
                    created_at=parse_optional_datetime(record.get("created_at")),
                )
            )

        for record in tasks_payload:
            if not isinstance(record, dict):
                continue
            db.add(
                Task(
                    id=int(record["id"]),
                    user_id=int(record["user_id"]),
                    text=str(record["text"]),
                    done=bool(record.get("done", False)),
                    priority=int(record.get("priority", 2)),
                    due_date=coerce_optional_str(record.get("due_date")),
                    completed_at=parse_optional_datetime(record.get("completed_at")),
                    created_at=parse_optional_datetime(record.get("created_at")),
                    updated_at=parse_optional_datetime(record.get("updated_at")),
                )
            )

        for record in agent_threads_payload:
            if not isinstance(record, dict):
                continue
            db.add(
                AgentThread(
                    id=int(record["id"]),
                    user_id=int(record["user_id"]),
                    status=str(record.get("status", "active")),
                    title=coerce_optional_str(record.get("title")),
                    created_at=parse_optional_datetime(record.get("created_at")),
                    updated_at=parse_optional_datetime(record.get("updated_at")),
                    last_message_at=parse_optional_datetime(record.get("last_message_at")),
                    next_message_sequence=int(record.get("next_message_sequence", 1)),
                )
            )

        db.flush()

        for record in self_model_blocks_payload:
            if not isinstance(record, dict):
                continue
            db.add(
                SelfModelBlock(
                    id=int(record["id"]),
                    user_id=int(record["user_id"]),
                    section=str(record["section"]),
                    content=str(record.get("content", "")),
                    version=int(record.get("version", 1)),
                    updated_by=str(record.get("updated_by", "system")),
                    metadata_json=record.get("metadata_json"),
                    created_at=parse_optional_datetime(record.get("created_at")),
                    updated_at=parse_optional_datetime(record.get("updated_at")),
                )
            )

        for record in emotional_signals_payload:
            if not isinstance(record, dict):
                continue
            db.add(
                EmotionalSignal(
                    id=int(record["id"]),
                    user_id=int(record["user_id"]),
                    thread_id=coerce_optional_int(record.get("thread_id")),
                    emotion=str(record["emotion"]),
                    confidence=float(record.get("confidence", 0.5)),
                    evidence_type=str(record.get("evidence_type", "linguistic")),
                    evidence=str(record.get("evidence", "")),
                    trajectory=str(record.get("trajectory", "stable")),
                    previous_emotion=coerce_optional_str(record.get("previous_emotion")),
                    topic=str(record.get("topic", "")),
                    acted_on=bool(record.get("acted_on", False)),
                    created_at=parse_optional_datetime(record.get("created_at")),
                )
            )

        for record in agent_runs_payload:
            if not isinstance(record, dict):
                continue
            db.add(
                AgentRun(
                    id=int(record["id"]),
                    thread_id=int(record["thread_id"]),
                    user_id=int(record["user_id"]),
                    provider=str(record["provider"]),
                    model=str(record["model"]),
                    mode=str(record.get("mode", "chat")),
                    status=str(record.get("status", "completed")),
                    stop_reason=coerce_optional_str(record.get("stop_reason")),
                    error_text=coerce_optional_str(record.get("error_text")),
                    started_at=parse_optional_datetime(record.get("started_at")),
                    completed_at=parse_optional_datetime(record.get("completed_at")),
                    prompt_tokens=coerce_optional_int(record.get("prompt_tokens")),
                    completion_tokens=coerce_optional_int(record.get("completion_tokens")),
                    total_tokens=coerce_optional_int(record.get("total_tokens")),
                )
            )

        db.flush()

        for record in agent_steps_payload:
            if not isinstance(record, dict):
                continue
            db.add(
                AgentStep(
                    id=int(record["id"]),
                    run_id=int(record["run_id"]),
                    thread_id=int(record["thread_id"]),
                    step_index=int(record["step_index"]),
                    status=str(record["status"]),
                    request_json=record.get("request_json", {}),
                    response_json=record.get("response_json", {}),
                    tool_calls_json=record.get("tool_calls_json"),
                    usage_json=record.get("usage_json"),
                    error_text=coerce_optional_str(record.get("error_text")),
                    created_at=parse_optional_datetime(record.get("created_at")),
                )
            )

        for record in agent_messages_payload:
            if not isinstance(record, dict):
                continue
            db.add(
                AgentMessage(
                    id=int(record["id"]),
                    thread_id=int(record["thread_id"]),
                    run_id=coerce_optional_int(record.get("run_id")),
                    step_id=coerce_optional_int(record.get("step_id")),
                    sequence_id=int(record["sequence_id"]),
                    role=str(record["role"]),
                    content_text=coerce_optional_str(record.get("content_text")),
                    content_json=record.get("content_json"),
                    tool_name=coerce_optional_str(record.get("tool_name")),
                    tool_call_id=coerce_optional_str(record.get("tool_call_id")),
                    tool_args_json=record.get("tool_args_json"),
                    is_in_context=bool(record.get("is_in_context", True)),
                    token_estimate=coerce_optional_int(record.get("token_estimate")),
                    created_at=parse_optional_datetime(record.get("created_at")),
                )
            )

        db.flush()
        sync_agent_thread_sequence_counters(db)
        reset_identity_sequences(db)
        db.commit()
    except Exception:
        db.rollback()
        raise


def read_data_snapshot(*, user_id: int | None = None) -> dict[str, str]:
    root = settings.data_dir
    if not root.exists():
        return {}

    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative_path = path.relative_to(root)
        if relative_path.name in {"anima.db", "anima.db-shm", "anima.db-wal"}:
            continue
        if relative_path.parts and relative_path.parts[0] == "chroma":
            continue  # skip legacy chroma directory if present
        if relative_path.parts and relative_path.parts[0] == "runtime":
            continue  # skip ephemeral runtime data (embedded PG, etc.)
        # Scope to user directory if user_id is set (files are stored under users/{id}/)
        if user_id is not None and relative_path.parts:
            if relative_path.parts[0] == "users" and len(relative_path.parts) > 1:
                if relative_path.parts[1] != str(user_id):
                    continue
            elif relative_path.parts[0] == "users":
                continue
        snapshot[relative_path.as_posix()] = path.read_text(encoding="utf-8")
    return snapshot


def write_data_snapshot(user_files: dict[str, Any], *, user_id: int | None = None) -> None:
    # NOTE: This restores legacy file-based user data from older vault exports.
    # All personal data now lives inside the encrypted SQLite database.
    # These files are written for backwards compatibility only — no runtime code
    # reads them.  A future version should stop writing them entirely and
    # document the filesystem boundary as sealed (see portable-core thesis §5.3).
    root = settings.data_dir
    if user_id is None:
        root.parent.mkdir(parents=True, exist_ok=True)
        staging_root = root.parent / f"{root.name}.import-{uuid4().hex}"
        backup_root = root.parent / f"{root.name}.backup-{uuid4().hex}"
        staging_root.mkdir(parents=True, exist_ok=True)

        try:
            for relative_path, content in user_files.items():
                safe_path = sanitize_relative_path(relative_path)
                if safe_path.parts and safe_path.parts[0] == "chroma":
                    continue
                target = staging_root / safe_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(content), encoding="utf-8")

            if backup_root.exists():
                shutil.rmtree(backup_root, ignore_errors=True)
            if root.exists():
                root.rename(backup_root)
            staging_root.rename(root)
            shutil.rmtree(backup_root, ignore_errors=True)
        except Exception:
            shutil.rmtree(staging_root, ignore_errors=True)
            if backup_root.exists() and not root.exists():
                backup_root.rename(root)
            raise
        return

    user_root = root / "users" / str(user_id)
    user_root.parent.mkdir(parents=True, exist_ok=True)
    user_root.mkdir(parents=True, exist_ok=True)

    try:
        for existing in list(user_root.iterdir()):
            if existing.name in {"anima.db", "anima.db-shm", "anima.db-wal"}:
                continue
            if existing.is_dir():
                shutil.rmtree(existing, ignore_errors=True)
            else:
                existing.unlink(missing_ok=True)

        for relative_path, content in user_files.items():
            safe_path = sanitize_relative_path(relative_path)
            if safe_path.parts and safe_path.parts[0] == "chroma":
                continue
            if safe_path.parts[:2] != ("users", str(user_id)):
                continue
            local_relative = Path(*safe_path.parts[2:]) if len(safe_path.parts) > 2 else Path()
            if not local_relative.parts:
                continue
            target = user_root / local_relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(content), encoding="utf-8")
    except Exception:
        raise


def sanitize_relative_path(raw_path: Any) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("Vault file path is invalid.")
    normalized = PurePosixPath(raw_path)
    if normalized.is_absolute() or ".." in normalized.parts:
        raise ValueError("Vault file path is invalid.")
    return Path(*normalized.parts)


def serialize_user_record(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "password_hash": user.password_hash,
        "display_name": user.display_name,
        "gender": user.gender,
        "age": user.age,
        "birthday": user.birthday,
        "created_at": serialize_optional_datetime(user.created_at),
        "updated_at": serialize_optional_datetime(user.updated_at),
    }


def serialize_user_key_record(user_key: UserKey) -> dict[str, Any]:
    return {
        "id": user_key.id,
        "user_id": user_key.user_id,
        "domain": user_key.domain,
        "kdf_salt": user_key.kdf_salt,
        "kdf_time_cost": user_key.kdf_time_cost,
        "kdf_memory_cost_kib": user_key.kdf_memory_cost_kib,
        "kdf_parallelism": user_key.kdf_parallelism,
        "kdf_key_length": user_key.kdf_key_length,
        "wrap_iv": user_key.wrap_iv,
        "wrap_tag": user_key.wrap_tag,
        "wrapped_dek": user_key.wrapped_dek,
        "created_at": serialize_optional_datetime(user_key.created_at),
        "updated_at": serialize_optional_datetime(user_key.updated_at),
    }


def serialize_soul_keyslot_record(keyslot: SoulKeyslot) -> dict[str, Any]:
    return {
        "id": keyslot.id,
        "owner_id": keyslot.owner_id,
        "domain": keyslot.domain,
        "wrapping_path": keyslot.wrapping_path,
        "key_version": keyslot.key_version,
        "credential_generation": keyslot.credential_generation,
        "status": keyslot.status,
        "kdf_algorithm": keyslot.kdf_algorithm,
        "wrap_algorithm": keyslot.wrap_algorithm,
        "envelope_version": keyslot.envelope_version,
        "kdf_salt": keyslot.kdf_salt,
        "kdf_time_cost": keyslot.kdf_time_cost,
        "kdf_memory_cost_kib": keyslot.kdf_memory_cost_kib,
        "kdf_parallelism": keyslot.kdf_parallelism,
        "kdf_key_length": keyslot.kdf_key_length,
        "wrap_iv": keyslot.wrap_iv,
        "wrap_tag": keyslot.wrap_tag,
        "wrapped_dek": keyslot.wrapped_dek,
        "created_at": serialize_optional_datetime(keyslot.created_at),
        "updated_at": serialize_optional_datetime(keyslot.updated_at),
    }


def serialize_memory_item_record(
    item: MemoryItem,
    *,
    deks: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    return {
        "id": item.id,
        "user_id": item.user_id,
        "content": _decrypt_field_value(
            item.content,
            deks,
            table="memory_items",
            field="content",
            user_id=item.user_id,
        ),
        "category": item.category,
        "importance": item.importance,
        "source": item.source,
        "superseded_by": item.superseded_by,
        "reference_count": item.reference_count,
        "last_referenced_at": serialize_optional_datetime(item.last_referenced_at),
        "embedding_json": item.embedding_json,
        "embedding_checksum": item.embedding_checksum,
        "memory_class": item.memory_class,
        "emotional_salience": item.emotional_salience,
        "stability_class": item.stability_class,
        "decay_class": item.decay_class,
        "relationship_proximity": item.relationship_proximity,
        "evidence_strength": item.evidence_strength,
        "evolves_from_item_id": item.evolves_from_item_id,
        "evolution_kind": item.evolution_kind,
        "distilled_at": serialize_optional_datetime(item.distilled_at),
        "reconsolidation_drift": item.reconsolidation_drift,
        "created_at": serialize_optional_datetime(item.created_at),
        "updated_at": serialize_optional_datetime(item.updated_at),
    }


def serialize_memory_item_evidence_record(
    evidence: MemoryItemEvidence,
    *,
    deks: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    return {
        "id": evidence.id,
        "user_id": evidence.user_id,
        "memory_item_id": evidence.memory_item_id,
        "source_kind": evidence.source_kind,
        "runtime_thread_id": evidence.runtime_thread_id,
        "runtime_message_id": evidence.runtime_message_id,
        "runtime_message_ids_json": evidence.runtime_message_ids_json,
        "transcript_ref": evidence.transcript_ref,
        "sequence_id": evidence.sequence_id,
        "speaker": evidence.speaker,
        "observed_at": serialize_optional_datetime(evidence.observed_at),
        "source_created_at": serialize_optional_datetime(evidence.source_created_at),
        "confidence": evidence.confidence,
        "extractor": evidence.extractor,
        "evidence_text": _decrypt_field_value(
            evidence.evidence_text,
            deks,
            table="memory_item_evidence",
            field="evidence_text",
            user_id=evidence.user_id,
        ),
        "metadata_json": evidence.metadata_json,
        "created_at": serialize_optional_datetime(evidence.created_at),
    }


def serialize_user_profile_field_record(
    field: UserProfileField,
    *,
    deks: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    return {
        "id": field.id,
        "user_id": field.user_id,
        "category": field.category,
        "key": field.key,
        "value_text": _decrypt_field_value(
            field.value_text,
            deks,
            table="user_profile_fields",
            field="value_text",
            user_id=field.user_id,
        ),
        "confidence": field.confidence,
        "status": field.status,
        "source_kind": field.source_kind,
        "source_memory_id": field.source_memory_id,
        "source_evidence_id": field.source_evidence_id,
        "source_claim_evidence_id": field.source_claim_evidence_id,
        "superseded_by_id": field.superseded_by_id,
        "first_observed_at": serialize_optional_datetime(field.first_observed_at),
        "last_observed_at": serialize_optional_datetime(field.last_observed_at),
        "created_at": serialize_optional_datetime(field.created_at),
        "updated_at": serialize_optional_datetime(field.updated_at),
    }


def serialize_user_profile_field_evidence_record(
    evidence: UserProfileFieldEvidence,
    *,
    deks: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    return {
        "id": evidence.id,
        "profile_field_id": evidence.profile_field_id,
        "user_id": evidence.user_id,
        "source_kind": evidence.source_kind,
        "source_memory_id": evidence.source_memory_id,
        "source_evidence_id": evidence.source_evidence_id,
        "source_claim_evidence_id": evidence.source_claim_evidence_id,
        "runtime_thread_id": evidence.runtime_thread_id,
        "runtime_message_id": evidence.runtime_message_id,
        "evidence_text": _decrypt_field_value(
            evidence.evidence_text,
            deks,
            table="user_profile_field_evidence",
            field="evidence_text",
            user_id=evidence.user_id,
        ),
        "observed_at": serialize_optional_datetime(evidence.observed_at),
        "created_at": serialize_optional_datetime(evidence.created_at),
    }


def serialize_memory_episode_record(
    ep: MemoryEpisode,
    *,
    deks: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    return {
        "id": ep.id,
        "user_id": ep.user_id,
        "thread_id": ep.thread_id,
        "date": ep.date,
        "time": ep.time,
        "topics_json": ep.topics_json,
        "summary": _decrypt_field_value(
            ep.summary,
            deks,
            table="memory_episodes",
            field="summary",
            user_id=ep.user_id,
        ),
        "emotional_arc": _decrypt_field_value(
            ep.emotional_arc,
            deks,
            table="memory_episodes",
            field="emotional_arc",
            user_id=ep.user_id,
        ),
        "significance_score": ep.significance_score,
        "turn_count": ep.turn_count,
        "created_at": serialize_optional_datetime(ep.created_at),
    }


def serialize_kg_entity_record(entity: KGEntity) -> dict[str, Any]:
    return {
        "id": entity.id,
        "user_id": entity.user_id,
        "name": entity.name,
        "name_normalized": entity.name_normalized,
        "entity_type": entity.entity_type,
        "description": entity.description,
        "mentions": entity.mentions,
        "aliases_json": entity.aliases_json,
        "embedding_json": entity.embedding_json,
        "embedding_checksum": entity.embedding_checksum,
        "created_at": serialize_optional_datetime(entity.created_at),
        "updated_at": serialize_optional_datetime(entity.updated_at),
    }


def serialize_kg_relation_record(relation: KGRelation) -> dict[str, Any]:
    return {
        "id": relation.id,
        "user_id": relation.user_id,
        "source_id": relation.source_id,
        "destination_id": relation.destination_id,
        "relation_type": relation.relation_type,
        "mentions": relation.mentions,
        "source_memory_id": relation.source_memory_id,
        "evidence_id": relation.evidence_id,
        "observed_at": serialize_optional_datetime(relation.observed_at),
        "valid_from": serialize_optional_datetime(relation.valid_from),
        "valid_to": serialize_optional_datetime(relation.valid_to),
        "confidence": relation.confidence,
        "status": relation.status,
        "supersedes_relation_id": relation.supersedes_relation_id,
        "evolves_from_relation_id": relation.evolves_from_relation_id,
        "created_at": serialize_optional_datetime(relation.created_at),
        "updated_at": serialize_optional_datetime(relation.updated_at),
    }


def serialize_task_record(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "user_id": task.user_id,
        "text": task.text,
        "done": task.done,
        "priority": task.priority,
        "due_date": task.due_date,
        "completed_at": serialize_optional_datetime(task.completed_at),
        "created_at": serialize_optional_datetime(task.created_at),
        "updated_at": serialize_optional_datetime(task.updated_at),
    }


def serialize_agent_thread_record(t: AgentThread) -> dict[str, Any]:
    return {
        "id": t.id,
        "user_id": t.user_id,
        "status": t.status,
        "title": t.title,
        "created_at": serialize_optional_datetime(t.created_at),
        "updated_at": serialize_optional_datetime(t.updated_at),
        "last_message_at": serialize_optional_datetime(t.last_message_at),
        "next_message_sequence": t.next_message_sequence,
    }


def serialize_agent_run_record(r: AgentRun) -> dict[str, Any]:
    return {
        "id": r.id,
        "thread_id": r.thread_id,
        "user_id": r.user_id,
        "provider": r.provider,
        "model": r.model,
        "mode": r.mode,
        "status": r.status,
        "stop_reason": r.stop_reason,
        "error_text": r.error_text,
        "started_at": serialize_optional_datetime(r.started_at),
        "completed_at": serialize_optional_datetime(r.completed_at),
        "prompt_tokens": r.prompt_tokens,
        "completion_tokens": r.completion_tokens,
        "total_tokens": r.total_tokens,
    }


def serialize_agent_step_record(s: AgentStep) -> dict[str, Any]:
    return {
        "id": s.id,
        "run_id": s.run_id,
        "thread_id": s.thread_id,
        "step_index": s.step_index,
        "status": s.status,
        "request_json": s.request_json,
        "response_json": s.response_json,
        "tool_calls_json": s.tool_calls_json,
        "usage_json": s.usage_json,
        "error_text": s.error_text,
        "created_at": serialize_optional_datetime(s.created_at),
    }


def serialize_agent_message_record(
    m: AgentMessage,
    *,
    thread_user_map: dict[int, int] | None = None,
    deks: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    user_id = thread_user_map.get(m.thread_id) if thread_user_map is not None else None
    return {
        "id": m.id,
        "thread_id": m.thread_id,
        "run_id": m.run_id,
        "step_id": m.step_id,
        "sequence_id": m.sequence_id,
        "role": m.role,
        "content_text": _decrypt_field_value(
            m.content_text,
            deks,
            table="agent_messages",
            field="content_text",
            user_id=user_id,
        ),
        "content_json": m.content_json,
        "tool_name": m.tool_name,
        "tool_call_id": m.tool_call_id,
        "tool_args_json": m.tool_args_json,
        "is_in_context": m.is_in_context,
        "token_estimate": m.token_estimate,
        "created_at": serialize_optional_datetime(m.created_at),
    }


def serialize_self_model_block_record(
    block: SelfModelBlock,
    *,
    deks: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    return {
        "id": block.id,
        "user_id": block.user_id,
        "section": block.section,
        "content": _decrypt_field_value(
            block.content,
            deks,
            table="self_model_blocks",
            field="content",
            user_id=block.user_id,
        ),
        "version": block.version,
        "updated_by": block.updated_by,
        "metadata_json": block.metadata_json,
        "created_at": serialize_optional_datetime(block.created_at),
        "updated_at": serialize_optional_datetime(block.updated_at),
    }


def serialize_emotional_signal_record(
    signal: EmotionalSignal,
    *,
    deks: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    return {
        "id": signal.id,
        "user_id": signal.user_id,
        "thread_id": signal.thread_id,
        "emotion": signal.emotion,
        "confidence": signal.confidence,
        "evidence_type": signal.evidence_type,
        "evidence": _decrypt_field_value(
            signal.evidence,
            deks,
            table="emotional_signals",
            field="evidence",
            user_id=signal.user_id,
        ),
        "trajectory": signal.trajectory,
        "previous_emotion": signal.previous_emotion,
        "topic": _decrypt_field_value(
            signal.topic,
            deks,
            table="emotional_signals",
            field="topic",
            user_id=signal.user_id,
        ),
        "acted_on": signal.acted_on,
        "created_at": serialize_optional_datetime(signal.created_at),
    }


def serialize_foresight_signal_record(
    signal: ForesightSignal,
    *,
    deks: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    return {
        "id": signal.id,
        "user_id": signal.user_id,
        "content": _decrypt_field_value(
            signal.content,
            deks,
            table="foresight_signals",
            field="content",
            user_id=signal.user_id,
        ),
        "evidence": _decrypt_field_value(
            signal.evidence,
            deks,
            table="foresight_signals",
            field="evidence",
            user_id=signal.user_id,
        ),
        "relative_text": _decrypt_field_value(
            signal.relative_text,
            deks,
            table="foresight_signals",
            field="relative_text",
            user_id=signal.user_id,
        ),
        "start_date": serialize_optional_date(signal.start_date),
        "end_date": serialize_optional_date(signal.end_date),
        "duration_days": signal.duration_days,
        "status": signal.status,
        "confidence": signal.confidence,
        "source_thread_id": signal.source_thread_id,
        "source_message_ids_json": signal.source_message_ids_json,
        "observed_at": serialize_optional_datetime(signal.observed_at),
        "last_seen_at": serialize_optional_datetime(signal.last_seen_at),
        "created_at": serialize_optional_datetime(signal.created_at),
        "updated_at": serialize_optional_datetime(signal.updated_at),
    }


def serialize_agent_experience_record(
    experience: AgentExperience,
    *,
    deks: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    return {
        "id": experience.id,
        "user_id": experience.user_id,
        "task_intent": _decrypt_field_value(
            experience.task_intent,
            deks,
            table="agent_experiences",
            field="task_intent",
            user_id=experience.user_id,
        ),
        "approach": _decrypt_field_value(
            experience.approach,
            deks,
            table="agent_experiences",
            field="approach",
            user_id=experience.user_id,
        ),
        "quality_score": experience.quality_score,
        "source_thread_id": experience.source_thread_id,
        "source_run_id": experience.source_run_id,
        "tool_names_json": experience.tool_names_json,
        "turn_count": experience.turn_count,
        "embedding_json": experience.embedding_json,
        "cluster_id": experience.cluster_id,
        "superseded_by": experience.superseded_by,
        "created_at": serialize_optional_datetime(experience.created_at),
        "updated_at": serialize_optional_datetime(experience.updated_at),
    }


def serialize_latent_trace_record(trace: LatentTrace) -> dict[str, Any]:
    """IL4 latent trace: no encrypted fields (see ``LatentTrace`` docstring —
    ``topic_key`` is a structural key stored plaintext like
    ``MemoryClaim.canonical_key``, and ``evidence_refs`` holds only
    identifiers, never copied content), so unlike memory/skill records this
    needs no ``deks`` argument."""
    return {
        "id": trace.id,
        "user_id": trace.user_id,
        "topic_key": trace.topic_key,
        "kind": trace.kind,
        "weight": trace.weight,
        "evidence_refs": trace.evidence_refs,
        "first_seen": serialize_optional_datetime(trace.first_seen),
        "last_seen": serialize_optional_datetime(trace.last_seen),
    }


def serialize_memory_claim_record(
    claim: MemoryClaim,
    *,
    deks: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    """``value_text`` uses ``table="memory_items"`` for decryption — matching
    ``claims.upsert_claim``/``claims.upsert_tendency_claim``'s own (existing)
    encryption convention for this column, so domain resolution and AAD stay
    consistent between write and export."""
    return {
        "id": claim.id,
        "user_id": claim.user_id,
        "subject_type": claim.subject_type,
        "namespace": claim.namespace,
        "slot": claim.slot,
        "value_text": _decrypt_field_value(
            claim.value_text,
            deks,
            table="memory_items",
            field="content",
            user_id=claim.user_id,
        ),
        "value_json": claim.value_json,
        "polarity": claim.polarity,
        "confidence": claim.confidence,
        "status": claim.status,
        "canonical_key": claim.canonical_key,
        "source_kind": claim.source_kind,
        "extractor": claim.extractor,
        "memory_item_id": claim.memory_item_id,
        "superseded_by_id": claim.superseded_by_id,
        "created_at": serialize_optional_datetime(claim.created_at),
        "updated_at": serialize_optional_datetime(claim.updated_at),
    }


def serialize_tendency_contribution_record(
    row: TendencyContribution,
) -> dict[str, Any]:
    """IL5 ledger row: ``contribution_vector`` is numeric-only (never
    content, see ``TendencyContribution`` docstring), so — like
    ``LatentTrace.evidence_refs`` — this needs no ``deks`` argument."""
    return {
        "id": row.id,
        "user_id": row.user_id,
        "tombstone_item_id": row.tombstone_item_id,
        "tendency_claim_id": row.tendency_claim_id,
        "contribution_vector": row.contribution_vector,
        "created_at": serialize_optional_datetime(row.created_at),
    }


def serialize_reconsolidation_log_record(
    row: ReconsolidationLog,
) -> dict[str, Any]:
    """IL6 provenance ledger row: ``old_value``/``new_value`` are numeric
    only (never content, see ``ReconsolidationLog`` docstring), so — like
    ``TendencyContribution`` — this needs no ``deks`` argument. Preserving
    this table verbatim across export/import is what makes
    ``original_salience_from_log`` reversibility survive a vault
    round-trip."""
    return {
        "id": row.id,
        "user_id": row.user_id,
        "memory_item_id": row.memory_item_id,
        "applied_at": serialize_optional_datetime(row.applied_at),
        "field": row.field,
        "old_value": row.old_value,
        "new_value": row.new_value,
        "eta": row.eta,
    }


def serialize_initiative_log_record(
    row: InitiativeLog,
    *,
    deks: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    """IL3 push-initiative provenance row. ``generated_text`` is the one
    free-text field (field-encrypted like ``foresight_signals.content``);
    ``pressure_snapshot``/``gate_states`` are numeric/boolean JSON only, no
    ``deks`` needed for those two (mirrors ``TendencyContribution``/
    ``ReconsolidationLog``)."""
    return {
        "id": row.id,
        "user_id": row.user_id,
        "fired_at": serialize_optional_datetime(row.fired_at),
        "drive": row.drive,
        "pressure_snapshot": row.pressure_snapshot,
        "gate_states": row.gate_states,
        "generated_text": _decrypt_field_value(
            row.generated_text,
            deks,
            table="initiative_log",
            field="generated_text",
            user_id=row.user_id,
        ),
        "delivered": row.delivered,
        "answered": row.answered,
        "created_at": serialize_optional_datetime(row.created_at),
    }


def serialize_dream_journal_record(
    row: DreamJournal,
    *,
    deks: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    """IL7 dream-journal row. ``narrative`` AND ``source_refs.latent_topic_keys``
    are field-encrypted (the topic keys embed content slugs), so both are
    decrypted here and re-encrypted on import — exporting the per-field
    ciphertext verbatim would leave it under the old key hierarchy and break
    forget/purge after an import. ``memory_item_ids``/``affect_delta`` are
    numeric only."""
    source_refs = dict(row.source_refs) if isinstance(row.source_refs, dict) else {}
    keys = source_refs.get("latent_topic_keys")
    if isinstance(keys, list):
        source_refs["latent_topic_keys"] = [
            _decrypt_field_value(
                k, deks, table="dream_journal", field="source_refs", user_id=row.user_id
            )
            for k in keys
        ]
    return {
        "id": row.id,
        "user_id": row.user_id,
        "dreamt_at": serialize_optional_datetime(row.dreamt_at),
        "narrative": _decrypt_field_value(
            row.narrative,
            deks,
            table="dream_journal",
            field="narrative",
            user_id=row.user_id,
        ),
        "source_refs": source_refs,
        "affect_delta": row.affect_delta,
        "share_worthy": row.share_worthy,
        "surfaced": row.surfaced,
        "created_at": serialize_optional_datetime(row.created_at),
    }


def serialize_experience_cluster_state_record(
    state: ExperienceClusterState,
) -> dict[str, Any]:
    return {
        "id": state.id,
        "user_id": state.user_id,
        "state_json": state.state_json,
        "created_at": serialize_optional_datetime(state.created_at),
        "updated_at": serialize_optional_datetime(state.updated_at),
    }


def serialize_agent_skill_record(
    skill: AgentSkill,
    *,
    deks: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    return {
        "id": skill.id,
        "user_id": skill.user_id,
        "cluster_id": skill.cluster_id,
        "name": _decrypt_field_value(
            skill.name,
            deks,
            table="agent_skills",
            field="name",
            user_id=skill.user_id,
        ),
        "description": _decrypt_field_value(
            skill.description,
            deks,
            table="agent_skills",
            field="description",
            user_id=skill.user_id,
        ),
        "content": _decrypt_field_value(
            skill.content,
            deks,
            table="agent_skills",
            field="content",
            user_id=skill.user_id,
        ),
        "confidence": skill.confidence,
        "experience_count": skill.experience_count,
        "last_refined_at": serialize_optional_datetime(skill.last_refined_at),
        "embedding_json": skill.embedding_json,
        "superseded_by": skill.superseded_by,
        "created_at": serialize_optional_datetime(skill.created_at),
        "updated_at": serialize_optional_datetime(skill.updated_at),
    }


def serialize_optional_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def serialize_optional_date(value: date | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def parse_optional_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("Vault timestamp is invalid.")
    return datetime.fromisoformat(value)


def parse_optional_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("Vault date is invalid.")
    return date.fromisoformat(value)


def coerce_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def coerce_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def reset_identity_sequences(db: Session) -> None:
    bind = db.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return

    for table_name in (
        "users",
        "user_keys",
        "memory_items",
        "memory_item_evidence",
        "user_profile_fields",
        "user_profile_field_evidence",
        "memory_episodes",
        "kg_entities",
        "kg_relations",
        "tasks",
        "self_model_blocks",
        "emotional_signals",
        "foresight_signals",
        "agent_experiences",
        "experience_cluster_state",
        "agent_skills",
        "latent_traces",
        # IL provenance tables restored with explicit ids — their PG sequences
        # must be advanced past the imported max or the next insert reuses an
        # imported id and hits a PK conflict. dream_journal is IL7's; initiative_log
        # (IL3) and reconsolidation_log (IL6) had the same latent gap.
        "reconsolidation_log",
        "initiative_log",
        "dream_journal",
        "agent_threads",
        "agent_runs",
        "agent_steps",
        "agent_messages",
    ):
        db.execute(
            text(
                f"""
                SELECT setval(
                    pg_get_serial_sequence('{table_name}', 'id'),
                    COALESCE((SELECT MAX(id) FROM {table_name}), 1),
                    COALESCE((SELECT MAX(id) FROM {table_name}), 0) > 0
                )
                """,
            )
        )


def sync_agent_thread_sequence_counters(db: Session) -> None:
    db.execute(
        text(
            """
            UPDATE agent_threads
            SET next_message_sequence = COALESCE(
                (
                    SELECT MAX(agent_messages.sequence_id) + 1
                    FROM agent_messages
                    WHERE agent_messages.thread_id = agent_threads.id
                ),
                1
            )
            """
        )
    )


def _rebuild_vector_indices(db: Session, snapshot: dict[str, Any]) -> None:
    """Rebuild vector indices in per-user anima.db from imported embedding data."""
    try:
        from anima_server.services.agent.embeddings import (
            sync_embeddings_to_runtime,
            sync_to_vector_store,
        )

        user_ids = {int(u["id"]) for u in snapshot.get("users", []) if isinstance(u, dict)}
        for uid in user_ids:
            sync_embeddings_to_runtime(db, user_id=uid)
            sync_to_vector_store(db, user_id=uid)
    except Exception:
        import logging

        logging.getLogger(__name__).debug("Vector index rebuild skipped during import")


def _read_manifest_snapshot() -> dict[str, Any]:
    """Read manifest.json for inclusion in vault export."""
    manifest_path = settings.data_dir / "manifest.json"
    if manifest_path.is_file():
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    return {}


def _manifest_key_hierarchy_matches(
    current_manifest: dict[str, Any],
    vault_manifest: dict[str, Any],
) -> bool:
    required_fields = (
        "core_id",
        "owner_id",
        "keyslots",
        "active_password_credential_generation",
        "active_recovery_credential_generation",
        "frk_rotation",
    )
    if any(
        field not in current_manifest or field not in vault_manifest for field in required_fields
    ):
        return False
    return all(
        current_manifest.get(field) == vault_manifest.get(field)
        for field in _VERSIONED_KEY_HIERARCHY_FIELDS
    )


def _rebind_snapshot_key_hierarchy(
    db: Session,
    snapshot: dict[str, Any],
    *,
    user_id: int,
    current_manifest: dict[str, Any],
) -> None:
    """Keep the authenticated Core's account and credentials on cross-Core import."""
    owner_id = current_manifest.get("owner_id")
    if not isinstance(owner_id, str) or not owner_id:
        raise ValueError("Current Core manifest is missing its owner identity.")

    current_user = db.get(User, user_id)
    if current_user is None:
        raise ValueError("Authenticated Core user is missing.")
    users_payload = snapshot.get("users")
    if (
        not isinstance(users_payload, list)
        or len(users_payload) != 1
        or not isinstance(users_payload[0], dict)
    ):
        raise ValueError("Vault database snapshot must contain one user.")

    rebound_user = dict(users_payload[0])
    rebound_user.update(
        {
            "id": current_user.id,
            "username": current_user.username,
            "password_hash": current_user.password_hash,
            "created_at": serialize_optional_datetime(current_user.created_at),
        }
    )
    snapshot["users"] = [rebound_user]
    for records in snapshot.values():
        if not isinstance(records, list):
            continue
        for record in records:
            if isinstance(record, dict) and "user_id" in record:
                record["user_id"] = current_user.id

    current_user_keys = list(db.scalars(select(UserKey).where(UserKey.user_id == user_id)).all())
    current_soul_keyslots = list(
        db.scalars(select(SoulKeyslot).where(SoulKeyslot.owner_id == owner_id)).all()
    )
    snapshot["userKeys"] = [serialize_user_key_record(user_key) for user_key in current_user_keys]
    snapshot["soulKeyslots"] = [
        serialize_soul_keyslot_record(keyslot) for keyslot in current_soul_keyslots
    ]


def _restore_manifest_identity(vault_manifest: dict[str, Any]) -> None:
    """Atomically restore portable identity and its matching keyslot authority."""

    def restore_authority(current: dict[str, object]) -> None:
        for field in _PORTABLE_MANIFEST_AUTHORITY_FIELDS:
            if field in vault_manifest:
                current[field] = vault_manifest[field]
            else:
                current.pop(field, None)

    update_core_manifest(restore_authority)


def _re_encrypt_snapshot_fields(
    snapshot: dict[str, Any],
    user_id: int,
) -> None:
    """Re-encrypt plaintext fields in a vault snapshot with the importing user's DEK.

    The vault stores plaintext (field-level encryption was stripped on export).
    This function applies the importing user's DEK before the data is written to DB.
    """
    deks = get_active_deks(user_id)
    if deks is None:
        return  # No encryption active — store as plaintext

    for item in snapshot.get("memoryItems", []):
        if isinstance(item, dict) and item.get("content"):
            item["content"] = _re_encrypt_field_value(
                item["content"], user_id, table="memory_items", field="content"
            )

    for evidence in snapshot.get("memoryItemEvidence", []):
        if isinstance(evidence, dict) and evidence.get("evidence_text"):
            evidence["evidence_text"] = _re_encrypt_field_value(
                evidence["evidence_text"],
                user_id,
                table="memory_item_evidence",
                field="evidence_text",
            )

    for claim in snapshot.get("memoryClaims", []):
        if isinstance(claim, dict) and claim.get("value_text"):
            # Claim value_text encrypts under table="memory_items" (see the
            # decrypt path in _serialize_memory_claim) — keep it consistent.
            claim["value_text"] = _re_encrypt_field_value(
                claim["value_text"],
                user_id,
                table="memory_items",
                field="content",
            )

    for field in snapshot.get("userProfileFields", []):
        if isinstance(field, dict) and field.get("value_text"):
            field["value_text"] = _re_encrypt_field_value(
                field["value_text"],
                user_id,
                table="user_profile_fields",
                field="value_text",
            )

    for evidence in snapshot.get("userProfileFieldEvidence", []):
        if isinstance(evidence, dict) and evidence.get("evidence_text"):
            evidence["evidence_text"] = _re_encrypt_field_value(
                evidence["evidence_text"],
                user_id,
                table="user_profile_field_evidence",
                field="evidence_text",
            )

    for ep in snapshot.get("memoryEpisodes", []):
        if isinstance(ep, dict):
            if ep.get("summary"):
                ep["summary"] = _re_encrypt_field_value(
                    ep["summary"], user_id, table="memory_episodes", field="summary"
                )
            if ep.get("emotional_arc"):
                ep["emotional_arc"] = _re_encrypt_field_value(
                    ep["emotional_arc"], user_id, table="memory_episodes", field="emotional_arc"
                )

    for block in snapshot.get("selfModelBlocks", []):
        if isinstance(block, dict) and block.get("content"):
            block["content"] = _re_encrypt_field_value(
                block["content"], user_id, table="self_model_blocks", field="content"
            )

    for signal in snapshot.get("emotionalSignals", []):
        if isinstance(signal, dict):
            if signal.get("evidence"):
                signal["evidence"] = _re_encrypt_field_value(
                    signal["evidence"], user_id, table="emotional_signals", field="evidence"
                )
            if signal.get("topic"):
                signal["topic"] = _re_encrypt_field_value(
                    signal["topic"], user_id, table="emotional_signals", field="topic"
                )

    for signal in snapshot.get("foresightSignals", []):
        if isinstance(signal, dict):
            if signal.get("content"):
                signal["content"] = _re_encrypt_field_value(
                    signal["content"], user_id, table="foresight_signals", field="content"
                )
            if signal.get("evidence"):
                signal["evidence"] = _re_encrypt_field_value(
                    signal["evidence"], user_id, table="foresight_signals", field="evidence"
                )
            if signal.get("relative_text"):
                signal["relative_text"] = _re_encrypt_field_value(
                    signal["relative_text"],
                    user_id,
                    table="foresight_signals",
                    field="relative_text",
                )

    for initiative in snapshot.get("initiativeLog", []):
        if isinstance(initiative, dict) and initiative.get("generated_text"):
            initiative["generated_text"] = _re_encrypt_field_value(
                initiative["generated_text"],
                user_id,
                table="initiative_log",
                field="generated_text",
            )

    for dream in snapshot.get("dreamJournal", []):
        if not isinstance(dream, dict):
            continue
        if dream.get("narrative"):
            dream["narrative"] = _re_encrypt_field_value(
                dream["narrative"],
                user_id,
                table="dream_journal",
                field="narrative",
            )
        # source_refs.latent_topic_keys are field-encrypted too — re-encrypt
        # them under the importing hierarchy so post-import forget/purge can
        # decrypt and match them.
        refs = dream.get("source_refs")
        if isinstance(refs, dict) and isinstance(refs.get("latent_topic_keys"), list):
            refs["latent_topic_keys"] = [
                _re_encrypt_field_value(
                    k, user_id, table="dream_journal", field="source_refs"
                )
                for k in refs["latent_topic_keys"]
            ]

    for experience in snapshot.get("agentExperiences", []):
        if isinstance(experience, dict):
            if experience.get("task_intent"):
                experience["task_intent"] = _re_encrypt_field_value(
                    experience["task_intent"],
                    user_id,
                    table="agent_experiences",
                    field="task_intent",
                )
            if experience.get("approach"):
                experience["approach"] = _re_encrypt_field_value(
                    experience["approach"],
                    user_id,
                    table="agent_experiences",
                    field="approach",
                )

    for skill in snapshot.get("agentSkills", []):
        if isinstance(skill, dict):
            if skill.get("name"):
                skill["name"] = _re_encrypt_field_value(
                    skill["name"], user_id, table="agent_skills", field="name"
                )
            if skill.get("description"):
                skill["description"] = _re_encrypt_field_value(
                    skill["description"],
                    user_id,
                    table="agent_skills",
                    field="description",
                )
            if skill.get("content"):
                skill["content"] = _re_encrypt_field_value(
                    skill["content"], user_id, table="agent_skills", field="content"
                )

    for msg in snapshot.get("agentMessages", []):
        if isinstance(msg, dict) and msg.get("content_text"):
            msg["content_text"] = _re_encrypt_field_value(
                msg["content_text"], user_id, table="agent_messages", field="content_text"
            )


def random_bytes(length: int) -> bytes:
    return os.urandom(length)
