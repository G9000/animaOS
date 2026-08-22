from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import anima_core
import pytest
from anima_server.db.base import Base
from anima_server.services.corefs import conversation_authority
from anima_server.services.corefs.conversation_authority import (
    ConversationAuthoritySelection,
    conversation_authority_selection,
    list_canonical_threads,
)
from anima_server.services.corefs.conversation_migration import (
    build_conversation_shadow_catalog,
)
from anima_server.services.corefs.diary_migration import (
    migration_opaque_id,
    read_prepared_writing_body,
    read_prepared_writing_snapshot,
)
from anima_server.services.corefs.messages import (
    decode_message_segment,
    decode_thread_document,
    merge_conversation_sources,
)
from anima_server.services.corefs.writing_source import (
    WritingSourceBody,
    WritingSourceObjectDescriptor,
    prepare_writing_source_catalog,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def _message(
    *,
    message_id: int | None,
    sequence: int,
    content: str,
    role: str = "user",
) -> dict[str, object]:
    return {
        "id": message_id,
        "thread_id": 4,
        "sequence_id": sequence,
        "role": role,
        "content_text": content,
        "created_at": datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        + timedelta(minutes=sequence),
    }


def test_active_and_archive_overlap_deduplicates_by_stable_or_fallback_identity() -> None:
    active = [_message(message_id=10, sequence=1, content="hello")]
    archived = [
        {
            "id": 10,
            "thread_id": 4,
            "seq": 1,
            "role": "user",
            "content": "hello",
            "ts": "2026-01-01T12:01:00+00:00",
        },
        {
            "thread_id": 4,
            "seq": 2,
            "role": "assistant",
            "content": "welcome",
            "ts": "2026-01-01T12:02:00+00:00",
        },
    ]
    merged = merge_conversation_sources(active=active, archived=archived)
    assert [record.content for record in merged.records] == ["hello", "welcome"]
    assert merged.duplicate_count == 1
    assert merged.conflicts == ()


def test_conflicting_identity_and_unknown_role_are_quarantined_not_chosen() -> None:
    active = [_message(message_id=10, sequence=1, content="first")]
    archived = [
        {
            "id": 10,
            "thread_id": 4,
            "seq": 1,
            "role": "user",
            "content": "different",
            "ts": "2026-01-01T12:01:00+00:00",
        },
        {
            "id": 11,
            "thread_id": 4,
            "seq": 2,
            "role": "developer",
            "content": "hidden",
            "ts": "2026-01-01T12:02:00+00:00",
        },
    ]
    merged = merge_conversation_sources(active=active, archived=archived)
    assert merged.records == ()
    assert len(merged.conflicts) == 2
    assert {conflict.reason for conflict in merged.conflicts} == {
        "identity_conflict",
        "unsupported_role",
    }


def test_internal_rows_are_excluded_without_becoming_conflicts() -> None:
    active = [
        _message(message_id=1, sequence=1, content="visible"),
        {
            **_message(message_id=2, sequence=2, content="thinking", role="assistant"),
            "content_json": {"tool_calls": [{"name": "search"}]},
        },
        {
            **_message(message_id=3, sequence=3, content="raw", role="tool"),
            "tool_name": "search",
        },
    ]
    merged = merge_conversation_sources(active=active)
    assert [record.content for record in merged.records] == ["visible"]
    assert merged.excluded_count == 2
    assert merged.conflicts == ()


def test_shadow_catalog_binds_shared_root_and_builds_thread_segment_chain() -> None:
    thread = {
        "id": 4,
        "title": "Portable conversations",
        "status": "active",
        "created_at": datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        "updated_at": datetime(2026, 1, 1, 12, 3, tzinfo=UTC),
        "last_message_at": datetime(2026, 1, 1, 12, 2, tzinfo=UTC),
    }
    shadow = build_conversation_shadow_catalog(
        user_id=7,
        active_threads=[thread],
        active_messages=[
            _message(message_id=10, sequence=1, content="hello"),
            _message(
                message_id=11,
                sequence=2,
                content="welcome",
                role="assistant",
            ),
        ],
    )
    assert shadow.thread_count == 1
    assert shadow.message_count == 2
    assert shadow.conflicts == ()
    assert len(shadow.folders) == 1
    conversations = shadow.folders[0]
    assert conversations.role == "core.conversations"
    assert (conversations.owner, conversations.agent_access, conversations.policy) == (
        "shared",
        "manage",
        "shared-manage",
    )

    segment_body = next(
        item for item in shadow.objects if item.descriptor.kind == "message-segment"
    )
    segment = decode_message_segment(
        segment_body.body,
        expected_previous_segment_id=None,
        expected_previous_sha256=None,
    )
    assert [event.kind for event in segment.events] == [
        "message.created",
        "message.created",
    ]
    assert [event.sequence for event in segment.events] == [1, 2]

    thread_body = next(item for item in shadow.objects if item.descriptor.kind == "thread")
    document = decode_thread_document(thread_body.body)
    assert document.title == "Portable conversations"
    assert document.status == "active"
    assert document.segment_ids == (segment.segment_id,)
    assert document.segment_sha256 == (segment.sha256,)
    assert document.segment_ranges == ((1, 2),)
    assert document.quarantine == ()


def test_multiple_threads_receive_collision_free_segment_names() -> None:
    first = _message(message_id=1, sequence=1, content="first")
    second = {**_message(message_id=2, sequence=1, content="second"), "thread_id": 5}
    shadow = build_conversation_shadow_catalog(
        user_id=7,
        active_threads=[
            {
                "id": thread_id,
                "title": None,
                "status": "active" if thread_id == 4 else "closed",
                "created_at": datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
                "updated_at": datetime(2026, 1, 1, 12, thread_id, tzinfo=UTC),
                "last_message_at": datetime(2026, 1, 1, 12, thread_id, tzinfo=UTC),
            }
            for thread_id in (4, 5)
        ],
        active_messages=[first, second],
    )
    segment_names = [
        item.descriptor.name
        for item in shadow.objects
        if item.descriptor.kind == "message-segment"
    ]
    assert len(segment_names) == 2
    assert len(set(segment_names)) == 2


def test_visible_attachment_is_canonicalized_and_referenced_without_host_path() -> None:
    attachment_id = migration_opaque_id("image-asset", "8")
    attachment_bytes = b"image-bytes"
    digest = hashlib.sha256(attachment_bytes).hexdigest()
    parent_id = migration_opaque_id("core-folder-role", "core.conversations")
    attachment = WritingSourceBody(
        descriptor=WritingSourceObjectDescriptor(
            stable_id=attachment_id,
            parent_id=parent_id,
            name="attachment.png",
            kind="attachment",
            content_type="image/png",
            body_encoding="binary",
            body_length=len(attachment_bytes),
            content_sha256=digest,
            source_fingerprint_sha256=digest,
            created_at="2026-01-01T12:00:00.000000+00:00",
            updated_at="2026-01-01T12:00:00.000000+00:00",
            revision=1,
            body_source="supplemental",
            source_key="asset:8",
        ),
        body=attachment_bytes,
    )
    message = _message(message_id=10, sequence=1, content="see this")
    message["content_json"] = {
        "attachments": [
            {"assetId": 8, "storagePath": "users/7/private/image.png"}
        ]
    }
    shadow = build_conversation_shadow_catalog(
        user_id=7,
        active_messages=[message],
        attachment_resolver=lambda value: (
            f"corefs://object/{attachment_id}"
            if isinstance(value, dict) and value.get("assetId") == 8
            else None
        ),
        attachment_objects=[attachment],
    )
    assert {item.descriptor.kind for item in shadow.objects} == {
        "attachment",
        "message-segment",
        "thread",
    }
    segment_body = next(
        item for item in shadow.objects if item.descriptor.kind == "message-segment"
    )
    segment = decode_message_segment(
        segment_body.body or b"",
        expected_previous_segment_id=None,
        expected_previous_sha256=None,
    )
    assert segment.events[0].attachment_uris == (
        f"corefs://object/{attachment_id}",
    )
    assert segment_body.descriptor.references == (attachment_id,)
    assert segment_body.body is not None
    assert "storagePath" not in segment_body.body.decode()


def test_conflicts_are_excluded_and_durably_mark_the_thread_degraded() -> None:
    thread = {
        "id": 4,
        "title": None,
        "status": "closed",
        "created_at": datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        "updated_at": datetime(2026, 1, 1, 12, 3, tzinfo=UTC),
        "last_message_at": datetime(2026, 1, 1, 12, 2, tzinfo=UTC),
    }
    shadow = build_conversation_shadow_catalog(
        user_id=7,
        active_threads=[thread],
        active_messages=[_message(message_id=10, sequence=1, content="first")],
        archived_messages=[
            {
                "id": 10,
                "thread_id": 4,
                "seq": 1,
                "role": "user",
                "content": "different",
                "ts": "2026-01-01T12:01:00+00:00",
            }
        ],
    )
    assert shadow.message_count == 0
    assert len(shadow.conflicts) == 1
    thread_body = next(item for item in shadow.objects if item.descriptor.kind == "thread")
    document = decode_thread_document(thread_body.body)
    assert document.status == "degraded"
    assert document.message_count == 0
    assert len(document.quarantine) == 1
    assert document.quarantine[0].reason == "identity_conflict"


def test_native_shadow_publication_and_diary_only_rerun_preserve_conversations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("anima_server.services.core.update_core_manifest", lambda _update: None)
    monkeypatch.setattr(
        "anima_server.services.core.get_manifest_path",
        lambda: tmp_path / "missing-manifest.json",
    )
    engine = create_engine(f"sqlite:///{(tmp_path / 'source.db').as_posix()}")
    Base.metadata.create_all(engine)
    native = anima_core.CorefsSession(
        str(tmp_path / "core"),
        migration_opaque_id("test-core", "conversation-shadow"),
    )
    keys = anima_core.corefs_derive_subkeys(anima_core.corefs_generate_root_key(), 1)
    session = SimpleNamespace(user_id=7, corefs_session=native, corefs_keys=keys)
    shadow = build_conversation_shadow_catalog(
        user_id=7,
        active_threads=[
            {
                "id": 4,
                "title": "Portable",
                "status": "active",
                "created_at": datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
                "updated_at": datetime(2026, 1, 1, 12, 2, tzinfo=UTC),
                "last_message_at": datetime(2026, 1, 1, 12, 1, tzinfo=UTC),
            }
        ],
        active_messages=[_message(message_id=1, sequence=1, content="encrypted")],
    )
    with Session(engine) as db:
        first = prepare_writing_source_catalog(
            session=session,
            db=db,
            supplemental_folders=shadow.folders,
            supplemental_objects=shadow.objects,
        )
    assert first.published is True
    prepared = read_prepared_writing_snapshot(session=session)
    assert {folder.role for folder in prepared.folders} >= {
        "core.journal",
        "core.notes",
        "core.conversations",
    }
    prepared_thread = next(item for item in prepared.objects if item.kind == "thread")
    assert decode_thread_document(
        read_prepared_writing_body(session=session, item=prepared_thread)
    ).title == "Portable"
    monkeypatch.setattr(
        conversation_authority,
        "authenticated_content_authority",
        lambda candidate, *, family: candidate.content_authority,
    )
    canonical_views = list_canonical_threads(
        session=SimpleNamespace(
            user_id=7,
            corefs_session=native,
            corefs_keys=keys,
            content_authority={
                "version": 1,
                "state": "authoritative",
                "families": ["conversations"],
                "generation": first.generation,
                "catalogHash": first.catalog_hash,
            },
        )
    )
    assert len(canonical_views) == 1
    assert canonical_views[0].document.legacy_thread_id == 4
    assert [message.content for message in canonical_views[0].messages] == ["encrypted"]
    assert canonical_views[0].degraded_ranges == ()

    with Session(engine) as db:
        second = prepare_writing_source_catalog(session=session, db=db)
    assert second.published is False
    assert second.generation == first.generation
    after = read_prepared_writing_snapshot(session=session)
    assert {item.stable_id for item in after.objects} == {
        item.stable_id for item in prepared.objects
    }


def test_missing_segment_reports_exact_gap_and_keeps_later_valid_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shadow = build_conversation_shadow_catalog(
        user_id=7,
        active_messages=[
            _message(
                message_id=index,
                sequence=index,
                content=f"message-{index}",
            )
            for index in range(1, 258)
        ],
    )
    thread_body = next(
        item.body
        for item in shadow.objects
        if item.descriptor.kind == "thread"
    )
    thread_document = decode_thread_document(thread_body)
    segments = sorted(
        (
            item
            for item in shadow.objects
            if item.descriptor.kind == "message-segment"
        ),
        key=lambda item: int(item.descriptor.metadata["segmentOrdinal"]),
    )
    later = segments[1]

    monkeypatch.setattr(
        conversation_authority,
        "_walk_all",
        lambda **_kwargs: [
            {
                "stableId": later.descriptor.stable_id,
                "path": "/Conversations/later.jsonl",
                "revision": 1,
            }
        ],
    )
    monkeypatch.setattr(
        conversation_authority,
        "_read_all",
        lambda **kwargs: (
            thread_body
            if kwargs["path"] == "/Conversations/thread.json"
            else later.body
        ),
    )

    view = conversation_authority._read_thread_view(
        session=object(),
        selection=ConversationAuthoritySelection(1, "a" * 64),
        entry={
            "path": "/Conversations/thread.json",
            "stableId": thread_document.thread_id,
            "revision": 1,
        },
    )

    assert [message.sequence for message in view.messages] == [257]
    assert view.degraded_ranges == ((1, 256),)


def test_authority_gate_requires_exact_authenticated_cutover_shape() -> None:
    base = SimpleNamespace(corefs_session=object(), corefs_keys=object())
    assert conversation_authority_selection(base) is None
    assert (
        conversation_authority_selection(
            SimpleNamespace(
                corefs_session=object(),
                corefs_keys=object(),
                content_authority={
                    "version": 1,
                    "state": "validation_readonly",
                    "families": ["conversations"],
                    "generation": 3,
                    "catalogHash": "a" * 64,
                },
            )
        )
        is None
    )
    selection = conversation_authority_selection(
        SimpleNamespace(
            corefs_session=object(),
            corefs_keys=object(),
            content_authority={
                "version": 1,
                "state": "authoritative",
                "families": ["conversations"],
                "generation": 3,
                "catalogHash": "a" * 64,
            },
        )
    )
    assert selection is not None
    assert (selection.generation, selection.catalog_hash) == (3, "a" * 64)


def test_persistence_cannot_bypass_active_authority_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent.persistence import create_thread

    monkeypatch.setattr(
        "anima_server.services.corefs.conversation_authority."
        "active_conversation_authority_session",
        lambda _user_id: object(),
    )
    with pytest.raises(RuntimeError, match="disabled after CoreFS"):
        create_thread(SimpleNamespace(), 7)


def test_cutover_blocks_legacy_transcript_creation_before_directory_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent.transcript_archive import export_transcript

    monkeypatch.setattr(
        "anima_server.services.corefs.conversation_authority."
        "active_conversation_authority_session",
        lambda _user_id: object(),
    )
    transcripts = tmp_path / "transcripts"
    with pytest.raises(RuntimeError, match="disabled after CoreFS"):
        export_transcript(
            messages=[],
            thread_id=1,
            user_id=7,
            dek=b"x" * 32,
            transcripts_dir=transcripts,
        )
    assert not transcripts.exists()


def test_transcript_export_preserves_stable_source_ids_for_deduplication() -> None:
    from anima_server.services.agent.transcript_archive import messages_to_transcript_dicts

    exported = messages_to_transcript_dicts(
        [
            SimpleNamespace(
                id=12,
                thread_id=4,
                role="user",
                tool_name=None,
                content_text="hello",
                content_json=None,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                sequence_id=3,
                source=None,
                tool_call_id=None,
            )
        ]
    )
    assert exported == [
        {
            "id": 12,
            "thread_id": 4,
            "role": "user",
            "content": "hello",
            "ts": "2026-01-01T00:00:00Z",
            "seq": 3,
        }
    ]


def test_collect_sources_merges_legacy_runtime_and_archive_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.db.runtime_base import RuntimeBase
    from anima_server.models.agent_runtime import AgentMessage, AgentThread
    from anima_server.models.runtime import RuntimeMessage, RuntimeThread
    from anima_server.services.corefs.conversation_migration import (
        collect_conversation_shadow_sources,
    )
    from anima_server.services.crypto import encrypt_blob

    soul_engine = create_engine("sqlite://")
    Base.metadata.create_all(soul_engine)
    runtime_engine = create_engine("sqlite://")
    RuntimeBase.metadata.create_all(runtime_engine)
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    dek = b"d" * 32
    body = (
        b'{"id":12,"thread_id":4,"role":"user","content":"runtime",'
        b'"ts":"2026-01-01T12:01:00+00:00","seq":1}\n'
        b'{"id":13,"thread_id":4,"role":"assistant","content":"archived",'
        b'"ts":"2026-01-01T12:02:00+00:00","seq":2}\n'
    )
    encrypted_path = transcripts / "2026-01-01_thread-4.jsonl.enc"
    encrypted_path.write_bytes(
        encrypt_blob(body, dek, aad=b"transcript:4:2026-01-01")
    )
    (transcripts / "2026-01-01_thread-4.meta.json").write_text(
        '{"thread_id":4,"user_id":7,"archived_at":"2026-01-01T12:03:00+00:00"}'
    )
    monkeypatch.setattr(
        "anima_server.services.corefs.conversation_migration."
        "_collect_runtime_message_attachments",
        lambda **_kwargs: ((), lambda _value: None),
    )
    with Session(soul_engine) as soul_db, Session(runtime_engine) as runtime_db:
        legacy_thread = AgentThread(user_id=7, status="closed", title="Legacy")
        soul_db.add(legacy_thread)
        soul_db.flush()
        soul_db.add(
            AgentMessage(
                id=11,
                thread_id=legacy_thread.id,
                sequence_id=1,
                role="user",
                content_text="legacy",
            )
        )
        runtime_thread = RuntimeThread(id=4, user_id=7, status="closed", title="Runtime")
        runtime_db.add(runtime_thread)
        runtime_db.flush()
        runtime_db.add(
            RuntimeMessage(
                id=12,
                thread_id=4,
                user_id=7,
                sequence_id=1,
                role="user",
                content_text="runtime",
                created_at=datetime(2026, 1, 1, 12, 1, tzinfo=UTC),
            )
        )
        soul_db.flush()
        runtime_db.flush()
        shadow = collect_conversation_shadow_sources(
            soul_db=soul_db,
            runtime_db=runtime_db,
            user_id=7,
            transcripts_dir=transcripts,
            dek=dek,
        )
    assert shadow.duplicate_count == 1
    assert shadow.conflicts == ()
    assert shadow.thread_count == 2
    assert shadow.message_count == 3
    threads = [
        decode_thread_document(item.body or b"")
        for item in shadow.objects
        if item.descriptor.kind == "thread"
    ]
    runtime = next(item for item in threads if item.legacy_thread_id == 4)
    assert runtime.title == "Runtime"
    assert runtime.message_count == 2
