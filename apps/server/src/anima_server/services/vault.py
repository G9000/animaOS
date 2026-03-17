from __future__ import annotations
from anima_server.services.crypto import (
    ARGON2_MEMORY_COST_KIB,
    ARGON2_PARALLELISM,
    ARGON2_TIME_COST,
    AUTH_TAG_LENGTH,
    IV_LENGTH,
    KEY_LENGTH,
    SALT_LENGTH,
    decrypt_text_with_dek,
    derive_argon2id_key,
)
from anima_server.models import (
    AgentMessage,
    AgentRun,
    AgentStep,
    AgentThread,
    EmotionalSignal,
    MemoryDailyLog,
    MemoryEpisode,
    MemoryItem,
    SelfModelBlock,
    SessionNote,
    Task,
    User,
    UserKey,
)
from anima_server.config import settings
from anima_server.services.data_crypto import ef as encrypt_field_for_user
from anima_server.services.sessions import get_active_dek
from sqlalchemy.orm import Session
from sqlalchemy import select, text
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import base64
import hashlib
import json
import logging
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

vault_logger = logging.getLogger(__name__)


VAULT_VERSION = 2

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
            raise ValueError(
                f"No migration path from vault version {version} to {version + 1}."
            )
        payload = migrator(payload)
        version = payload.get("version", version + 1)
    return payload


_MEMORY_TABLES = frozenset({
    "memoryItems", "memoryEpisodes", "memoryDailyLogs",
    "selfModelBlocks", "emotionalSignals", "sessionNotes",
})

_IDENTITY_TABLES = frozenset({
    "users", "userKeys",
})

_CONVERSATION_TABLES = frozenset({
    "agentThreads", "agentRuns", "agentSteps", "agentMessages",
    "tasks",
})


def _decrypt_field_value(value: str | None, dek: bytes | None) -> str | None:
    """Decrypt a field-level encrypted value for vault export.

    Returns plaintext so the vault envelope is the only encryption layer.
    If no DEK is active or the value is not encrypted, returns as-is.
    """
    if value is None or dek is None:
        return value
    return decrypt_text_with_dek(value, dek)


def _re_encrypt_field_value(
    value: str | None, user_id: int,
) -> str | None:
    """Re-encrypt a plaintext value with the importing user's active DEK."""
    if value is None:
        return value
    return encrypt_field_for_user(user_id, value)


def export_vault(db: Session, passphrase: str, *, user_id: int | None = None, scope: str = "full") -> dict[str, Any]:
    # Resolve active DEK so we can decrypt field-level encryption before export.
    # The vault envelope is the only encryption layer in the exported file.
    dek: bytes | None = None
    if user_id is not None:
        dek = get_active_dek(user_id)

    full_snapshot = export_database_snapshot(db, user_id=user_id, dek=dek)

    if scope == "memories":
        # Only memory/identity tables — no conversation transcripts
        snapshot = {
            k: v for k, v in full_snapshot.items()
            if k in _MEMORY_TABLES or k in _IDENTITY_TABLES
        }
    else:
        snapshot = full_snapshot

    # Include manifest so the Core's identity survives transfer
    manifest = _read_manifest_snapshot()

    payload = {
        "version": VAULT_VERSION,
        "createdAt": datetime.now(UTC).isoformat(),
        "scope": scope,
        "database": snapshot,
        "manifest": manifest,
        "userFiles": read_data_snapshot(user_id=user_id) if scope == "full" else {},
    }
    plaintext = json.dumps(payload)
    aad = f"anima-vault:v{VAULT_VERSION}:{scope}".encode("utf-8")
    envelope = encrypt_string(plaintext, passphrase, aad=aad)
    vault = json.dumps(envelope)
    date_stamp = datetime.now().date().isoformat()
    return {
        "filename": f"anima-vault-{date_stamp}.vault.json",
        "vault": vault,
        "size": len(vault.encode("utf-8")),
    }


def import_vault(db: Session, vault: str, passphrase: str, *, user_id: int | None = None) -> dict[str, Any]:
    """Import an encrypted vault into the current database context."""
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

    # Re-encrypt plaintext fields with importing user's DEK before restoring
    if user_id is not None:
        _re_encrypt_snapshot_fields(database, user_id)

    restore_database_snapshot(db, database, scope=vault_scope)
    write_data_snapshot(user_files, user_id=user_id)

    # Restore manifest identity (core_id, created_at) from vault if present
    vault_manifest = payload.get("manifest")
    if isinstance(vault_manifest, dict):
        _restore_manifest_identity(vault_manifest)

    # Rebuild vector index from imported embeddings
    _rebuild_vector_indices(db, database)

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
    }


