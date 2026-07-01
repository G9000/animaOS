from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from anima_server.config import settings
from anima_server.services.agent.prompt_budget import PromptBudgetTrace
from anima_server.services.agent.runtime_types import StepTrace, ToolCall

RETRIEVAL_CONTENT_KEY = "retrieval"
ATTACHMENTS_CONTENT_KEY = "attachments"
PILLS_CONTENT_KEY = "pills"


@dataclass(frozen=True, slots=True)
class StoredAttachment:
    id: str
    kind: Literal["image"]
    mime_type: str
    path: str
    asset_id: int | None = None
    filename: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    storage_path: str | None = None
    retention_state: str | None = None
    delete_on_error: bool = True

    def to_content_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.id,
            "kind": self.kind,
            "mimeType": self.mime_type,
        }
        if self.filename is not None:
            payload["filename"] = self.filename
        if self.asset_id is not None:
            payload["assetId"] = self.asset_id
        if self.size_bytes is not None:
            payload["sizeBytes"] = self.size_bytes
        if self.sha256 is not None:
            payload["sha256"] = self.sha256
        if self.storage_path is not None:
            payload["storagePath"] = self.storage_path
        if self.retention_state is not None:
            payload["retentionState"] = self.retention_state
        return payload

    def to_public_dict(self, *, message_id: int) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.id,
            "kind": self.kind,
            "mimeType": self.mime_type,
            "url": f"/api/chat/messages/{message_id}/attachments/{self.id}",
        }
        if self.filename is not None:
            payload["filename"] = self.filename
        if self.asset_id is not None:
            payload["assetId"] = self.asset_id
        if self.size_bytes is not None:
            payload["sizeBytes"] = self.size_bytes
        if self.sha256 is not None:
            payload["sha256"] = self.sha256
        if self.retention_state is not None:
            payload["retentionState"] = self.retention_state
        return payload


@dataclass(frozen=True, slots=True)
class StoredMessage:
    role: str
    content: str
    attachments: tuple[StoredAttachment, ...] = field(default_factory=tuple)
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class AgentCitation:
    index: int
    memory_item_id: int
    uri: str
    score: float | None = None
    category: str | None = None


@dataclass(frozen=True, slots=True)
class AgentContextFragment:
    rank: int
    memory_item_id: int
    uri: str
    text: str
    score: float | None = None
    category: str | None = None


@dataclass(frozen=True, slots=True)
class AgentRetrievalStats:
    retrieval_ms: float | None = None
    total_considered: int = 0
    returned: int = 0
    cutoff_index: int = 0
    cutoff_score: float | None = None
    top_score: float | None = None
    cutoff_ratio: float | None = None
    triggered_by: str = ""


@dataclass(frozen=True, slots=True)
class AgentRetrievalTrace:
    retriever: str
    citations: tuple[AgentCitation, ...] = field(default_factory=tuple)
    context_fragments: tuple[AgentContextFragment, ...] = field(default_factory=tuple)
    stats: AgentRetrievalStats | None = None


def serialize_agent_retrieval(
    retrieval: AgentRetrievalTrace | None,
) -> dict[str, object] | None:
    if retrieval is None:
        return None

    citations = [
        {
            "index": citation.index,
            "memoryItemId": citation.memory_item_id,
            "uri": citation.uri,
            "score": citation.score,
            "category": citation.category,
        }
        for citation in retrieval.citations
    ]
    context_fragments = [
        {
            "rank": fragment.rank,
            "memoryItemId": fragment.memory_item_id,
            "uri": fragment.uri,
            "text": fragment.text,
            "score": fragment.score,
            "category": fragment.category,
        }
        for fragment in retrieval.context_fragments
    ]

    stats_payload: dict[str, object] | None = None
    if retrieval.stats is not None:
        stats_payload = {
            "retrievalMs": retrieval.stats.retrieval_ms,
            "totalConsidered": retrieval.stats.total_considered,
            "returned": retrieval.stats.returned,
            "cutoffIndex": retrieval.stats.cutoff_index,
            "cutoffScore": retrieval.stats.cutoff_score,
            "topScore": retrieval.stats.top_score,
            "cutoffRatio": retrieval.stats.cutoff_ratio,
            "triggeredBy": retrieval.stats.triggered_by,
        }

    return {
        "retriever": retrieval.retriever,
        "citations": citations,
        "contextFragments": context_fragments,
        "stats": stats_payload,
    }


