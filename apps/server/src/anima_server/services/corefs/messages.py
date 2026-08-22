"""Canonical conversation projection and immutable message-segment codecs."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

MESSAGE_SEGMENT_FORMAT_VERSION = 1
MESSAGE_SEGMENT_CONTENT_TYPE = "application/vnd.anima.message-segment+jsonl;version=1"
THREAD_CONTENT_TYPE = "application/vnd.anima.thread+json;version=1"
MAX_MESSAGE_SEGMENT_EVENTS = 256
MAX_MESSAGE_SEGMENT_BYTES = 1024 * 1024

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_CORE_OBJECT_URI = re.compile(r"corefs://object/[0-7][0-9A-HJKMNP-TV-Z]{25}")
_EVENT_KINDS = frozenset({"message.created", "message.edited", "message.deleted"})
_VISIBLE_ROLES = frozenset({"user", "assistant"})
_INTERNAL_ROLES = frozenset({"system", "approval", "summary"})


class ConversationFormatError(ValueError):
    """A legacy or canonical conversation record is unsafe or inconsistent."""


class MessageConflictError(ConversationFormatError):
    """An event precondition conflicts with the current canonical message."""

    def __init__(
        self,
        message: str,
        *,
        current_event_id: str | None,
        current_version: int,
        current_state: str,
    ) -> None:
        super().__init__(message)
        self.current_event_id = current_event_id
        self.current_version = current_version
        self.current_state = current_state


@dataclass(frozen=True, slots=True)
class CanonicalMessageRecord:
    message_id: str
    thread_id: str
    sequence: int
    role: Literal["user", "assistant"]
    content: str
    attachment_uris: tuple[str, ...]
    created_at: str
    stable_source_id: str | None
    fallback_identity: str

    def as_dict(self) -> dict[str, object]:
        return {
            "messageId": self.message_id,
            "threadId": self.thread_id,
            "sequence": self.sequence,
            "role": self.role,
            "content": self.content,
            "attachmentUris": list(self.attachment_uris),
            "createdAt": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class MessageEvent:
    event_id: str
    message_id: str
    legacy_message_id: int | str | None
    thread_id: str
    sequence: int
    kind: Literal["message.created", "message.edited", "message.deleted"] | str
    message_version: int
    expected_prior_event_id: str | None
    expected_prior_version: int | None
    role: Literal["user", "assistant"] | str
    content: str
    attachment_uris: tuple[str, ...]
    created_at: str

    def as_dict(self) -> dict[str, object]:
        _validate_event(self)
        return {
            "eventId": self.event_id,
            "messageId": self.message_id,
            "legacyMessageId": self.legacy_message_id,
            "threadId": self.thread_id,
            "sequence": self.sequence,
            "type": self.kind,
            "messageVersion": self.message_version,
            "expectedPriorEventId": self.expected_prior_event_id,
            "expectedPriorVersion": self.expected_prior_version,
            "role": self.role,
            "content": self.content,
            "attachmentUris": list(self.attachment_uris),
            "createdAt": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class ReducedMessage:
    message_id: str
    legacy_message_id: int | str | None
    thread_id: str
    sequence: int
    version: int
    current_event_id: str
    role: str
    content: str
    attachment_uris: tuple[str, ...]
    created_at: str


@dataclass(frozen=True, slots=True)
class EncodedMessageSegment:
    segment_id: str
    index: int
    previous_segment_id: str | None
    previous_segment_sha256: str | None
    sha256: str
    event_count: int
    events: tuple[MessageEvent, ...]
    data: bytes


@dataclass(frozen=True, slots=True)
class ConversationTailSnapshot:
    thread_id: str
    events: tuple[MessageEvent, ...]
    tail: EncodedMessageSegment | None


@dataclass(frozen=True, slots=True)
class ConversationAppendPlan:
    event: MessageEvent
    expected_tail_id: str | None
    expected_tail_sha256: str | None
    segment: EncodedMessageSegment
    replaces_tail: bool


class ConversationTailConflict(RuntimeError):
    """The committed thread tail changed after an append plan was built."""


@dataclass(frozen=True, slots=True)
class ConversationConflict:
    reason: str
    source: str
    identity: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class ConversationMergeResult:
    records: tuple[CanonicalMessageRecord, ...]
    conflicts: tuple[ConversationConflict, ...]
    duplicate_count: int
    excluded_count: int


@dataclass(frozen=True, slots=True)
class ThreadDocument:
    thread_id: str
    legacy_thread_id: int | str | None
    title: str | None
    status: str
    created_at: str
    updated_at: str
    last_message_at: str | None
    closed_at: str | None
    is_archived: bool
    segment_ids: tuple[str, ...]
    segment_sha256: tuple[str, ...]
    segment_ranges: tuple[tuple[int, int], ...]
    message_count: int
    quarantine: tuple[ConversationConflict, ...] = ()


def encode_thread_document(document: ThreadDocument) -> bytes:
    _validate_thread_document(document)
    return _canonical_json(
        {
            "format": "anima.thread",
            "version": 1,
            "threadId": document.thread_id,
            "legacyThreadId": document.legacy_thread_id,
            "title": document.title,
            "status": document.status,
            "createdAt": document.created_at,
            "updatedAt": document.updated_at,
            "lastMessageAt": document.last_message_at,
            "closedAt": document.closed_at,
            "isArchived": document.is_archived,
            "segmentIds": list(document.segment_ids),
            "segmentSha256": list(document.segment_sha256),
            "segmentRanges": [
                {"firstSequence": first, "lastSequence": last}
                for first, last in document.segment_ranges
            ],
            "messageCount": document.message_count,
            "quarantine": [
                {
                    "reason": item.reason,
                    "source": item.source,
                    "identity": item.identity,
                    "detail": item.detail,
                }
                for item in document.quarantine
            ],
        }
    )


def decode_thread_document(data: bytes) -> ThreadDocument:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConversationFormatError("thread document is invalid JSON") from exc
    if not isinstance(value, dict) or value.get("format") != "anima.thread" or value.get(
        "version"
    ) != 1:
        raise ConversationFormatError("thread document format is unsupported")
    try:
        segment_ids = value["segmentIds"]
        segment_hashes = value["segmentSha256"]
        segment_ranges = value["segmentRanges"]
        if not isinstance(segment_ids, list) or not all(
            isinstance(item, str) for item in segment_ids
        ):
            raise TypeError
        if not isinstance(segment_hashes, list) or not all(
            isinstance(item, str) for item in segment_hashes
        ):
            raise TypeError
        if not isinstance(segment_ranges, list):
            raise TypeError
        quarantine = value["quarantine"]
        if not isinstance(quarantine, list):
            raise TypeError
        document = ThreadDocument(
            thread_id=value["threadId"],
            legacy_thread_id=value["legacyThreadId"],
            title=value["title"],
            status=value["status"],
            created_at=value["createdAt"],
            updated_at=value["updatedAt"],
            last_message_at=value["lastMessageAt"],
            closed_at=value["closedAt"],
            is_archived=value["isArchived"],
            segment_ids=tuple(segment_ids),
            segment_sha256=tuple(segment_hashes),
            segment_ranges=tuple(
                (item["firstSequence"], item["lastSequence"])
                for item in segment_ranges
                if isinstance(item, dict)
            ),
            message_count=value["messageCount"],
            quarantine=tuple(_conflict_from_dict(item) for item in quarantine),
        )
    except (KeyError, TypeError) as exc:
        raise ConversationFormatError("thread document is invalid") from exc
    _validate_thread_document(document)
    if encode_thread_document(document) != data:
        raise ConversationFormatError("thread document encoding is not canonical")
    return document


def message_segment_references(segment: EncodedMessageSegment) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            [
                *(
                    [segment.previous_segment_id]
                    if segment.previous_segment_id is not None
                    else []
                ),
                *(
                    uri.rsplit("/", 1)[-1]
                    for event in segment.events
                    for uri in event.attachment_uris
                ),
            ]
        )
    )


def canonical_message_projection(
    source: object,
    *,
    attachment_resolver: Any | None = None,
) -> CanonicalMessageRecord | None:
    """Project one source row to the exact user-visible conversation record.

    Known execution-only roles and wrappers return ``None``. Unknown roles,
    malformed visible blocks, and unresolved attachments fail closed so a
    caller can quarantine them rather than silently copying or dropping data.
    """
    role = _required_source_string(source, "role")
    tool_name = _optional_source_string(source, "tool_name", "toolName")
    content_json = _source_value(source, "content_json", "contentJson")
    if content_json is not None and not isinstance(content_json, Mapping):
        raise ConversationFormatError("message content metadata is invalid")

    if role in _INTERNAL_ROLES:
        return None
    if role == "assistant" and isinstance(content_json, Mapping):
        tool_calls = content_json.get("tool_calls", content_json.get("toolCalls"))
        if isinstance(tool_calls, list) and tool_calls:
            return None
    if role == "tool":
        if tool_name != "send_message":
            return None
        role = "assistant"
    elif role not in _VISIBLE_ROLES:
        raise ConversationFormatError(f"unsupported message role: {role}")

    thread_source = _source_value(source, "thread_id", "threadId")
    if isinstance(thread_source, bool) or not isinstance(thread_source, (int, str)):
        raise ConversationFormatError("message thread identity is invalid")
    thread_source_text = str(thread_source).strip()
    if not thread_source_text:
        raise ConversationFormatError("message thread identity is invalid")
    sequence = _required_positive_int(source, "sequence_id", "seq", "sequence")
    created_at = _canonical_timestamp(_source_value(source, "created_at", "ts", "createdAt"))
    raw_content = _source_value(source, "content_text", "content", "contentText")
    if raw_content is None:
        content = ""
    elif isinstance(raw_content, str):
        content = raw_content
    else:
        raise ConversationFormatError("visible message content is invalid")

    raw_attachments: object = None
    if isinstance(content_json, Mapping):
        raw_attachments = content_json.get("attachments")
    if raw_attachments is None:
        raw_attachments = _source_value(source, "attachments")
    attachment_uris = _project_attachment_uris(
        raw_attachments,
        resolver=attachment_resolver,
    )

    raw_id = _source_value(source, "id", "message_id", "messageId")
    stable_source_id: str | None = None
    if not isinstance(raw_id, bool) and isinstance(raw_id, (int, str)):
        candidate = str(raw_id).strip()
        stable_source_id = candidate or None

    fallback_payload = {
        "thread": thread_source_text,
        "sequence": sequence,
        "role": role,
        "createdAt": created_at,
        "contentSha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "attachments": list(attachment_uris),
    }
    fallback_identity = hashlib.sha256(_canonical_json(fallback_payload)).hexdigest()
    identity_source = (
        f"{thread_source_text}:{stable_source_id}"
        if stable_source_id is not None
        else fallback_identity
    )
    return CanonicalMessageRecord(
        message_id=_opaque_id("conversation-message", identity_source),
        thread_id=_opaque_id("conversation-thread", thread_source_text),
        sequence=sequence,
        role=role,  # type: ignore[arg-type]
        content=content,
        attachment_uris=attachment_uris,
        created_at=created_at,
        stable_source_id=stable_source_id,
        fallback_identity=fallback_identity,
    )


def reduce_message_events(events: Iterable[MessageEvent]) -> tuple[ReducedMessage, ...]:
    """Apply optimistic message events and return the visible sequence order."""
    messages, degraded = _reduce_message_events(events, tolerate_conflicts=False)
    assert not degraded
    return messages


def reduce_message_events_resilient(
    events: Iterable[MessageEvent],
) -> tuple[tuple[ReducedMessage, ...], tuple[tuple[int, int], ...]]:
    """Keep independent valid messages when earlier event history is unavailable."""
    return _reduce_message_events(events, tolerate_conflicts=True)


def _reduce_message_events(
    events: Iterable[MessageEvent],
    *,
    tolerate_conflicts: bool,
) -> tuple[tuple[ReducedMessage, ...], tuple[tuple[int, int], ...]]:
    state: dict[str, ReducedMessage] = {}
    deleted: set[str] = set()
    deleted_events: dict[str, tuple[str, int]] = {}
    sequence_owner: dict[int, str] = {}
    degraded: list[tuple[int, int]] = []
    for event in events:
        try:
            _apply_message_event(
                event=event,
                state=state,
                deleted=deleted,
                deleted_events=deleted_events,
                sequence_owner=sequence_owner,
            )
        except ConversationFormatError:
            if not tolerate_conflicts:
                raise
            degraded.append((event.sequence, event.sequence))
    return (
        tuple(sorted(state.values(), key=lambda item: (item.sequence, item.message_id))),
        tuple(degraded),
    )


def _apply_message_event(
    *,
    event: MessageEvent,
    state: dict[str, ReducedMessage],
    deleted: set[str],
    deleted_events: dict[str, tuple[str, int]],
    sequence_owner: dict[int, str],
) -> None:
    _validate_event(event)
    owner = sequence_owner.get(event.sequence)
    if owner is not None and owner != event.message_id:
        raise ConversationFormatError("message sequence belongs to another identity")
    if event.message_id in deleted:
        current_event_id, current_version = deleted_events[event.message_id]
        raise MessageConflictError(
            "message deletion is terminal",
            current_event_id=current_event_id,
            current_version=current_version,
            current_state="deleted",
        )
    current = state.get(event.message_id)
    current_version = current.version if current is not None else 0
    current_event_id = current.current_event_id if current is not None else None
    if (
        event.expected_prior_version != (current_version or None)
        or event.expected_prior_event_id != current_event_id
    ):
        raise MessageConflictError(
            "message event precondition is stale",
            current_event_id=current_event_id,
            current_version=current_version,
            current_state="live" if current is not None else "missing",
        )
    if event.message_version != current_version + 1:
        raise ConversationFormatError("message event version is not monotonic")
    if event.kind == "message.created":
        if current is not None or current_version != 0:
            raise ConversationFormatError("message create conflicts with existing state")
    elif current is None:
        raise ConversationFormatError("message transition has no create event")
    elif (
        current.thread_id != event.thread_id
        or current.sequence != event.sequence
        or current.role != event.role
        or current.legacy_message_id != event.legacy_message_id
    ):
        raise ConversationFormatError("message transition changes immutable identity fields")

    sequence_owner[event.sequence] = event.message_id
    if event.kind == "message.deleted":
        state.pop(event.message_id, None)
        deleted.add(event.message_id)
        deleted_events[event.message_id] = (event.event_id, event.message_version)
        return
    state[event.message_id] = ReducedMessage(
        message_id=event.message_id,
        legacy_message_id=event.legacy_message_id,
        thread_id=event.thread_id,
        sequence=event.sequence,
        version=event.message_version,
        current_event_id=event.event_id,
        role=event.role,
        content=event.content,
        attachment_uris=event.attachment_uris,
        created_at=event.created_at,
    )


def encode_message_segments(
    events: Iterable[MessageEvent],
    *,
    starting_ordinal: int = 0,
    previous_segment_id: str | None = None,
    previous_segment_sha256: str | None = None,
) -> tuple[EncodedMessageSegment, ...]:
    """Split events at the 256-event/1-MiB closed segment boundaries."""
    if previous_segment_sha256 is not None and _SHA256_HEX.fullmatch(
        previous_segment_sha256
    ) is None:
        raise ConversationFormatError("previous message segment hash is invalid")
    if starting_ordinal < 0:
        raise ConversationFormatError("starting message segment ordinal is invalid")
    if (previous_segment_id is None) != (previous_segment_sha256 is None):
        raise ConversationFormatError("previous message segment identity is incomplete")
    result: list[EncodedMessageSegment] = []
    current: list[MessageEvent] = []
    previous_id = previous_segment_id
    previous_hash = previous_segment_sha256
    thread_id: str | None = None
    for event in events:
        _validate_event(event)
        if thread_id is None:
            thread_id = event.thread_id
        elif event.thread_id != thread_id:
            raise ConversationFormatError("message segment events span multiple threads")
        ordinal = starting_ordinal + len(result)
        candidate = [*current, event]
        candidate_data = _encode_segment_data(
            candidate,
            index=ordinal,
            previous_segment_id=previous_id,
            previous_sha256=previous_hash,
        )
        if current and (
            len(candidate) > MAX_MESSAGE_SEGMENT_EVENTS
            or len(candidate_data) > MAX_MESSAGE_SEGMENT_BYTES
        ):
            segment = _finalize_segment(
                current,
                index=ordinal,
                previous_segment_id=previous_id,
                previous_sha256=previous_hash,
            )
            result.append(segment)
            previous_id = segment.segment_id
            previous_hash = segment.sha256
            current = [event]
            single = _encode_segment_data(
                current,
                index=starting_ordinal + len(result),
                previous_segment_id=previous_id,
                previous_sha256=previous_hash,
            )
            if len(single) > MAX_MESSAGE_SEGMENT_BYTES:
                raise ConversationFormatError("one message event exceeds the segment byte limit")
        else:
            current = candidate
    if current:
        result.append(
            _finalize_segment(
                current,
                index=starting_ordinal + len(result),
                previous_segment_id=previous_id,
                previous_sha256=previous_hash,
            )
        )
    return tuple(result)


def append_message_event_with_tail_retry(
    *,
    load_tail: Callable[[], ConversationTailSnapshot],
    event_factory: Callable[[ConversationTailSnapshot], MessageEvent],
    commit: Callable[[ConversationAppendPlan], None],
    max_attempts: int = 4,
) -> ConversationAppendPlan:
    """Plan and commit one event with exact-tail retry semantics.

    The callback boundary is intentionally storage-agnostic. PCF-008 can bind
    ``commit`` to the native Core-wide catalog transaction without duplicating
    rollover or CAS behavior.
    """
    if max_attempts < 1:
        raise ValueError("message append attempts must be positive")
    last_conflict: ConversationTailConflict | None = None
    for _attempt in range(max_attempts):
        snapshot = load_tail()
        event = event_factory(snapshot)
        if event.thread_id != snapshot.thread_id:
            raise ConversationFormatError("message append thread identity is invalid")
        reduce_message_events([*snapshot.events, event])
        plan = _plan_tail_append(snapshot=snapshot, event=event)
        try:
            commit(plan)
        except ConversationTailConflict as exc:
            last_conflict = exc
            continue
        return plan
    raise last_conflict or ConversationTailConflict("message append tail did not stabilize")


def _plan_tail_append(
    *,
    snapshot: ConversationTailSnapshot,
    event: MessageEvent,
) -> ConversationAppendPlan:
    tail = snapshot.tail
    if tail is None:
        segments = encode_message_segments([event])
        return ConversationAppendPlan(
            event=event,
            expected_tail_id=None,
            expected_tail_sha256=None,
            segment=segments[0],
            replaces_tail=False,
        )
    if not tail.events or tail.events[-1].thread_id != snapshot.thread_id:
        raise ConversationFormatError("message tail snapshot is invalid")
    segments = encode_message_segments(
        [*tail.events, event],
        starting_ordinal=tail.index,
        previous_segment_id=tail.previous_segment_id,
        previous_segment_sha256=tail.previous_segment_sha256,
    )
    if len(segments) == 1:
        return ConversationAppendPlan(
            event=event,
            expected_tail_id=tail.segment_id,
            expected_tail_sha256=tail.sha256,
            segment=segments[0],
            replaces_tail=True,
        )
    if len(segments) != 2 or segments[0].data != tail.data:
        raise ConversationFormatError("message tail rollover is inconsistent")
    return ConversationAppendPlan(
        event=event,
        expected_tail_id=tail.segment_id,
        expected_tail_sha256=tail.sha256,
        segment=segments[1],
        replaces_tail=False,
    )


def decode_message_segment(
    data: bytes,
    *,
    expected_previous_segment_id: str | None = None,
    expected_previous_sha256: str | None,
) -> EncodedMessageSegment:
    if not data or len(data) > MAX_MESSAGE_SEGMENT_BYTES:
        raise ConversationFormatError("message segment byte length is invalid")
    try:
        lines = data.decode("utf-8").splitlines()
        header = json.loads(lines[0])
        raw_events = [json.loads(line) for line in lines[1:]]
    except (IndexError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConversationFormatError("message segment JSONL is invalid") from exc
    if not isinstance(header, dict):
        raise ConversationFormatError("message segment header is invalid")
    if (
        header.get("format") != "anima.message-segment"
        or header.get("version") != MESSAGE_SEGMENT_FORMAT_VERSION
    ):
        raise ConversationFormatError("message segment format is unsupported")
    index = header.get("segmentIndex")
    event_count = header.get("eventCount")
    previous_id = header.get("previousSegmentId")
    previous = header.get("previousSegmentSha256")
    first_sequence = header.get("firstSequence")
    last_sequence = header.get("lastSequence")
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ConversationFormatError("message segment index is invalid")
    if event_count != len(raw_events) or not 0 < len(raw_events) <= MAX_MESSAGE_SEGMENT_EVENTS:
        raise ConversationFormatError("message segment event count is invalid")
    if previous_id is not None and not isinstance(previous_id, str):
        raise ConversationFormatError("message segment chain identity is invalid")
    if (previous_id is None) != (previous is None):
        raise ConversationFormatError("message segment chain identity is incomplete")
    if previous is not None and (
        not isinstance(previous, str) or _SHA256_HEX.fullmatch(previous) is None
    ):
        raise ConversationFormatError("message segment chain hash is invalid")
    if (
        previous_id != expected_previous_segment_id
        or previous != expected_previous_sha256
    ):
        raise ConversationFormatError("message segment chain is discontinuous")
    events = tuple(_event_from_dict(value) for value in raw_events)
    if first_sequence != min(event.sequence for event in events) or last_sequence != max(
        event.sequence for event in events
    ):
        raise ConversationFormatError("message segment sequence range is invalid")
    thread_ids = {event.thread_id for event in events}
    if len(thread_ids) != 1 or header.get("threadId") not in thread_ids:
        raise ConversationFormatError("message segment thread identity is invalid")
    canonical = _encode_segment_data(
        events,
        index=index,
        previous_segment_id=previous_id,
        previous_sha256=previous,
    )
    if canonical != data:
        raise ConversationFormatError("message segment encoding is not canonical")
    return EncodedMessageSegment(
        segment_id=_opaque_id("conversation-segment", f"{events[0].thread_id}:{index}"),
        index=index,
        previous_segment_id=previous_id,
        previous_segment_sha256=previous,
        sha256=hashlib.sha256(data).hexdigest(),
        event_count=len(events),
        events=events,
        data=data,
    )


def merge_conversation_sources(
    *,
    active: Iterable[object] = (),
    archived: Iterable[object] = (),
    legacy: Iterable[object] = (),
    attachment_resolver: Any | None = None,
) -> ConversationMergeResult:
    """Merge all known source families without silently choosing conflicts."""
    accepted: dict[str, CanonicalMessageRecord] = {}
    identity_to_primary: dict[str, str] = {}
    blocked: set[str] = set()
    conflicts: list[ConversationConflict] = []
    duplicate_count = 0
    excluded_count = 0

    for source_name, rows in (
        ("legacy", legacy),
        ("active", active),
        ("archived", archived),
    ):
        for row in rows:
            try:
                record = canonical_message_projection(
                    row,
                    attachment_resolver=attachment_resolver,
                )
            except ConversationFormatError as exc:
                reason = "unsupported_role" if "role" in str(exc) else "invalid_projection"
                conflicts.append(
                    ConversationConflict(
                        reason=reason,
                        source=source_name,
                        identity=_source_conflict_identity(row),
                        detail=str(exc),
                    )
                )
                continue
            if record is None:
                excluded_count += 1
                continue
            identities = [f"fallback:{record.fallback_identity}"]
            if record.stable_source_id is not None:
                identities.insert(
                    0,
                    f"stable:{record.thread_id}:{record.stable_source_id}",
                )
            existing_primary = next(
                (
                    identity_to_primary[identity]
                    for identity in identities
                    if identity in identity_to_primary
                ),
                None,
            )
            if existing_primary is None and not any(identity in blocked for identity in identities):
                primary = identities[0]
                accepted[primary] = record
                for identity in identities:
                    identity_to_primary[identity] = primary
                continue
            if existing_primary is not None:
                existing = accepted.get(existing_primary)
                if existing is not None and _record_fingerprint(existing) == _record_fingerprint(record):
                    duplicate_count += 1
                    for identity in identities:
                        identity_to_primary.setdefault(identity, existing_primary)
                    continue
                if existing is not None:
                    accepted.pop(existing_primary, None)
                conflict_identity = existing_primary
            else:
                conflict_identity = identities[0]
            for identity in identities:
                blocked.add(identity)
                identity_to_primary.pop(identity, None)
            conflicts.append(
                ConversationConflict(
                    reason="identity_conflict",
                    source=source_name,
                    identity=conflict_identity,
                    detail="same message identity has different canonical content",
                )
            )

    return ConversationMergeResult(
        records=tuple(
            sorted(accepted.values(), key=lambda item: (item.sequence, item.message_id))
        ),
        conflicts=tuple(conflicts),
        duplicate_count=duplicate_count,
        excluded_count=excluded_count,
    )


def _record_fingerprint(record: CanonicalMessageRecord) -> bytes:
    return _canonical_json(record.as_dict())


def _source_conflict_identity(source: object) -> str | None:
    raw_thread_id = _source_value(source, "thread_id", "threadId")
    if isinstance(raw_thread_id, bool) or not isinstance(raw_thread_id, (int, str)):
        return None
    thread_text = str(raw_thread_id).strip()
    if not thread_text:
        return None
    thread_id = _opaque_id("conversation-thread", thread_text)
    raw_message_id = _source_value(source, "id", "message_id", "messageId")
    if not isinstance(raw_message_id, bool) and isinstance(raw_message_id, (int, str)):
        message_text = str(raw_message_id).strip()
        if message_text:
            return f"stable:{thread_id}:{message_text}"
    return thread_id


def _validate_thread_document(document: ThreadDocument) -> None:
    if not isinstance(document.thread_id, str) or not document.thread_id:
        raise ConversationFormatError("thread document identity is invalid")
    if (
        document.legacy_thread_id is not None
        and (
            isinstance(document.legacy_thread_id, bool)
            or not isinstance(document.legacy_thread_id, (int, str))
            or not str(document.legacy_thread_id)
        )
    ):
        raise ConversationFormatError("thread legacy identity is invalid")
    if document.title is not None and not isinstance(document.title, str):
        raise ConversationFormatError("thread document title is invalid")
    if not isinstance(document.status, str) or document.status not in {
        "active",
        "closed",
        "archived",
        "deleted",
        "degraded",
    }:
        raise ConversationFormatError("thread document status is invalid")
    for value in (document.created_at, document.updated_at):
        if not isinstance(value, str) or not value:
            raise ConversationFormatError("thread document timestamp is invalid")
    for value in (document.last_message_at, document.closed_at):
        if value is not None and not isinstance(value, str):
            raise ConversationFormatError("thread document timestamp is invalid")
    if not isinstance(document.is_archived, bool):
        raise ConversationFormatError("thread archive state is invalid")
    if not (
        len(document.segment_ids)
        == len(document.segment_sha256)
        == len(document.segment_ranges)
    ):
        raise ConversationFormatError("thread segment inventory is inconsistent")
    for stable_id in document.segment_ids:
        if not isinstance(stable_id, str) or not stable_id:
            raise ConversationFormatError("thread segment identity is invalid")
    for digest in document.segment_sha256:
        if _SHA256_HEX.fullmatch(digest) is None:
            raise ConversationFormatError("thread segment hash is invalid")
    for first, last in document.segment_ranges:
        if (
            isinstance(first, bool)
            or not isinstance(first, int)
            or isinstance(last, bool)
            or not isinstance(last, int)
            or first < 1
            or last < first
        ):
            raise ConversationFormatError("thread segment range is invalid")
    if (
        isinstance(document.message_count, bool)
        or not isinstance(document.message_count, int)
        or document.message_count < 0
    ):
        raise ConversationFormatError("thread message count is invalid")
    for conflict in document.quarantine:
        if (
            not isinstance(conflict, ConversationConflict)
            or not conflict.reason
            or not conflict.source
            or not isinstance(conflict.detail, str)
            or len(conflict.detail.encode("utf-8")) > 4096
        ):
            raise ConversationFormatError("thread quarantine record is invalid")


def _conflict_from_dict(value: object) -> ConversationConflict:
    if not isinstance(value, dict):
        raise ConversationFormatError("thread quarantine record is invalid")
    reason = value.get("reason")
    source = value.get("source")
    identity = value.get("identity")
    detail = value.get("detail")
    if (
        not isinstance(reason, str)
        or not isinstance(source, str)
        or (identity is not None and not isinstance(identity, str))
        or not isinstance(detail, str)
    ):
        raise ConversationFormatError("thread quarantine record is invalid")
    return ConversationConflict(reason, source, identity, detail)


def _finalize_segment(
    events: Iterable[MessageEvent],
    *,
    index: int,
    previous_segment_id: str | None,
    previous_sha256: str | None,
) -> EncodedMessageSegment:
    values = tuple(events)
    data = _encode_segment_data(
        values,
        index=index,
        previous_segment_id=previous_segment_id,
        previous_sha256=previous_sha256,
    )
    if len(data) > MAX_MESSAGE_SEGMENT_BYTES:
        raise ConversationFormatError("message segment exceeds the byte limit")
    return EncodedMessageSegment(
        segment_id=_opaque_id("conversation-segment", f"{values[0].thread_id}:{index}"),
        index=index,
        previous_segment_id=previous_segment_id,
        previous_segment_sha256=previous_sha256,
        sha256=hashlib.sha256(data).hexdigest(),
        event_count=len(values),
        events=values,
        data=data,
    )


def _encode_segment_data(
    events: Iterable[MessageEvent],
    *,
    index: int,
    previous_segment_id: str | None,
    previous_sha256: str | None,
) -> bytes:
    values = tuple(events)
    if not values:
        raise ConversationFormatError("message segment cannot be empty")
    header = {
        "format": "anima.message-segment",
        "version": MESSAGE_SEGMENT_FORMAT_VERSION,
        "threadId": values[0].thread_id,
        "segmentIndex": index,
        "firstSequence": min(event.sequence for event in values),
        "lastSequence": max(event.sequence for event in values),
        "previousSegmentId": previous_segment_id,
        "previousSegmentSha256": previous_sha256,
        "eventCount": len(values),
    }
    lines = [_canonical_json(header), *(_canonical_json(event.as_dict()) for event in values)]
    return b"\n".join(lines) + b"\n"


def _event_from_dict(value: object) -> MessageEvent:
    if not isinstance(value, dict):
        raise ConversationFormatError("message event is invalid")
    try:
        attachments = value["attachmentUris"]
        if not isinstance(attachments, list) or not all(
            isinstance(item, str) for item in attachments
        ):
            raise TypeError
        event = MessageEvent(
            event_id=value["eventId"],
            message_id=value["messageId"],
            legacy_message_id=value["legacyMessageId"],
            thread_id=value["threadId"],
            sequence=value["sequence"],
            kind=value["type"],
            message_version=value["messageVersion"],
            expected_prior_event_id=value["expectedPriorEventId"],
            expected_prior_version=value["expectedPriorVersion"],
            role=value["role"],
            content=value["content"],
            attachment_uris=tuple(attachments),
            created_at=value["createdAt"],
        )
    except (KeyError, TypeError) as exc:
        raise ConversationFormatError("message event is invalid") from exc
    _validate_event(event)
    return event


def _validate_event(event: MessageEvent) -> None:
    for label, value in (
        ("event identity", event.event_id),
        ("message identity", event.message_id),
        ("thread identity", event.thread_id),
        ("created timestamp", event.created_at),
    ):
        if not isinstance(value, str) or not value:
            raise ConversationFormatError(f"{label} is invalid")
    if (
        event.legacy_message_id is not None
        and (
            isinstance(event.legacy_message_id, bool)
            or not isinstance(event.legacy_message_id, (int, str))
            or not str(event.legacy_message_id)
        )
    ):
        raise ConversationFormatError("message legacy identity is invalid")
    if isinstance(event.sequence, bool) or not isinstance(event.sequence, int) or event.sequence < 1:
        raise ConversationFormatError("message sequence is invalid")
    if event.kind not in _EVENT_KINDS:
        raise ConversationFormatError("message event kind is invalid")
    if event.role not in _VISIBLE_ROLES:
        raise ConversationFormatError("message event role is invalid")
    if (
        isinstance(event.message_version, bool)
        or not isinstance(event.message_version, int)
        or event.message_version < 1
    ):
        raise ConversationFormatError("message event version is invalid")
    if event.kind == "message.created":
        if event.expected_prior_event_id is not None or event.expected_prior_version is not None:
            raise ConversationFormatError("message create must not declare a prior event")
        if event.message_version != 1:
            raise ConversationFormatError("message create must publish version one")
    elif (
        not isinstance(event.expected_prior_event_id, str)
        or not event.expected_prior_event_id
        or isinstance(event.expected_prior_version, bool)
        or not isinstance(event.expected_prior_version, int)
        or event.expected_prior_version < 1
    ):
        raise ConversationFormatError("message prior event precondition is invalid")
    if not isinstance(event.content, str):
        raise ConversationFormatError("message event content is invalid")
    if not isinstance(event.attachment_uris, tuple):
        raise ConversationFormatError("message event attachments are invalid")
    for uri in event.attachment_uris:
        _validate_core_object_uri(uri)


def _project_attachment_uris(
    raw: object,
    *,
    resolver: Any | None,
) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConversationFormatError("visible message attachments are invalid")
    result: list[str] = []
    for attachment in raw:
        uri: object = attachment if isinstance(attachment, str) else None
        if isinstance(attachment, Mapping):
            uri = attachment.get(
                "corefsUri",
                attachment.get("corefs_uri", attachment.get("uri")),
            )
        if (not isinstance(uri, str) or not uri) and resolver is not None:
            uri = resolver(attachment)
        if not isinstance(uri, str) or not uri:
            raise ConversationFormatError("visible message attachment is unresolved")
        _validate_core_object_uri(uri)
        result.append(uri)
    return tuple(result)


def _validate_core_object_uri(uri: str) -> None:
    if _CORE_OBJECT_URI.fullmatch(uri) is None:
        raise ConversationFormatError("visible message attachment URI is invalid")


def _source_value(source: object, *names: str) -> object:
    if isinstance(source, Mapping):
        for name in names:
            if name in source:
                return source[name]
        return None
    for name in names:
        if hasattr(source, name):
            return getattr(source, name)
    return None


def _required_source_string(source: object, *names: str) -> str:
    value = _source_value(source, *names)
    if not isinstance(value, str) or not value:
        raise ConversationFormatError(f"message {names[0]} is invalid")
    return value


def _optional_source_string(source: object, *names: str) -> str | None:
    value = _source_value(source, *names)
    return value if isinstance(value, str) and value else None


def _required_positive_int(source: object, *names: str) -> int:
    value = _source_value(source, *names)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConversationFormatError(f"message {names[0]} is invalid")
    return value


def _canonical_timestamp(value: object) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ConversationFormatError("message timestamp is invalid") from exc
    else:
        raise ConversationFormatError("message timestamp is invalid")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat(timespec="microseconds")


def _opaque_id(domain: str, source_key: str) -> str:
    from anima_server.services.corefs.diary_migration import migration_opaque_id

    return migration_opaque_id(domain, source_key)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