def encrypt_string(
    plaintext: str,
    passphrase: str,
    *,
    aad: bytes | None = None,
) -> dict[str, Any]:
    salt = random_bytes(SALT_LENGTH)
    iv = random_bytes(IV_LENGTH)
    key = derive_argon2id_key(passphrase, salt)
    encrypted = AESGCM(key).encrypt(iv, plaintext.encode("utf-8"), aad)
    ciphertext, tag = encrypted[:-
                                AUTH_TAG_LENGTH], encrypted[-AUTH_TAG_LENGTH:]
    ciphertext_b64 = base64.b64encode(ciphertext).decode("ascii")
    integrity_hash = hashlib.sha256(ciphertext_b64.encode("ascii")).hexdigest()
    envelope: dict[str, Any] = {
        "version": VAULT_VERSION,
        "createdAt": datetime.now(UTC).isoformat(),
        "payloadVersion": VAULT_VERSION,
        "kdf": {
            "name": "argon2id",
            "timeCost": ARGON2_TIME_COST,
            "memoryCostKiB": ARGON2_MEMORY_COST_KIB,
            "parallelism": ARGON2_PARALLELISM,
            "keyLength": KEY_LENGTH,
            "salt": base64.b64encode(salt).decode("ascii"),
        },
        "cipher": {
            "name": "aes-256-gcm",
            "iv": base64.b64encode(iv).decode("ascii"),
            "tag": base64.b64encode(tag).decode("ascii"),
        },
        "ciphertext": ciphertext_b64,
        "integrity": {
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

    # Pre-decryption integrity check (v2+ envelopes include integrity hash)
    integrity = envelope.get("integrity")
    if isinstance(integrity, dict) and integrity.get("algorithm") == "sha256":
        expected = integrity.get("hash", "")
        actual = hashlib.sha256(ciphertext_b64.encode("ascii")).hexdigest()
        if actual != expected:
            raise ValueError(
                "Vault integrity check failed — ciphertext may be corrupted."
            )

    # Recover AAD from envelope (backwards-compatible: None if absent)
    aad: bytes | None = None
    aad_b64 = envelope.get("aad_b64")
    if isinstance(aad_b64, str):
        aad = base64.b64decode(aad_b64)

    try:
        salt = base64.b64decode(str(kdf["salt"]))
        iv = base64.b64decode(str(cipher["iv"]))
        tag = base64.b64decode(str(cipher["tag"]))
        ciphertext = base64.b64decode(ciphertext_b64)
        key = derive_argon2id_key(
            passphrase,
            salt,
            time_cost=int(kdf["timeCost"]),
            memory_cost_kib=int(kdf["memoryCostKiB"]),
            parallelism=int(kdf["parallelism"]),
            key_length=int(kdf["keyLength"]),
        )
        plaintext = AESGCM(key).decrypt(iv, ciphertext + tag, aad)
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ValueError(
            "Failed to decrypt vault. Check the passphrase and payload.") from exc

    return plaintext.decode("utf-8")


def export_database_snapshot(
    db: Session,
    *,
    user_id: int | None = None,
    dek: bytes | None = None,
) -> dict[str, list[dict[str, Any]]]:
    def _scoped(query, model):  # type: ignore[no-untyped-def]
        if user_id is not None and hasattr(model, "user_id"):
            return query.where(model.user_id == user_id)
        return query

    if user_id is not None:
        users = [serialize_user_record(u) for u in db.scalars(
            select(User).where(User.id == user_id)).all()]
    else:
        users = [serialize_user_record(u)
                 for u in db.scalars(select(User)).all()]
    user_keys = [
        serialize_user_key_record(user_key)
        for user_key in db.scalars(_scoped(select(UserKey), UserKey)).all()
    ]
    memory_items = [
        serialize_memory_item_record(item, dek=dek)
        for item in db.scalars(_scoped(select(MemoryItem), MemoryItem)).all()
    ]
    memory_episodes = [
        serialize_memory_episode_record(ep, dek=dek)
        for ep in db.scalars(_scoped(select(MemoryEpisode), MemoryEpisode)).all()
    ]
    memory_daily_logs = [
        serialize_memory_daily_log_record(log, dek=dek)
        for log in db.scalars(_scoped(select(MemoryDailyLog), MemoryDailyLog)).all()
    ]
    tasks = [
        serialize_task_record(task)
        for task in db.scalars(_scoped(select(Task), Task)).all()
    ]
    agent_threads = [
        serialize_agent_thread_record(t)
        for t in db.scalars(_scoped(select(AgentThread), AgentThread)).all()
    ]
    # Scope runs/steps/messages via user_id on runs, thread_id on steps/messages
    agent_runs = [
        serialize_agent_run_record(r)
        for r in db.scalars(_scoped(select(AgentRun), AgentRun)).all()
    ]
    # Build thread_id -> user_id map for message decryption
    _thread_user_map: dict[int, int] = {
        t["id"]: t["user_id"] for t in agent_threads}

    if user_id is not None:
        scoped_thread_ids = [t["id"] for t in agent_threads]
        agent_steps = [
            serialize_agent_step_record(s)
            for s in db.scalars(
                select(AgentStep).where(
                    AgentStep.thread_id.in_(scoped_thread_ids))
            ).all()
        ] if scoped_thread_ids else []
        agent_messages = [
            serialize_agent_message_record(m, thread_user_map=_thread_user_map, dek=dek)
            for m in db.scalars(
                select(AgentMessage).where(
                    AgentMessage.thread_id.in_(scoped_thread_ids))
            ).all()
        ] if scoped_thread_ids else []
    else:
        agent_steps = [
            serialize_agent_step_record(s)
            for s in db.scalars(select(AgentStep)).all()
        ]
        agent_messages = [
            serialize_agent_message_record(m, thread_user_map=_thread_user_map, dek=dek)
            for m in db.scalars(select(AgentMessage)).all()
        ]
    session_notes = [
        serialize_session_note_record(n, dek=dek)
        for n in db.scalars(_scoped(select(SessionNote), SessionNote)).all()
    ]
    self_model_blocks = [
        serialize_self_model_block_record(b, dek=dek)
        for b in db.scalars(_scoped(select(SelfModelBlock), SelfModelBlock)).all()
    ]
    emotional_signals = [
        serialize_emotional_signal_record(s, dek=dek)
        for s in db.scalars(_scoped(select(EmotionalSignal), EmotionalSignal)).all()
    ]
    return {
        "users": users,
        "userKeys": user_keys,
        "memoryItems": memory_items,
        "memoryEpisodes": memory_episodes,
        "memoryDailyLogs": memory_daily_logs,
        "tasks": tasks,
        "sessionNotes": session_notes,
        "selfModelBlocks": self_model_blocks,
        "emotionalSignals": emotional_signals,
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
        raise ValueError(
            "Vault database snapshot is missing users or userKeys.")

    memory_items_payload = snapshot.get("memoryItems", [])
    memory_episodes_payload = snapshot.get("memoryEpisodes", [])
    memory_daily_logs_payload = snapshot.get("memoryDailyLogs", [])
    tasks_payload = snapshot.get("tasks", [])
    session_notes_payload = snapshot.get("sessionNotes", [])
    self_model_blocks_payload = snapshot.get("selfModelBlocks", [])
    emotional_signals_payload = snapshot.get("emotionalSignals", [])
    agent_threads_payload = snapshot.get("agentThreads", [])
    agent_runs_payload = snapshot.get("agentRuns", [])
    agent_steps_payload = snapshot.get("agentSteps", [])
    agent_messages_payload = snapshot.get("agentMessages", [])

    # For memories-only imports, only clear tables that were exported
    is_full = scope == "full"

    try:
        db.query(EmotionalSignal).delete()
        db.query(SelfModelBlock).delete()
        db.query(SessionNote).delete()
        if is_full:
            db.query(AgentStep).delete()
            db.query(AgentMessage).delete()
            db.query(AgentRun).delete()
            db.query(AgentThread).delete()
            db.query(Task).delete()
        db.query(MemoryDailyLog).delete()
        db.query(MemoryEpisode).delete()
        db.query(MemoryItem).delete()
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
                    created_at=parse_optional_datetime(
                        record.get("created_at")),
                    updated_at=parse_optional_datetime(
                        record.get("updated_at")),
                )
            )

        for record in user_keys_payload:
            if not isinstance(record, dict):
                raise ValueError("Vault user key record is invalid.")
            db.add(
                UserKey(
                    id=int(record["id"]),
                    user_id=int(record["user_id"]),
                    kdf_salt=str(record["kdf_salt"]),
                    kdf_time_cost=int(record["kdf_time_cost"]),
                    kdf_memory_cost_kib=int(record["kdf_memory_cost_kib"]),
                    kdf_parallelism=int(record["kdf_parallelism"]),
                    kdf_key_length=int(record["kdf_key_length"]),
                    wrap_iv=str(record["wrap_iv"]),
                    wrap_tag=str(record["wrap_tag"]),
                    wrapped_dek=str(record["wrapped_dek"]),
                    created_at=parse_optional_datetime(
                        record.get("created_at")),
                    updated_at=parse_optional_datetime(
                        record.get("updated_at")),
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
                    superseded_by=coerce_optional_int(
                        record.get("superseded_by")),
                    reference_count=int(record.get("reference_count", 0)),
                    last_referenced_at=parse_optional_datetime(
                        record.get("last_referenced_at")),
                    embedding_json=record.get("embedding_json"),
                    created_at=parse_optional_datetime(
                        record.get("created_at")),
                    updated_at=parse_optional_datetime(
                        record.get("updated_at")),
                )
            )

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
                    emotional_arc=coerce_optional_str(
                        record.get("emotional_arc")),
                    significance_score=int(
                        record.get("significance_score", 3)),
                    turn_count=coerce_optional_int(record.get("turn_count")),
                    created_at=parse_optional_datetime(
                        record.get("created_at")),
                )
            )

        for record in memory_daily_logs_payload:
            if not isinstance(record, dict):
                continue
            db.add(
                MemoryDailyLog(
                    id=int(record["id"]),
                    user_id=int(record["user_id"]),
                    date=str(record["date"]),
                    user_message=str(record["user_message"]),
                    assistant_response=str(record["assistant_response"]),
                    created_at=parse_optional_datetime(
                        record.get("created_at")),
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
                    completed_at=parse_optional_datetime(
                        record.get("completed_at")),
                    created_at=parse_optional_datetime(
                        record.get("created_at")),
                    updated_at=parse_optional_datetime(
                        record.get("updated_at")),
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
                    created_at=parse_optional_datetime(
                        record.get("created_at")),
                    updated_at=parse_optional_datetime(
                        record.get("updated_at")),
                    last_message_at=parse_optional_datetime(
                        record.get("last_message_at")),
                    next_message_sequence=int(
                        record.get("next_message_sequence", 1)),
                )
            )

        db.flush()

        for record in session_notes_payload:
            if not isinstance(record, dict):
                continue
            db.add(
                SessionNote(
                    id=int(record["id"]),
                    thread_id=int(record["thread_id"]),
                    user_id=int(record["user_id"]),
                    key=str(record["key"]),
                    value=str(record["value"]),
                    note_type=str(record.get("note_type", "observation")),
                    is_active=bool(record.get("is_active", True)),
                    promoted_to_item_id=coerce_optional_int(
                        record.get("promoted_to_item_id")),
                    created_at=parse_optional_datetime(
                        record.get("created_at")),
                    updated_at=parse_optional_datetime(
                        record.get("updated_at")),
                )
            )

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
                    created_at=parse_optional_datetime(
                        record.get("created_at")),
                    updated_at=parse_optional_datetime(
                        record.get("updated_at")),
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
                    evidence_type=str(record.get(
                        "evidence_type", "linguistic")),
                    evidence=str(record.get("evidence", "")),
                    trajectory=str(record.get("trajectory", "stable")),
                    previous_emotion=coerce_optional_str(
                        record.get("previous_emotion")),
                    topic=str(record.get("topic", "")),
                    acted_on=bool(record.get("acted_on", False)),
                    created_at=parse_optional_datetime(
                        record.get("created_at")),
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
                    started_at=parse_optional_datetime(
                        record.get("started_at")),
                    completed_at=parse_optional_datetime(
                        record.get("completed_at")),
                    prompt_tokens=coerce_optional_int(
                        record.get("prompt_tokens")),
                    completion_tokens=coerce_optional_int(
                        record.get("completion_tokens")),
                    total_tokens=coerce_optional_int(
                        record.get("total_tokens")),
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
                    created_at=parse_optional_datetime(
                        record.get("created_at")),
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
                    content_text=coerce_optional_str(
                        record.get("content_text")),
                    content_json=record.get("content_json"),
                    tool_name=coerce_optional_str(record.get("tool_name")),
                    tool_call_id=coerce_optional_str(
                        record.get("tool_call_id")),
                    tool_args_json=record.get("tool_args_json"),
                    is_in_context=bool(record.get("is_in_context", True)),
                    token_estimate=coerce_optional_int(
                        record.get("token_estimate")),
                    created_at=parse_optional_datetime(
                        record.get("created_at")),
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
            local_relative = Path(
                *safe_path.parts[2:]) if len(safe_path.parts) > 2 else Path()
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


