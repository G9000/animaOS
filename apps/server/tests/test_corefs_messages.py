from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from anima_server.services.corefs.messages import (
    MAX_MESSAGE_SEGMENT_BYTES,
    MAX_MESSAGE_SEGMENT_EVENTS,
    ConversationFormatError,
    ConversationTailConflict,
    ConversationTailSnapshot,
    MessageConflictError,
    MessageEvent,
    ThreadDocument,
    append_message_event_with_tail_retry,
    canonical_message_projection,
    decode_message_segment,
    decode_thread_document,
    encode_message_segments,
    encode_thread_document,
    reduce_message_events,
    reduce_message_events_resilient,
)


def _event(
    sequence: int,
    *,
    message_id: str | None = None,
    kind: str = "message.created",
    version: int = 1,
    expected_event_id: str | None = None,
    expected_version: int | None = None,
    content: str = "visible",
    created_at: datetime | None = None,
) -> MessageEvent:
    return MessageEvent(
        event_id=f"event-{sequence}-{version}",
        message_id=message_id or f"message-{sequence}",
        legacy_message_id=sequence,
        thread_id="thread-1",
        sequence=sequence,
        kind=kind,
        message_version=version,
        expected_prior_event_id=expected_event_id,
        expected_prior_version=expected_version,
        role="user" if sequence % 2 else "assistant",
        content=content,
        attachment_uris=(),
        created_at=(created_at or datetime(2026, 1, 1, tzinfo=UTC)).isoformat(),
    )


def test_projection_excludes_internal_execution_and_keeps_visible_attachments() -> None:
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    user = canonical_message_projection(
        {
            "id": 7,
            "thread_id": 3,
            "sequence_id": 9,
            "role": "user",
            "content_text": "show me",
            "content_json": {
                "attachments": [
                    {
                        "id": "a1",
                        "corefsUri": "corefs://object/01ARZ3NDEKTSV4RRFFQ69G5FAV",
                    }
                ],
                "retrieval": {"query": "private runtime context"},
                "usage": {"tokens": 12},
            },
            "created_at": created_at,
        }
    )
    assert user is not None
    assert user.role == "user"
    assert user.content == "show me"
    assert user.attachment_uris == ("corefs://object/01ARZ3NDEKTSV4RRFFQ69G5FAV",)
    encoded = json.dumps(user.as_dict(), sort_keys=True)
    assert "private runtime context" not in encoded
    assert "tokens" not in encoded

    assert canonical_message_projection(
        {
            "id": 8,
            "thread_id": 3,
            "sequence_id": 10,
            "role": "assistant",
            "content_text": "hidden chain of thought",
            "content_json": {"tool_calls": [{"name": "search", "arguments": {"q": "x"}}]},
            "created_at": created_at,
        }
    ) is None
    assert canonical_message_projection(
        {
            "id": 9,
            "thread_id": 3,
            "sequence_id": 11,
            "role": "tool",
            "tool_name": "search",
            "content_text": "raw tool output",
            "created_at": created_at,
        }
    ) is None
    visible_tool = canonical_message_projection(
        {
            "id": 10,
            "thread_id": 3,
            "sequence_id": 12,
            "role": "tool",
            "tool_name": "send_message",
            "content_text": "final answer",
            "created_at": created_at,
        }
    )
    assert visible_tool is not None
    assert visible_tool.role == "assistant"
    assert visible_tool.content == "final answer"


