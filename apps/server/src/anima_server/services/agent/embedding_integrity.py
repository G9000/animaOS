from __future__ import annotations

import hashlib
import hmac
import json
import math
import struct
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal


def _normalize_embedding_values(values: list[Any]) -> list[float] | None:
    if not values:
        return None

    normalized: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        numeric = float(value)
        if not math.isfinite(numeric):
            return None
        normalized.append(numeric)
    return normalized


def parse_embedding_payload(raw: Any) -> list[float] | None:
    if raw is None:
        return None

    payload = raw
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    elif isinstance(raw, (bytes, bytearray, memoryview, dict)):
        return None
    elif not isinstance(raw, list):
        try:
            payload = list(raw)
        except TypeError:
            return None

    if not isinstance(payload, list):
        return None
    return _normalize_embedding_values(payload)


def compute_embedding_checksum(embedding: Iterable[float]) -> str:
    normalized = _normalize_embedding_values(list(embedding))
    if normalized is None:
        raise ValueError("embedding must be a non-empty sequence of finite numbers")

    payload = struct.pack("!I", len(normalized)) + struct.pack(
        f"!{len(normalized)}d", *normalized
    )
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class CheckedEmbedding:
    embedding: list[float] | None
    actual_checksum: str | None
    status: Literal["valid", "missing_checksum", "checksum_mismatch", "invalid"]


def check_embedding(raw: Any, expected_checksum: str | None) -> CheckedEmbedding:
    embedding = parse_embedding_payload(raw)
    if embedding is None:
        return CheckedEmbedding(None, None, "invalid")

    actual_checksum = compute_embedding_checksum(embedding)
    if not expected_checksum:
        return CheckedEmbedding(embedding, actual_checksum, "missing_checksum")

    if not hmac.compare_digest(expected_checksum, actual_checksum):
        return CheckedEmbedding(None, actual_checksum, "checksum_mismatch")

    return CheckedEmbedding(embedding, actual_checksum, "valid")