def serialize_memory_item_record(
    item: MemoryItem, *, dek: bytes | None = None,
) -> dict[str, Any]:
    return {
        "id": item.id,
        "user_id": item.user_id,
        "content": _decrypt_field_value(item.content, dek),
        "category": item.category,
        "importance": item.importance,
        "source": item.source,
        "superseded_by": item.superseded_by,
        "reference_count": item.reference_count,
        "last_referenced_at": serialize_optional_datetime(item.last_referenced_at),
        "embedding_json": item.embedding_json,
        "created_at": serialize_optional_datetime(item.created_at),
        "updated_at": serialize_optional_datetime(item.updated_at),
    }


def serialize_memory_episode_record(
    ep: MemoryEpisode, *, dek: bytes | None = None,
) -> dict[str, Any]:
    return {
        "id": ep.id,
        "user_id": ep.user_id,
        "thread_id": ep.thread_id,
        "date": ep.date,
        "time": ep.time,
        "topics_json": ep.topics_json,
        "summary": _decrypt_field_value(ep.summary, dek),
        "emotional_arc": _decrypt_field_value(ep.emotional_arc, dek),
        "significance_score": ep.significance_score,
        "turn_count": ep.turn_count,
        "created_at": serialize_optional_datetime(ep.created_at),
    }


