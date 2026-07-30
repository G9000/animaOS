from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models.corefs_runtime import CoreFSSealedPayload
from anima_server.services.corefs.indexer import (
    CoreFSProgressiveIndex,
    CoreFSRuntimeLocked,
)
from anima_server.services.corefs.runtime_sealing import (
    RuntimePayloadAAD,
    RuntimeSealingLocked,
    SealedRuntimePayload,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _active_runtime_index(user_id: int) -> CoreFSProgressiveIndex | None:
    from anima_server.services.sessions import unlock_session_store

    return unlock_session_store.get_active_runtime_index(user_id)


def active_runtime_index(user_id: int) -> CoreFSProgressiveIndex | None:
    return _active_runtime_index(user_id)


def seal_runtime_record(
    runtime_db: Session,
    *,
    index: CoreFSProgressiveIndex,
    row_type: str,
    row_id: int,
    owner_id: int,
    payload: dict[str, object],
) -> None:
    aad = RuntimePayloadAAD(
        row_type=row_type,
        row_id=str(row_id),
        owner_id=str(owner_id),
    )
    plaintext = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    sealed = index.seal_runtime_payload(plaintext, aad=aad)
    row_id_hash = _digest(str(row_id))
    stored = runtime_db.scalar(
        select(CoreFSSealedPayload).where(
            CoreFSSealedPayload.core_id == index.core_id,
            CoreFSSealedPayload.local_instance_id == index.local_instance_id,
            CoreFSSealedPayload.row_type == row_type,
            CoreFSSealedPayload.row_id_hash == row_id_hash,
        )
    )
    if stored is None:
        stored = CoreFSSealedPayload(
            core_id=index.core_id,
            local_instance_id=index.local_instance_id,
            row_type=row_type,
            row_id_hash=row_id_hash,
            owner_id_hash=_digest(str(owner_id)),
            key_version=sealed.version,
            nonce=sealed.nonce,
            ciphertext=sealed.ciphertext,
            aad_digest=hashlib.sha256(aad.encode()).hexdigest(),
        )
        runtime_db.add(stored)
    else:
        stored.owner_id_hash = _digest(str(owner_id))
        stored.key_version = sealed.version
        stored.nonce = sealed.nonce
        stored.ciphertext = sealed.ciphertext
        stored.aad_digest = hashlib.sha256(aad.encode()).hexdigest()
    runtime_db.flush()


def load_runtime_record(
    runtime_db: Session,
    *,
    row_type: str,
    row_id: int,
    owner_id: int,
) -> dict[str, Any] | None:
    aad = RuntimePayloadAAD(
        row_type=row_type,
        row_id=str(row_id),
        owner_id=str(owner_id),
    )
    sealed = runtime_db.scalar(
        select(CoreFSSealedPayload).where(
            CoreFSSealedPayload.row_type == row_type,
            CoreFSSealedPayload.row_id_hash == _digest(str(row_id)),
            CoreFSSealedPayload.owner_id_hash == _digest(str(owner_id)),
        )
    )
    if sealed is None:
        return None
    index = _active_runtime_index(owner_id)
    if index is None:
        raise RuntimeSealingLocked("sealed Runtime payload is unavailable while CoreFS is locked")
    try:
        if (
            sealed.core_id != index.core_id
            or sealed.local_instance_id != index.local_instance_id
            or sealed.aad_digest != hashlib.sha256(aad.encode()).hexdigest()
        ):
            raise ValueError("sealed Runtime payload binding is invalid")
        raw = index.open_runtime_payload(
            SealedRuntimePayload(
                nonce=bytes(sealed.nonce),
                ciphertext=bytes(sealed.ciphertext),
                version=sealed.key_version,
            ),
            aad=aad,
        )
    except CoreFSRuntimeLocked as exc:
        raise RuntimeSealingLocked(
            "sealed Runtime payload is unavailable while CoreFS is locked"
        ) from exc
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("sealed Runtime payload is invalid")
    return value