def attach_serialized_retrieval(
    content_json: dict[str, object] | None,
    retrieval: dict[str, object] | None,
) -> dict[str, object] | None:
    if retrieval is None:
        return content_json

    payload = dict(content_json or {})
    payload[RETRIEVAL_CONTENT_KEY] = retrieval
    return payload


def extract_stored_retrieval(
    content_json: dict[str, object] | None,
) -> dict[str, object] | None:
    if not isinstance(content_json, dict):
        return None

    retrieval = content_json.get(RETRIEVAL_CONTENT_KEY)
    return retrieval if isinstance(retrieval, dict) else None


def attach_serialized_pills(
    content_json: dict[str, object] | None,
    pills: list[dict[str, object]],
) -> dict[str, object] | None:
    if not pills:
        return content_json

    payload = dict(content_json or {})
    payload[PILLS_CONTENT_KEY] = pills
    return payload


def extract_stored_pills(
    content_json: dict[str, object] | None,
) -> list[dict[str, object]]:
    if not isinstance(content_json, dict):
        return []

    pills = content_json.get(PILLS_CONTENT_KEY)
    if not isinstance(pills, list):
        return []

    return [dict(pill) for pill in pills if isinstance(pill, dict)]


def attach_serialized_attachments(
    content_json: dict[str, object] | None,
    attachments: tuple[StoredAttachment, ...],
) -> dict[str, object] | None:
    if not attachments:
        return content_json

    payload = dict(content_json or {})
    payload[ATTACHMENTS_CONTENT_KEY] = [
        attachment.to_content_dict() for attachment in attachments
    ]
    return payload


def extract_stored_attachment_metadata(
    content_json: dict[str, object] | None,
) -> list[dict[str, object]]:
    if not isinstance(content_json, dict):
        return []

    raw_attachments = content_json.get(ATTACHMENTS_CONTENT_KEY)
    if not isinstance(raw_attachments, list):
        return []

    return [
        dict(raw_attachment)
        for raw_attachment in raw_attachments
        if isinstance(raw_attachment, dict)
    ]


def deserialize_stored_attachments(
    content_json: dict[str, object] | None,
) -> tuple[StoredAttachment, ...]:
    attachments: list[StoredAttachment] = []
    for raw_attachment in extract_stored_attachment_metadata(content_json):
        attachment_id = raw_attachment.get("id")
        kind = raw_attachment.get("kind")
        mime_type = raw_attachment.get("mimeType")
        storage_path = raw_attachment.get("storagePath")
        if (
            not isinstance(attachment_id, str)
            or not attachment_id
            or kind != "image"
            or not isinstance(mime_type, str)
            or not mime_type
            or not isinstance(storage_path, str)
            or not storage_path
        ):
            continue

        resolved_path = _resolve_stored_attachment_path(storage_path)
        if resolved_path is None:
            continue

        attachments.append(
            StoredAttachment(
                id=attachment_id,
                kind="image",
                mime_type=mime_type,
                path=str(resolved_path),
                storage_path=storage_path,
                asset_id=_coerce_int(raw_attachment.get("assetId")),
                filename=raw_attachment.get("filename")
                if isinstance(raw_attachment.get("filename"), str)
                else None,
                size_bytes=_coerce_int(raw_attachment.get("sizeBytes")),
                sha256=raw_attachment.get("sha256")
                if isinstance(raw_attachment.get("sha256"), str)
                else None,
                retention_state=raw_attachment.get("retentionState")
                if isinstance(raw_attachment.get("retentionState"), str)
                else None,
            )
        )

    return tuple(attachments)


def _resolve_stored_attachment_path(storage_path: str) -> Path | None:
    path = Path(storage_path)
    if path.is_absolute():
        return None

    try:
        data_root = settings.data_dir.resolve()
        resolved_path = (data_root / path).resolve()
        resolved_path.relative_to(data_root)
    except (OSError, ValueError):
        return None

    return resolved_path


