from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from anima_server.services.corefs.conversation_authority import (
    conversation_authority_selection,
)
from anima_server.services.corefs.messages import (
    decode_message_segment,
    decode_thread_document,
    merge_conversation_sources,
)
from corefs_catalog_test_support import (
    migration_opaque_id,
)
from corefs_conversation_test_support import (
    build_conversation_shadow_catalog,
)
from corefs_writing_fixture_support import WritingSourceBody, WritingSourceObjectDescriptor


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
        "created_at": datetime(2026, 1, 1, 12, 0, tzinfo=UTC) + timedelta(minutes=sequence),
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
        item.descriptor.name for item in shadow.objects if item.descriptor.kind == "message-segment"
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
        "attachments": [{"assetId": 8, "storagePath": "users/7/private/image.png"}]
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
    assert segment.events[0].attachment_uris == (f"corefs://object/{attachment_id}",)
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
        "anima_server.services.corefs.conversation_authority.active_conversation_authority_session",
        lambda _user_id: object(),
    )
    with pytest.raises(RuntimeError, match="disabled after CoreFS"):
        create_thread(SimpleNamespace(), 7)
