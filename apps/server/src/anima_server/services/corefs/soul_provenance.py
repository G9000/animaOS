"""Authenticated Core references and honest unavailable-source evidence."""

from __future__ import annotations

import re
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import anima_core

from anima_server.config import settings
from anima_server.services.core import get_core_id


@dataclass(frozen=True, slots=True)
class SoulSourceProof:
    user_id: int
    profile: Any
    objects: tuple[Any, ...]
    folders: tuple[Any, ...]
    authored_ids: dict[str, set[str]]
    threads: dict[str, str]
    messages: dict[str, str]
    native_messages: dict[str, str] = field(default_factory=dict)
    owner_id: str | None = None


_proof: ContextVar[SoulSourceProof | None] = ContextVar("soul_source_proof", default=None)
_runtime_source_db: ContextVar[Any | None] = ContextVar("runtime_source_db", default=None)


def _unique(mapping: dict[str, str], identity: object, uri: str) -> None:
    if identity is None:
        return
    key = str(identity)
    if key in mapping and mapping[key] != uri:
        raise ValueError("Authenticated Core source identity is ambiguous")
    mapping[key] = uri


def source_proof_for_session(session: Any) -> SoulSourceProof:
    from anima_server.services.corefs.account_profile import read_account_profile_for_session
    from anima_server.services.corefs.catalog_reader import read_prepared_writing_snapshot
    from anima_server.services.corefs.conversation_authority import (
        read_canonical_conversation_catalog,
    )

    profile = read_account_profile_for_session(session)
    if profile is None:
        raise ValueError("Soul evolution requires an authenticated account profile")
    catalog = read_canonical_conversation_catalog(session=session)
    threads: dict[str, str] = {}
    messages: dict[str, str] = {}
    native_messages: dict[str, str] = {}
    for thread in catalog.threads:
        uri = f"corefs://object/{thread.document.thread_id}"
        _unique(threads, thread.document.legacy_thread_id, uri)
        for segment in thread.segments:
            for event in segment.segment.events:
                message_uri = f"{uri}#message={event.message_id}"
                _unique(messages, event.legacy_message_id, message_uri)
                _unique(native_messages, event.message_id, message_uri)
    from anima_server.services.corefs.catalog_reader import read_prepared_writing_body
    from anima_server.services.corefs.formats import decode_diary_document, decode_task_document

    snapshot = read_prepared_writing_snapshot(session=session)
    authored = {
        "diary_entries": set(),
        "diary_folders": set(),
        "diary_attachments": set(),
        "tasks": set(),
    }
    for folder in snapshot.folders:
        if "legacyId" in folder.metadata:
            authored["diary_folders"].add(str(folder.metadata["legacyId"]))
    for item in snapshot.objects:
        if item.kind == "diary":
            document = decode_diary_document(read_prepared_writing_body(session=session, item=item))
            authored["diary_entries"].add(str(document.legacy_id))
        elif item.kind == "task":
            document = decode_task_document(read_prepared_writing_body(session=session, item=item))
            authored["tasks"].add(str(document.legacy_id))
        elif item.kind == "attachment" and "legacyId" in item.metadata:
            authored["diary_attachments"].add(str(item.metadata["legacyId"]))
    return SoulSourceProof(
        user_id=int(session.user_id),
        profile=profile,
        objects=snapshot.objects,
        folders=snapshot.folders,
        authored_ids=authored,
        threads=threads,
        messages=messages,
        native_messages=native_messages,
        owner_id=profile.owner_id,
    )


@contextmanager
def authenticated_soul_evolution(*, user_id: int, corefs_keys: object):
    """Pin authenticated source facts for one supported forward schema upgrade."""
    from anima_server.services.corefs.authority import reconcile_content_authority

    native = anima_core.CorefsSession(str(settings.data_dir), get_core_id())
    token = None
    try:
        marker = reconcile_content_authority(corefs_session=native, keys=corefs_keys)
        if marker is None:
            from anima_server.services.corefs.greenfield import activate_initial_account_content

            activate_initial_account_content(user_id=user_id, corefs_keys=corefs_keys)
            marker = reconcile_content_authority(corefs_session=native, keys=corefs_keys)
            if marker is None:
                raise ValueError("Soul evolution requires authenticated CoreFS authority")
        session = SimpleNamespace(
            user_id=user_id,
            corefs_session=native,
            corefs_keys=corefs_keys,
            content_authority=marker,
        )
        proof = source_proof_for_session(session)
        token = _proof.set(proof)
        yield proof
    finally:
        if token is not None:
            _proof.reset(token)
        with suppress(Exception):
            native.close()