def serialize_public_attachments(
    content_json: dict[str, object] | None,
    *,
    message_id: int,
) -> list[dict[str, object]]:
    return [
        attachment.to_public_dict(message_id=message_id)
        for attachment in deserialize_stored_attachments(content_json)
    ]


def deserialize_agent_retrieval(
    payload: dict[str, object] | None,
) -> AgentRetrievalTrace | None:
    if not isinstance(payload, dict):
        return None

    retriever = payload.get("retriever")
    if not isinstance(retriever, str) or not retriever.strip():
        return None

    citations: list[AgentCitation] = []
    raw_citations = payload.get("citations")
    if isinstance(raw_citations, list):
        for raw_citation in raw_citations:
            citation = _deserialize_agent_citation(raw_citation)
            if citation is not None:
                citations.append(citation)

    context_fragments: list[AgentContextFragment] = []
    raw_context_fragments = payload.get("contextFragments")
    if isinstance(raw_context_fragments, list):
        for raw_fragment in raw_context_fragments:
            fragment = _deserialize_agent_context_fragment(raw_fragment)
            if fragment is not None:
                context_fragments.append(fragment)

    return AgentRetrievalTrace(
        retriever=retriever,
        citations=tuple(citations),
        context_fragments=tuple(context_fragments),
        stats=_deserialize_agent_retrieval_stats(payload.get("stats")),
    )


def _deserialize_agent_citation(payload: object) -> AgentCitation | None:
    if not isinstance(payload, dict):
        return None

    index = _coerce_int(payload.get("index"))
    memory_item_id = _coerce_int(payload.get("memoryItemId"))
    uri = payload.get("uri")
    if index is None or memory_item_id is None or not isinstance(uri, str) or not uri:
        return None

    return AgentCitation(
        index=index,
        memory_item_id=memory_item_id,
        uri=uri,
        score=_coerce_float(payload.get("score")),
        category=payload.get("category") if isinstance(payload.get("category"), str) else None,
    )


def _deserialize_agent_context_fragment(payload: object) -> AgentContextFragment | None:
    if not isinstance(payload, dict):
        return None

    rank = _coerce_int(payload.get("rank"))
    memory_item_id = _coerce_int(payload.get("memoryItemId"))
    uri = payload.get("uri")
    text = payload.get("text")
    if (
        rank is None
        or memory_item_id is None
        or not isinstance(uri, str)
        or not uri
        or not isinstance(text, str)
    ):
        return None

    return AgentContextFragment(
        rank=rank,
        memory_item_id=memory_item_id,
        uri=uri,
        text=text,
        score=_coerce_float(payload.get("score")),
        category=payload.get("category") if isinstance(payload.get("category"), str) else None,
    )


def _deserialize_agent_retrieval_stats(payload: object) -> AgentRetrievalStats | None:
    if not isinstance(payload, dict):
        return None

    return AgentRetrievalStats(
        retrieval_ms=_coerce_float(payload.get("retrievalMs")),
        total_considered=_coerce_int(payload.get("totalConsidered"), default=0) or 0,
        returned=_coerce_int(payload.get("returned"), default=0) or 0,
        cutoff_index=_coerce_int(payload.get("cutoffIndex"), default=0) or 0,
        cutoff_score=_coerce_float(payload.get("cutoffScore")),
        top_score=_coerce_float(payload.get("topScore")),
        cutoff_ratio=_coerce_float(payload.get("cutoffRatio")),
        triggered_by=payload.get("triggeredBy")
        if isinstance(payload.get("triggeredBy"), str)
        else "",
    )


def _coerce_int(value: object, *, default: int | None = None) -> int | None:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _coerce_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


@dataclass(slots=True)
class AgentResult:
    response: str
    model: str
    provider: str
    stop_reason: str | None = None
    tools_used: list[str] = field(default_factory=list)
    step_traces: list[StepTrace] = field(default_factory=list)
    prompt_budget: PromptBudgetTrace | None = None
    retrieval: AgentRetrievalTrace | None = None