def serialize_memory_daily_log_record(
    log: MemoryDailyLog, *, dek: bytes | None = None,
) -> dict[str, Any]:
    return {
        "id": log.id,
        "user_id": log.user_id,
        "date": log.date,
        "user_message": _decrypt_field_value(log.user_message, dek),
        "assistant_response": _decrypt_field_value(log.assistant_response, dek),
        "created_at": serialize_optional_datetime(log.created_at),
    }


def serialize_session_note_record(
    note: SessionNote, *, dek: bytes | None = None,
) -> dict[str, Any]:
    return {
        "id": note.id,
        "thread_id": note.thread_id,
        "user_id": note.user_id,
        "key": note.key,
        "value": _decrypt_field_value(note.value, dek),
        "note_type": note.note_type,
        "is_active": note.is_active,
        "promoted_to_item_id": note.promoted_to_item_id,
        "created_at": serialize_optional_datetime(note.created_at),
        "updated_at": serialize_optional_datetime(note.updated_at),
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
    dek: bytes | None = None,
) -> dict[str, Any]:
    return {
        "id": m.id,
        "thread_id": m.thread_id,
        "run_id": m.run_id,
        "step_id": m.step_id,
        "sequence_id": m.sequence_id,
        "role": m.role,
        "content_text": _decrypt_field_value(m.content_text, dek),
        "content_json": m.content_json,
        "tool_name": m.tool_name,
        "tool_call_id": m.tool_call_id,
        "tool_args_json": m.tool_args_json,
        "is_in_context": m.is_in_context,
        "token_estimate": m.token_estimate,
        "created_at": serialize_optional_datetime(m.created_at),
    }


