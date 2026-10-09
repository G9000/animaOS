from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

BodySource = str


@dataclass(frozen=True, slots=True)
class WritingSourceObjectDescriptor:
    stable_id: str
    parent_id: str
    name: str
    kind: str
    content_type: str
    body_encoding: str
    body_length: int
    content_sha256: str
    source_fingerprint_sha256: str
    created_at: str
    updated_at: str
    revision: int
    object_key_epoch: int = 1
    source_character_count: int | None = None
    references: tuple[str, ...] = ()
    policy: str = "inherit"
    stable_role: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    converter_format_version: int = 1
    body_source: BodySource = "prepared"
    source_key: str = ""
    source_digest: str | None = None
    prepared_path: str | None = None

    def native_object(self) -> dict[str, object]:
        return {
            "objectId": self.stable_id,
            "revision": self.revision,
            "objectKeyEpoch": self.object_key_epoch,
            "kind": self.kind,
            "parentId": self.parent_id,
            "name": self.name,
            "contentType": self.content_type,
            "bodyEncoding": self.body_encoding,
            "bodyLength": self.body_length,
            "contentSha256": self.content_sha256,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "sourceCharacterCount": self.source_character_count,
            "references": list(self.references),
            "policy": self.policy,
            "stableRole": self.stable_role,
            "graphMetadata": self.metadata,
            "sourceFingerprintSha256": self.source_fingerprint_sha256,
            "converterFormatVersion": self.converter_format_version,
        }


@dataclass(frozen=True, slots=True)
class WritingSourceBody:
    descriptor: WritingSourceObjectDescriptor
    body: bytes | None