def test_projection_rejects_unknown_roles_and_unresolved_attachments() -> None:
    base = {
        "id": 1,
        "thread_id": 2,
        "sequence_id": 1,
        "content_text": "x",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    with pytest.raises(ConversationFormatError, match="role"):
        canonical_message_projection({**base, "role": "developer"})
    with pytest.raises(ConversationFormatError, match="attachment"):
        canonical_message_projection(
            {
                **base,
                "role": "user",
                "content_json": {"attachments": [{"id": "legacy-only"}]},
            }
        )


def test_event_reducer_uses_sequence_not_timestamp_and_enforces_terminal_delete() -> None:
    later = datetime(2026, 1, 2, tzinfo=UTC)
    earlier = later - timedelta(days=1)
    events = [
        _event(2, created_at=earlier),
        _event(1, created_at=later),
        _event(
            1,
            message_id="message-1",
            kind="message.edited",
            version=2,
            expected_event_id="event-1-1",
            expected_version=1,
            content="edited",
        ),
        _event(
            2,
            message_id="message-2",
            kind="message.deleted",
            version=2,
            expected_event_id="event-2-1",
            expected_version=1,
            content="",
        ),
    ]
    reduced = reduce_message_events(events)
    assert [(message.sequence, message.content) for message in reduced] == [(1, "edited")]

    with pytest.raises(MessageConflictError, match="terminal") as conflict:
        reduce_message_events(
            [
                *events,
                _event(
                    2,
                    message_id="message-2",
                    kind="message.edited",
                    version=3,
                    expected_event_id="event-2-2",
                    expected_version=2,
                ),
            ]
        )
    assert conflict.value.current_event_id == "event-2-2"
    assert conflict.value.current_version == 2
    assert conflict.value.current_state == "deleted"


def test_event_reducer_rejects_stale_or_conflicting_versions() -> None:
    with pytest.raises(MessageConflictError, match="precondition"):
        reduce_message_events(
            [
                _event(1),
                _event(
                    1,
                    message_id="message-1",
                    kind="message.edited",
                    version=2,
                    expected_event_id="wrong",
                    expected_version=1,
                ),
            ]
        )
    with pytest.raises(ConversationFormatError, match="sequence"):
        reduce_message_events([_event(1), _event(1, message_id="other")])


def test_resilient_reducer_keeps_independent_messages_after_missing_history() -> None:
    messages, degraded = reduce_message_events_resilient(
        [
            _event(
                1,
                kind="message.edited",
                version=2,
                expected_event_id="missing-create",
                expected_version=1,
            ),
            _event(2),
        ]
    )

    assert [message.sequence for message in messages] == [2]
    assert degraded == ((1, 1),)


def test_segments_roll_at_256_events_and_form_a_verified_hash_chain() -> None:
    events = [_event(index + 1) for index in range(MAX_MESSAGE_SEGMENT_EVENTS + 1)]
    segments = encode_message_segments(events)
    assert [segment.event_count for segment in segments] == [256, 1]
    assert len(segments[0].data) <= MAX_MESSAGE_SEGMENT_BYTES
    assert segments[1].previous_segment_sha256 == segments[0].sha256

    first = decode_message_segment(segments[0].data, expected_previous_sha256=None)
    second = decode_message_segment(
        segments[1].data,
        expected_previous_segment_id=first.segment_id,
        expected_previous_sha256=first.sha256,
    )
    assert first.events[-1].sequence == 256
    assert second.events[0].sequence == 257


def test_segments_roll_before_one_mib_and_reject_corruption_or_chain_gaps() -> None:
    content = "x" * 300_000
    segments = encode_message_segments(
        [_event(index + 1, content=content) for index in range(5)]
    )
    assert len(segments) > 1
    assert all(len(segment.data) <= MAX_MESSAGE_SEGMENT_BYTES for segment in segments)

    damaged = bytearray(segments[0].data)
    damaged[-2] ^= 1
    with pytest.raises(ConversationFormatError):
        decode_message_segment(bytes(damaged), expected_previous_sha256=None)
    with pytest.raises(ConversationFormatError, match="chain"):
        decode_message_segment(
            segments[-1].data,
            expected_previous_segment_id=segments[-1].previous_segment_id,
            expected_previous_sha256="0" * 64,
        )


def test_concurrent_tail_conflict_reloads_and_reassigns_the_next_sequence() -> None:
    first = _event(1)
    state = {
        "events": [first],
        "tail": encode_message_segments([first])[0],
        "commits": 0,
    }

    def load_tail() -> ConversationTailSnapshot:
        return ConversationTailSnapshot(
            thread_id="thread-1",
            events=tuple(state["events"]),
            tail=state["tail"],
        )

    def event_factory(snapshot: ConversationTailSnapshot) -> MessageEvent:
        next_sequence = max(event.sequence for event in snapshot.events) + 1
        return _event(next_sequence)

    def commit(plan) -> None:
        state["commits"] += 1
        if state["commits"] == 1:
            concurrent = _event(2, message_id="concurrent-message")
            state["events"].append(concurrent)
            state["tail"] = encode_message_segments(state["events"])[0]
            raise ConversationTailConflict("tail changed")
        assert plan.expected_tail_sha256 == state["tail"].sha256
        state["events"].append(plan.event)
        state["tail"] = plan.segment

    plan = append_message_event_with_tail_retry(
        load_tail=load_tail,
        event_factory=event_factory,
        commit=commit,
    )
    assert state["commits"] == 2
    assert plan.event.sequence == 3
    assert plan.replaces_tail is True
    assert [message.sequence for message in reduce_message_events(state["events"])] == [1, 2, 3]


def test_full_tail_rolls_to_a_new_segment_instead_of_rewriting_it() -> None:
    events = [_event(index + 1) for index in range(MAX_MESSAGE_SEGMENT_EVENTS)]
    tail = encode_message_segments(events)[0]
    committed = []
    plan = append_message_event_with_tail_retry(
        load_tail=lambda: ConversationTailSnapshot(
            thread_id="thread-1",
            events=tuple(events),
            tail=tail,
        ),
        event_factory=lambda _snapshot: _event(MAX_MESSAGE_SEGMENT_EVENTS + 1),
        commit=committed.append,
    )
    assert plan.replaces_tail is False
    assert plan.segment.index == 1
    assert plan.segment.previous_segment_id == tail.segment_id
    assert plan.segment.previous_segment_sha256 == tail.sha256
    assert committed == [plan]


def test_thread_document_round_trips_exact_segment_inventory() -> None:
    document = ThreadDocument(
        thread_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        legacy_thread_id=42,
        title="Hello",
        status="active",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:01:00+00:00",
        last_message_at="2026-01-01T00:01:00+00:00",
        closed_at=None,
        is_archived=False,
        segment_ids=("01ARZ3NDEKTSV4RRFFQ69G5FAW",),
        segment_sha256=("1" * 64,),
        segment_ranges=((1, 2),),
        message_count=2,
    )
    encoded = encode_thread_document(document)
    assert decode_thread_document(encoded) == document
    with pytest.raises(ConversationFormatError, match="inventory"):
        encode_thread_document(replace(document, segment_sha256=()))