def serialize_self_model_block_record(
    block: SelfModelBlock, *, dek: bytes | None = None,
) -> dict[str, Any]:
    return {
        "id": block.id,
        "user_id": block.user_id,
        "section": block.section,
        "content": _decrypt_field_value(block.content, dek),
        "version": block.version,
        "updated_by": block.updated_by,
        "metadata_json": block.metadata_json,
        "created_at": serialize_optional_datetime(block.created_at),
        "updated_at": serialize_optional_datetime(block.updated_at),
    }


def serialize_emotional_signal_record(
    signal: EmotionalSignal, *, dek: bytes | None = None,
) -> dict[str, Any]:
    return {
        "id": signal.id,
        "user_id": signal.user_id,
        "thread_id": signal.thread_id,
        "emotion": signal.emotion,
        "confidence": signal.confidence,
        "evidence_type": signal.evidence_type,
        "evidence": _decrypt_field_value(signal.evidence, dek),
        "trajectory": signal.trajectory,
        "previous_emotion": signal.previous_emotion,
        "topic": _decrypt_field_value(signal.topic, dek),
        "acted_on": signal.acted_on,
        "created_at": serialize_optional_datetime(signal.created_at),
    }


def serialize_optional_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def parse_optional_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("Vault timestamp is invalid.")
    return datetime.fromisoformat(value)


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
        "users", "user_keys", "memory_items", "memory_episodes",
        "memory_daily_logs", "tasks", "session_notes",
        "self_model_blocks", "emotional_signals",
        "agent_threads", "agent_runs", "agent_steps", "agent_messages",
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
        from anima_server.services.agent.embeddings import sync_to_vector_store

        user_ids = {int(u["id"]) for u in snapshot.get(
            "users", []) if isinstance(u, dict)}
        for uid in user_ids:
            sync_to_vector_store(db, user_id=uid)
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).debug(
            "Vector index rebuild skipped during import")


