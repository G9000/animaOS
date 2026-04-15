"""Transcript archive export helpers."""

from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from anima_server.services import anima_core_retrieval
from anima_server.services.agent.state import extract_stored_retrieval
from anima_server.services.crypto import decrypt_blob, encrypt_blob

if TYPE_CHECKING:
    from anima_server.models.runtime import RuntimeMessage

logger = logging.getLogger(__name__)

_STOP_WORDS = frozenset(
    ["a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "have", "has", "had", "do", "does", "did", "will", "would", "shall", "should", "may", "might", "can", "could", "am", "i", "me", "my", "we", "our", "you", "your", "he", "she", "it", "they", "them", "his", "her", "its", "their", "this", "that", "these", "those", "in", "on", "at", "to", "for", "of", "with", "by", "from", "and", "or", "but", "not", "so", "if", "then", "else", "when", "how", "what", "which", "who", "whom", "where", "why", "all", "any", "some", "no", "nor", "too", "also", "very", "just", "about", "up", "down", "out", "off", "over", "under", "again", "further", "once", "here", "there", "each", "every", "both", "few", "more", "most", "other", "such", "only", "own", "same", "than", "into", "through", "during", "before", "after", "above", "below", "between", "don", "doesn", "didn", "isn", "aren", "wasn", "weren", "won", "wouldn", "hasn", "hadn", "ll", "ve", "re"]
)


@dataclass(frozen=True)
class TranscriptExportResult:
    enc_path: Path
    meta_path: Path
    message_count: int


def messages_to_transcript_dicts(messages: list[RuntimeMessage]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for message in messages:
        role = "assistant" if message.role == "tool" and message.tool_name == "send_message" else message.role
        payload: dict[str, object] = {
            "role": role,
            "content": message.content_text or "",
            "ts": _isoformat_utc(message.created_at),
            "seq": message.sequence_id,
        }
        retrieval = extract_stored_retrieval(message.content_json)
        if role == "assistant" and retrieval is not None:
            payload["retrieval"] = retrieval
        if message.role == "assistant":
            tool_calls = message.content_json.get("tool_calls") if isinstance(message.content_json, dict) else None
            if isinstance(tool_calls, list):
                payload["tool_calls"] = tool_calls
            if isinstance(tool_calls, list) and message.content_text:
                payload["thinking"] = message.content_text
                payload["content"] = ""
        if message.role == "tool" and message.tool_name != "send_message":
            if message.tool_name:
                payload["tool_name"] = message.tool_name
            if message.tool_call_id:
                payload["tool_call_id"] = message.tool_call_id
        if message.source:
            payload["source"] = message.source
        result.append(payload)
    return result


def serialize_messages_to_jsonl(messages: list[dict]) -> str:
    lines = [json.dumps(message, ensure_ascii=False, separators=(",", ":")) for message in messages]
    return "\n".join(lines) + ("\n" if lines else "")


def export_transcript(
    *,
    messages: list[dict],
    thread_id: int,
    user_id: int,
    dek: bytes | None,
    transcripts_dir: Path,
    episode_ids: list[str] | None = None,
    summary: str | None = None,
) -> TranscriptExportResult:
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    date_prefix = _get_date_prefix(messages)
    base_name = f"{date_prefix}_thread-{thread_id}"
    enc_path = transcripts_dir / (
        f"{base_name}.jsonl.enc" if dek is not None else f"{base_name}.jsonl"
    )
    meta_path = transcripts_dir / f"{base_name}.meta.json"

    plaintext = serialize_messages_to_jsonl(messages)
    if dek is not None:
        encrypted = encrypt_blob(
            plaintext.encode("utf-8"),
            dek,
            aad=_build_aad(thread_id, date_prefix),
        )
        _atomic_write_bytes(enc_path, encrypted)
        encryption_mode = "aes-256-gcm"
    else:
        _atomic_write_text(enc_path, plaintext)
        encryption_mode = "plaintext"
    sidecar = _build_sidecar(
        messages=messages,
        thread_id=thread_id,
        user_id=user_id,
        encryption_mode=encryption_mode,
        episode_ids=episode_ids,
        summary=summary,
    )
    _atomic_write_text(
        meta_path,
        json.dumps(
            sidecar,
            ensure_ascii=False,
            indent=2,
        ),
    )

    _sync_transcript_to_retrieval_index(
        messages=messages,
        thread_id=thread_id,
        user_id=user_id,
        transcript_ref=enc_path.name,
        sidecar=sidecar,
    )

    return TranscriptExportResult(
        enc_path=enc_path,
        meta_path=meta_path,
        message_count=len(messages),
    )


def decrypt_transcript(enc_path: Path, *, dek: bytes | None, thread_id: int) -> list[dict]:
    if enc_path.suffix == ".jsonl":
        plaintext = enc_path.read_text(encoding="utf-8")
    else:
        if dek is None:
            raise ValueError("A DEK is required to decrypt encrypted transcripts.")
        date_prefix = enc_path.name.split("_thread-", 1)[0]
        plaintext = decrypt_blob(
            enc_path.read_bytes(),
            dek,
            aad=_build_aad(thread_id, date_prefix),
        ).decode("utf-8")

    if not plaintext:
        return []

    return [json.loads(line) for line in plaintext.splitlines() if line.strip()]


def load_transcript_sidecar(meta_path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Failed to read transcript sidecar %s", meta_path.name, exc_info=True)
        return None
    return payload if isinstance(payload, dict) else None


def resolve_transcript_path(meta_path: Path) -> Path | None:
    transcript_path = meta_path.parent / meta_path.name.replace(".meta.json", ".jsonl.enc")
    if transcript_path.exists():
        return transcript_path
    transcript_path = meta_path.parent / meta_path.name.replace(".meta.json", ".jsonl")
    if transcript_path.exists():
        return transcript_path
    return None


def _build_aad(thread_id: int, date_str: str) -> bytes:
    return f"transcript:{thread_id}:{date_str}".encode()


def _build_sidecar(
    *,
    messages: list[dict],
    thread_id: int,
    user_id: int,
    encryption_mode: str,
    episode_ids: list[str] | None = None,
    summary: str | None = None,
) -> dict:
    timestamps = [str(message.get("ts", "")) for message in messages if message.get("ts")]
    roles = _distinct_roles(messages)
    return {
        "version": 1,
        "thread_id": thread_id,
        "user_id": user_id,
        "date_start": timestamps[0] if timestamps else "",
        "date_end": timestamps[-1] if timestamps else "",
        "message_count": len(messages),
        "roles": roles,
        "keywords": _extract_keywords(messages),
        "summary": summary.strip() if summary and summary.strip() else _build_summary(messages),
        "chunk_offsets": [0],
        "episodic_memory_ids": episode_ids or [],
        "archived_at": datetime.now(UTC).isoformat(),
        "encryption": {
            "domain": "conversations",
            "aad_prefix": "transcript",
            "mode": encryption_mode,
        },
    }


def _extract_keywords(messages: list[dict], *, max_keywords: int = 10) -> list[str]:
    words: list[str] = []
    for message in messages:
        if message.get("role") != "user":
            continue
        content = str(message.get("content", ""))
        tokens = re.findall(r"[a-zA-Z]{3,}", content.lower())
        words.extend(token for token in tokens if token not in _STOP_WORDS)

    if not words:
        return []

    counts = Counter(words)
    return [word for word, _count in counts.most_common(max_keywords)]


def _build_summary(messages: list[dict]) -> str:
    user_messages = [str(message.get("content", "")) for message in messages if message.get("role") == "user"]
    if not user_messages:
        return "Empty conversation"
    first = user_messages[0][:100]
    if len(user_messages) == 1:
        return first
    return f"{first} ... {user_messages[-1][:100]}"


def _build_transcript_search_text(messages: list[dict[str, object]]) -> str:
    lines: list[str] = []
    for message in messages:
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        role = str(message.get("role", "unknown")).strip() or "unknown"
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _mark_transcript_index_dirty(*, root: Path) -> None:
    try:
        anima_core_retrieval.mark_retrieval_index_dirty(root=root, family="transcript")
    except Exception:
        logger.debug("Failed to mark transcript retrieval index dirty", exc_info=True)


def _clear_transcript_index_dirty(*, root: Path) -> None:
    try:
        anima_core_retrieval.clear_retrieval_index_dirty(root=root, family="transcript")
    except Exception:
        logger.debug("Failed to clear transcript retrieval index dirty state", exc_info=True)


def rebuild_transcript_index(
    *,
    user_id: int,
    dek: bytes | None,
    transcripts_dir: Path,
    root: Path | None = None,
) -> int:
    resolved_root = root or anima_core_retrieval.get_retrieval_root()
    try:
        anima_core_retrieval.reset_transcript_index(root=resolved_root)
    except RuntimeError:
        logger.debug("Rust transcript index reset is unavailable for user %s", user_id)
        return 0
    except Exception:
        logger.warning(
            "Failed to reset the Rust transcript index before rebuild for user %s",
            user_id,
            exc_info=True,
        )
        _mark_transcript_index_dirty(root=resolved_root)
        return 0

    rebuilt = 0
    had_errors = False
    for meta_path in sorted(transcripts_dir.glob("*.meta.json")):
        sidecar = load_transcript_sidecar(meta_path)
        if sidecar is None:
            had_errors = True
            continue
        if int(sidecar.get("user_id", -1)) != user_id:
            continue

        transcript_path = resolve_transcript_path(meta_path)
        if transcript_path is None:
            had_errors = True
            continue

        try:
            messages = decrypt_transcript(
                transcript_path,
                dek=dek,
                thread_id=int(sidecar.get("thread_id", 0)),
            )
            date_start = str(sidecar.get("date_start", "")).strip()
            date_start_ts = (
                int(datetime.fromisoformat(date_start.replace("Z", "+00:00")).timestamp())
                if date_start
                else 0
            )
            anima_core_retrieval.transcript_index_upsert(
                root=resolved_root,
                thread_id=int(sidecar.get("thread_id", 0)),
                user_id=user_id,
                transcript_ref=transcript_path.name,
                summary=str(sidecar.get("summary", "")),
                keywords=[str(keyword) for keyword in sidecar.get("keywords", []) if str(keyword).strip()],
                text=_build_transcript_search_text(messages),
                date_start=date_start_ts,
            )
            rebuilt += 1
        except Exception:
            had_errors = True
            logger.warning(
                "Failed to rebuild transcript index entry from %s",
                meta_path.name,
                exc_info=True,
            )

    if had_errors:
        _mark_transcript_index_dirty(root=resolved_root)
    else:
        _clear_transcript_index_dirty(root=resolved_root)
    return rebuilt


def _sync_transcript_to_retrieval_index(
    *,
    messages: list[dict[str, object]],
    thread_id: int,
    user_id: int,
    transcript_ref: str,
    sidecar: dict[str, object],
) -> None:
    date_start = str(sidecar.get("date_start", "")).strip()
    try:
        date_start_ts = (
            int(datetime.fromisoformat(date_start.replace("Z", "+00:00")).timestamp())
            if date_start
            else 0
        )
        anima_core_retrieval.transcript_index_upsert(
            root=anima_core_retrieval.get_retrieval_root(),
            thread_id=thread_id,
            user_id=user_id,
            transcript_ref=transcript_ref,
            summary=str(sidecar.get("summary", "")),
            keywords=[str(keyword) for keyword in sidecar.get("keywords", []) if str(keyword).strip()],
            text=_build_transcript_search_text(messages),
            date_start=date_start_ts,
        )
    except RuntimeError:
        logger.debug("Rust transcript index upsert is unavailable for thread %s", thread_id)
    except Exception:
        logger.warning(
            "Failed to upsert transcript %s into the Rust retrieval index",
            transcript_ref,
            exc_info=True,
        )
        _mark_transcript_index_dirty(root=anima_core_retrieval.get_retrieval_root())


def _distinct_roles(messages: list[dict]) -> list[str]:
    roles: list[str] = []
    seen: set[str] = set()
    for message in messages:
        role = str(message.get("role", ""))
        if not role or role in seen:
            continue
        roles.append(role)
        seen.add(role)
    return roles


def _get_date_prefix(messages: list[dict]) -> str:
    if messages:
        ts = str(messages[0].get("ts", ""))
        if ts:
            return ts[:10]
    return datetime.now(UTC).date().isoformat()


def _isoformat_utc(value: datetime | None) -> str:
    if value is None:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp_path = path.parent / f"{path.name}.tmp"
    tmp_path.write_bytes(data)
    os.replace(tmp_path, path)


def _atomic_write_text(path: Path, data: str) -> None:
    tmp_path = path.parent / f"{path.name}.tmp"
    tmp_path.write_text(data, encoding="utf-8")
    os.replace(tmp_path, path)
