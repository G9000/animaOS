from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from anima_server.models.runtime import RuntimeSourceSpan


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    user_id: int
    kind: str
    source_uri: str
    content_hash: str
    title: str | None = None
    media_type: str | None = None
    metadata_json: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class SourceArtifactInput:
    artifact_kind: str
    content_text: str | None
    content_hash: str
    metadata_json: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class SourceSpanInput:
    artifact_kind: str
    span_kind: str
    locator_json: dict[str, object]
    content_text: str
    content_hash: str
    metadata_json: dict[str, object] | None = None

    @property
    def locator_hash(self) -> str:
        return RuntimeSourceSpan.compute_locator_hash(self.locator_json)


@dataclass(frozen=True, slots=True)
class IngestionAdapterResult:
    identity: SourceIdentity
    artifacts: Sequence[SourceArtifactInput]
    spans: Sequence[SourceSpanInput]
    metadata_json: dict[str, object] | None = None