def _read_manifest_snapshot() -> dict[str, Any]:
    """Read manifest.json for inclusion in vault export."""
    manifest_path = settings.data_dir / "manifest.json"
    if manifest_path.is_file():
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    return {}


def _restore_manifest_identity(vault_manifest: dict[str, Any]) -> None:
    """Restore core_id and created_at from vault manifest, preserving Core identity."""
    manifest_path = settings.data_dir / "manifest.json"
    if not manifest_path.is_file():
        return
    current = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Preserve the Core's birth identity from the vault
    if "core_id" in vault_manifest:
        current["core_id"] = vault_manifest["core_id"]
    if "created_at" in vault_manifest:
        current["created_at"] = vault_manifest["created_at"]

    manifest_path.write_text(json.dumps(current, indent=2), encoding="utf-8")


def _re_encrypt_snapshot_fields(
    snapshot: dict[str, Any],
    user_id: int,
) -> None:
    """Re-encrypt plaintext fields in a vault snapshot with the importing user's DEK.

    The vault stores plaintext (field-level encryption was stripped on export).
    This function applies the importing user's DEK before the data is written to DB.
    """
    dek = get_active_dek(user_id)
    if dek is None:
        return  # No encryption active — store as plaintext

    for item in snapshot.get("memoryItems", []):
        if isinstance(item, dict) and item.get("content"):
            item["content"] = _re_encrypt_field_value(item["content"], user_id)

    for ep in snapshot.get("memoryEpisodes", []):
        if isinstance(ep, dict):
            if ep.get("summary"):
                ep["summary"] = _re_encrypt_field_value(ep["summary"], user_id)
            if ep.get("emotional_arc"):
                ep["emotional_arc"] = _re_encrypt_field_value(ep["emotional_arc"], user_id)

    for log in snapshot.get("memoryDailyLogs", []):
        if isinstance(log, dict):
            if log.get("user_message"):
                log["user_message"] = _re_encrypt_field_value(log["user_message"], user_id)
            if log.get("assistant_response"):
                log["assistant_response"] = _re_encrypt_field_value(log["assistant_response"], user_id)

    for note in snapshot.get("sessionNotes", []):
        if isinstance(note, dict) and note.get("value"):
            note["value"] = _re_encrypt_field_value(note["value"], user_id)

    for block in snapshot.get("selfModelBlocks", []):
        if isinstance(block, dict) and block.get("content"):
            block["content"] = _re_encrypt_field_value(block["content"], user_id)

    for signal in snapshot.get("emotionalSignals", []):
        if isinstance(signal, dict):
            if signal.get("evidence"):
                signal["evidence"] = _re_encrypt_field_value(signal["evidence"], user_id)
            if signal.get("topic"):
                signal["topic"] = _re_encrypt_field_value(signal["topic"], user_id)

    for msg in snapshot.get("agentMessages", []):
        if isinstance(msg, dict) and msg.get("content_text"):
            msg["content_text"] = _re_encrypt_field_value(msg["content_text"], user_id)


def random_bytes(length: int) -> bytes:
    return os.urandom(length)