def current_source_proof(user_id: int | None = None) -> SoulSourceProof | None:
    proof = _proof.get()
    if proof is not None and (user_id is None or proof.user_id == user_id):
        return proof
    if user_id is None:
        return None
    from anima_server.services.sessions import active_unlock_sessions

    for session in reversed(active_unlock_sessions(user_id)):
        if session.corefs_keys is not None:
            return source_proof_for_session(session)
    return None


def preserve_source_reference(
    *, proof: SoulSourceProof | None, field: str, family: str, value: object, many: bool = False
):
    """Resolve authenticated source URIs, preserving unavailable original IDs."""
    if family == "runtime-message":
        return preserve_live_source_reference(
            proof=proof,
            user_id=proof.user_id if proof is not None else None,
            field=field,
            family="message",
            value=value,
            many=many,
        )
    values = list(value) if many and value is not None else [value]
    mapping = (
        {}
        if proof is None
        else (
            proof.threads if family == "thread" else proof.messages if family == "message" else {}
        )
    )
    if family == "transcript" and proof is not None:
        mapping = {uri: uri for uri in proof.threads.values()}
        for item in values:
            # Preserve an old locator as a Core URI only when its compatibility
            # thread identity is independently present in authenticated content.
            match = re.fullmatch(r"[^/\\]*_thread-(\d+)\.jsonl(?:\.enc)?", str(item))
            if match is not None and match[1] in proof.threads:
                mapping[str(item)] = proof.threads[match[1]]
    uris = [mapping.get(str(item)) for item in values if item is not None]
    complete = all(uri is not None for uri in uris)
    resolved = [uri for uri in uris if uri is not None]
    return (resolved if many else resolved[0] if resolved else None), {
        "provenanceState": "authenticated" if complete else "source_unavailable",
        "historicalSourceIds": {field: value},
    }


@contextmanager
def authenticated_soul_only_evolution(*, user_id: int, owner_id: str):
    """Pin caller-verified Soul ownership without inventing filesystem proof.

    The caller must authenticate the manifest owner and Soul key domain first;
    no account profile or authored source is available from this scope.
    """
    proof = SoulSourceProof(
        user_id=user_id,
        profile=None,
        objects=(),
        folders=(),
        authored_ids={},
        threads={},
        messages={},
        owner_id=owner_id,
    )
    token = _proof.set(proof)
    try:
        yield proof
    finally:
        _proof.reset(token)


@contextmanager
def runtime_source_transaction(runtime_db):
    """Resolve live evidence inside its supplied Runtime transaction."""
    if runtime_db is None:
        yield
        return
    token = _runtime_source_db.set(runtime_db)
    try:
        yield
    finally:
        _runtime_source_db.reset(token)


def preserve_live_source_reference(
    *,
    proof: SoulSourceProof | None,
    user_id: int | None,
    field: str,
    family: str,
    value: object,
    many: bool = False,
):
    """Resolve live Runtime message IDs through authenticated native stable IDs.

    Historical schema evolution uses preserve_source_reference separately. A
    Runtime primary key must never fall through to that canonical numeric map.
    """
    if family != "message":
        return preserve_source_reference(
            proof=proof, field=field, family=family, value=value, many=many
        )
    values = list(value) if many and value is not None else [value]
    mapping = {}
    if proof is not None and user_id is not None and proof.user_id == user_id:
        identities = set()
        for item in values:
            try:
                identities.add(int(item))
            except (TypeError, ValueError):
                continue
        if identities and proof.native_messages:
            mapping = _live_message_mapping(proof, user_id, identities)
    uris = [mapping.get(str(item)) for item in values if item is not None]
    resolved = [uri for uri in uris if uri is not None]
    return (resolved if many else resolved[0] if resolved else None), {
        "provenanceState": "authenticated"
        if all(uri is not None for uri in uris)
        else "source_unavailable",
        "historicalSourceIds": {field: value},
    }


def _live_message_mapping(proof: SoulSourceProof, user_id: int, identities: set[int]):
    from sqlalchemy import select
    from sqlalchemy.exc import SQLAlchemyError

    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.models.runtime import RuntimeMessage

    supplied_db = _runtime_source_db.get()
    owned_db = None
    try:
        runtime_db = supplied_db
        if runtime_db is None:
            owned_db = get_runtime_session_factory()()
            runtime_db = owned_db
        rows = runtime_db.scalars(
            select(RuntimeMessage).where(
                RuntimeMessage.user_id == user_id,
                RuntimeMessage.id.in_(identities),
            )
        ).all()
        return {
            str(row.id): proof.native_messages[row.corefs_message_id]
            for row in rows
            if row.corefs_message_id in proof.native_messages
            and proof.threads.get(str(row.thread_id))
            == proof.native_messages[row.corefs_message_id].partition("#message=")[0]
        }
    except (SQLAlchemyError, RuntimeError):
        # Disposable Runtime may be absent; preserve the original ID honestly.
        return {}
    finally:
        if owned_db is not None:
            owned_db.close()
