from __future__ import annotations

import hmac
import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from threading import RLock
from types import MappingProxyType
from uuid import uuid4

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from anima_server.services.corefs.runtime_sealing import (
    RuntimePayloadAAD,
    RuntimePayloadSealer,
    SealedRuntimePayload,
)

_BLIND_INDEX_INFO = b"anima-blind-index-v1"


class CoreFSRuntimeLocked(RuntimeError):
    """Raised when unlocked Runtime index state is unavailable."""


class ReadinessState(StrEnum):
    LOCKED = "locked"
    OPENING_CORE = "opening_core"
    VALIDATING_CORE = "validating_core"
    CATALOG_LOADING = "catalog_loading"
    CATALOG_READY = "catalog_ready"
    CATALOG_READY_DEGRADED = "catalog_ready_degraded"
    TEXT_INDEXING = "text_indexing"
    SEMANTIC_INDEXING = "semantic_indexing"
    READY = "ready"


class IndexCapability(StrEnum):
    NAVIGATION = "navigation"
    EXACT_SEARCH = "exact_search"
    TEXT_SEARCH = "text_search"
    SEMANTIC_SEARCH = "semantic_search"


@dataclass(frozen=True, slots=True)
class FamilyReadiness:
    total: int
    processed: int
    failed: int
    degraded: bool
    unavailable_object_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReadinessSnapshot:
    core_id: str
    state: ReadinessState
    catalog_generation: int | None
    processed_objects: int
    capabilities: frozenset[IndexCapability]
    families: Mapping[str, FamilyReadiness]
    blind_index_generation: int | None
    blind_index_pending_generation: int | None
    blind_index_progress: int


@dataclass(frozen=True, slots=True)
class IndexCheckpoint:
    core_id: str
    catalog_generation: int
    state: ReadinessState
    cursor: str | None
    processed_objects: int


@dataclass(frozen=True, slots=True)
class RuntimeEmbeddingSearchHit:
    source_type: str
    source_id: int
    content: str
    category: str
    importance: int
    similarity: float


class CoreFSProgressiveIndex:
    """Unlock-scoped progressive text and semantic index for one Core."""

    def __init__(self, core_id: str) -> None:
        if not core_id:
            raise ValueError("core_id must be non-empty")
        self.core_id = core_id
        self._lock = RLock()
        self._state = ReadinessState.LOCKED
        self._local_instance_id: str | None = None
        self._catalog_generation: int | None = None
        self._families: dict[str, FamilyReadiness] = {}
        self._documents: dict[str, tuple[str, str, str]] = {}
        self._vectors: dict[str, tuple[float, ...]] = {}
        self._runtime_embeddings: dict[
            tuple[str, int],
            tuple[tuple[float, ...], str, str, int],
        ] = {}
        self._runtime_embedding_fingerprint: str | None = None
        self._pending_runtime_embedding_fingerprint: str | None = None
        self._runtime_embedding_expected_count = 0
        self._runtime_embedding_failures: set[tuple[str, int]] = set()
        self._semantic_fingerprint: str | None = None
        self._pending_semantic_fingerprint: str | None = None
        self._processed_revisions: set[tuple[str, str]] = set()
        self._queries: set[str] = set()
        self._search_key: bytearray | None = None
        self._blind_generations: dict[int, dict[bytes, set[str]]] = {}
        self._active_blind_generation: int | None = None
        self._pending_blind_generation: int | None = None
        self._pending_blind_expected_count: int | None = None
        self._sealer = RuntimePayloadSealer()
        self._last_cursor: str | None = None

    def unlock(self, *, sqlcipher_key: bytes, local_instance_id: str) -> None:
        if not sqlcipher_key:
            raise ValueError("SQLCipher key must be non-empty")
        with self._lock:
            self.clear_unlocked_state()
            self._state = ReadinessState.OPENING_CORE
            self._local_instance_id = local_instance_id
            self._search_key = bytearray(
                HKDF(
                    algorithm=hashes.SHA256(),
                    length=32,
                    salt=local_instance_id.encode("utf-8"),
                    info=_BLIND_INDEX_INFO,
                ).derive(sqlcipher_key)
            )
            self._sealer.install(
                sqlcipher_key=sqlcipher_key,
                local_instance_id=local_instance_id,
            )
            self._state = ReadinessState.VALIDATING_CORE

    @property
    def local_instance_id(self) -> str:
        with self._lock:
            self._require_unlocked()
            if self._local_instance_id is None:
                raise CoreFSRuntimeLocked("CoreFS Runtime instance binding is unavailable")
            return self._local_instance_id

    def seal_runtime_payload(
        self,
        plaintext: bytes,
        *,
        aad: RuntimePayloadAAD,
    ) -> SealedRuntimePayload:
        with self._lock:
            self._require_unlocked()
            return self._sealer.seal(plaintext, aad=aad)

    def open_runtime_payload(
        self,
        payload: SealedRuntimePayload,
        *,
        aad: RuntimePayloadAAD,
    ) -> bytes:
        with self._lock:
            self._require_unlocked()
            return self._sealer.open(payload, aad=aad)

    def begin_catalog(
        self,
        *,
        preserve_blind_generation: bool = False,
    ) -> None:
        with self._lock:
            self._require_unlocked()
            self._documents.clear()
            self._vectors.clear()
            self._semantic_fingerprint = None
            self._pending_semantic_fingerprint = None
            self._processed_revisions.clear()
            self._queries.clear()
            self._families.clear()
            if not preserve_blind_generation:
                self._blind_generations.clear()
                self._active_blind_generation = None
                self._pending_blind_generation = None
                self._pending_blind_expected_count = None
            self._catalog_generation = None
            self._last_cursor = None
            self._state = ReadinessState.CATALOG_LOADING

    def publish_catalog(
        self,
        *,
        catalog_generation: int,
        families: Mapping[str, int],
        degraded: Mapping[str, tuple[str, ...]] | None = None,
    ) -> None:
        if catalog_generation < 0:
            raise ValueError("catalog generation must be non-negative")
        degraded = degraded or {}
        with self._lock:
            self._require_state(ReadinessState.CATALOG_LOADING)
            self._catalog_generation = catalog_generation
            self._families = {
                family: FamilyReadiness(
                    total=total,
                    processed=0,
                    failed=len(degraded.get(family, ())),
                    degraded=bool(degraded.get(family)),
                    unavailable_object_ids=tuple(degraded.get(family, ())),
                )
                for family, total in sorted(families.items())
            }
            self._state = (
                ReadinessState.CATALOG_READY_DEGRADED
                if any(item.degraded for item in self._families.values())
                else ReadinessState.CATALOG_READY
            )

    def begin_text_indexing(self) -> None:
        with self._lock:
            self._require_catalog()
            self._state = ReadinessState.TEXT_INDEXING

    def index_text(
        self,
        *,
        family: str,
        object_id: str,
        revision: str,
        text: str,
    ) -> None:
        with self._lock:
            self._require_state(ReadinessState.TEXT_INDEXING)
            pair = (object_id, revision)
            is_new_revision = pair not in self._processed_revisions
            self._documents[object_id] = (revision, family, text)
            if is_new_revision:
                self._processed_revisions.add(pair)
                self._increment_family_processed(family)
            self._last_cursor = f"{object_id}:{revision}"

    def skip_text(
        self,
        *,
        family: str,
        object_id: str,
        revision: str,
    ) -> None:
        """Complete one catalog object that cannot participate in text search."""
        with self._lock:
            self._require_state(ReadinessState.TEXT_INDEXING)
            pair = (object_id, revision)
            if pair not in self._processed_revisions:
                self._processed_revisions.add(pair)
                self._increment_family_processed(family)
            self._last_cursor = f"{object_id}:{revision}"

    def search_text(self, query: str) -> tuple[str, ...]:
        normalized = query.casefold().strip()
        with self._lock:
            self._require_unlocked()
            if not normalized:
                return ()
            return tuple(
                sorted(
                    object_id
                    for object_id, (_, _, text) in self._documents.items()
                    if normalized in text.casefold()
                )
            )

    def search_semantic(
        self,
        vector: tuple[float, ...],
        *,
        limit: int,
    ) -> tuple[str, ...]:
        if not vector:
            raise ValueError("semantic query vector must be non-empty")
        if limit <= 0:
            raise ValueError("semantic query limit must be positive")
        query = tuple(float(value) for value in vector)
        query_norm = math.sqrt(sum(value * value for value in query))
        if query_norm == 0:
            raise ValueError("semantic query vector must have non-zero magnitude")
        with self._lock:
            self._require_unlocked()
            ranked: list[tuple[float, str]] = []
            for object_id, stored in self._vectors.items():
                if len(stored) != len(query):
                    raise ValueError("semantic query vector dimension does not match index")
                stored_norm = math.sqrt(sum(value * value for value in stored))
                if stored_norm == 0:
                    continue
                score = sum(left * right for left, right in zip(query, stored, strict=True)) / (
                    query_norm * stored_norm
                )
                ranked.append((score, object_id))
            ranked.sort(key=lambda item: (-item[0], item[1]))
            return tuple(object_id for _score, object_id in ranked[:limit])

    def upsert_runtime_embedding(
        self,
        *,
        source_type: str,
        source_id: int,
        vector: tuple[float, ...],
        content: str,
        category: str,
        importance: int,
        embedding_fingerprint: str | None = None,
    ) -> None:
        if not source_type:
            raise ValueError("Runtime embedding source type must be non-empty")
        if source_id <= 0:
            raise ValueError("Runtime embedding source ID must be positive")
        if not vector:
            raise ValueError("Runtime embedding vector must be non-empty")
        with self._lock:
            self._require_unlocked()
            if (
                self._pending_runtime_embedding_fingerprint is not None
                and embedding_fingerprint != self._pending_runtime_embedding_fingerprint
            ):
                raise ValueError("Runtime embedding configuration changed")
            if (
                self._runtime_embedding_fingerprint is not None
                and embedding_fingerprint != self._runtime_embedding_fingerprint
            ):
                raise ValueError("Runtime embedding configuration changed")
            self._runtime_embeddings[(source_type, source_id)] = (
                tuple(float(value) for value in vector),
                content,
                category,
                importance,
            )
            self._runtime_embedding_failures.discard((source_type, source_id))
            self._update_runtime_embedding_family_locked()

    def runtime_embedding_fingerprint(self) -> str | None:
        """Return the active generation tag for newly produced Runtime vectors."""
        with self._lock:
            self._require_unlocked()
            return self._runtime_embedding_fingerprint

    def request_runtime_embedding_refresh(self, *, embedding_fingerprint: str) -> None:
        """Invalidate Runtime vectors and reject work from the prior embedding space."""
        if not embedding_fingerprint:
            raise ValueError("embedding fingerprint must be non-empty")
        with self._lock:
            self._require_unlocked()
            self._runtime_embeddings.clear()
            self._pending_runtime_embedding_fingerprint = embedding_fingerprint

    def begin_runtime_embedding_rebuild(
        self,
        *,
        embedding_fingerprint: str | None = None,
        expected_count: int | None = None,
    ) -> None:
        """Claim the embedding space used by one Runtime rebuild pass."""
        if expected_count is not None and expected_count < 0:
            raise ValueError("Runtime embedding count must be non-negative")
        with self._lock:
            self._require_unlocked()
            if expected_count is not None:
                self._runtime_embedding_expected_count = expected_count
                self._runtime_embedding_failures.clear()
            pending = self._pending_runtime_embedding_fingerprint
            if pending is not None:
                if embedding_fingerprint != pending:
                    raise ValueError("Runtime embedding configuration changed")
                self._runtime_embedding_fingerprint = pending
                self._pending_runtime_embedding_fingerprint = None
            elif (
                embedding_fingerprint is not None
                and embedding_fingerprint != self._runtime_embedding_fingerprint
            ):
                self._runtime_embeddings.clear()
                self._runtime_embedding_fingerprint = embedding_fingerprint

    def mark_runtime_embedding_failure(
        self,
        *,
        source_type: str,
        source_id: int,
    ) -> None:
        """Keep one failed Runtime vector visible to readiness and retries."""
        if not source_type:
            raise ValueError("Runtime embedding source type must be non-empty")
        if source_id <= 0:
            raise ValueError("Runtime embedding source ID must be positive")
        with self._lock:
            self._require_unlocked()
            self._runtime_embedding_failures.add((source_type, source_id))
            self._update_runtime_embedding_family_locked()

    def runtime_embedding_rebuild_status(
        self,
    ) -> tuple[int, tuple[tuple[str, int], ...]]:
        """Return the current pass size and failed opaque Runtime row keys."""
        with self._lock:
            self._require_unlocked()
            return (
                self._runtime_embedding_expected_count,
                tuple(sorted(self._runtime_embedding_failures)),
            )

    def publish_runtime_embedding_readiness(self) -> None:
        """Expose Runtime vector failures through the current catalog snapshot."""
        with self._lock:
            self._require_catalog()
            self._update_runtime_embedding_family_locked(force=True)

    def runtime_embedding_vector(
        self,
        *,
        source_type: str,
        source_id: int,
    ) -> tuple[float, ...] | None:
        with self._lock:
            self._require_unlocked()
            stored = self._runtime_embeddings.get((source_type, source_id))
            return None if stored is None else stored[0]

    def search_runtime_embeddings(
        self,
        query_vector: tuple[float, ...],
        *,
        limit: int,
        category: str | None = None,
        source_types: frozenset[str] | None = None,
        source_ids: frozenset[int] | None = None,
    ) -> tuple[RuntimeEmbeddingSearchHit, ...]:
        if not query_vector:
            raise ValueError("Runtime embedding query vector must be non-empty")
        if limit <= 0:
            return ()
        query = tuple(float(value) for value in query_vector)
        query_norm = math.sqrt(sum(value * value for value in query))
        if query_norm == 0:
            return ()
        with self._lock:
            self._require_unlocked()
            ranked: list[RuntimeEmbeddingSearchHit] = []
            for (source_type, source_id), stored in self._runtime_embeddings.items():
                vector, content, stored_category, importance = stored
                if source_types is not None and source_type not in source_types:
                    continue
                if source_ids is not None and source_id not in source_ids:
                    continue
                if category is not None and stored_category != category:
                    continue
                if len(vector) != len(query):
                    continue
                stored_norm = math.sqrt(sum(value * value for value in vector))
                if stored_norm == 0:
                    continue
                similarity = sum(
                    left * right for left, right in zip(query, vector, strict=True)
                ) / (query_norm * stored_norm)
                ranked.append(
                    RuntimeEmbeddingSearchHit(
                        source_type=source_type,
                        source_id=source_id,
                        content=content,
                        category=stored_category,
                        importance=importance,
                        similarity=similarity,
                    )
                )
            ranked.sort(key=lambda hit: (-hit.similarity, hit.source_type, hit.source_id))
            return tuple(ranked[:limit])

    def delete_runtime_embeddings(
        self,
        *,
        source_type: str | None = None,
        source_ids: frozenset[int] | None = None,
    ) -> None:
        with self._lock:
            self._require_unlocked()
            for key in tuple(self._runtime_embeddings):
                stored_source_type, stored_source_id = key
                if source_type is not None and stored_source_type != source_type:
                    continue
                if source_ids is not None and stored_source_id not in source_ids:
                    continue
                self._runtime_embeddings.pop(key, None)
                self._runtime_embedding_failures.discard(key)
            self._update_runtime_embedding_family_locked()

    def indexed_texts(self) -> tuple[tuple[str, str, str, str], ...]:
        """Return unlock-scoped text needed to resume an interrupted rebuild."""
        with self._lock:
            self._require_unlocked()
            return tuple(
                (object_id, revision, family, text)
                for object_id, (revision, family, text) in self._documents.items()
            )

    def has_vector(self, object_id: str) -> bool:
        with self._lock:
            self._require_unlocked()
            return object_id in self._vectors

    def begin_semantic_indexing(
        self,
        *,
        embedding_fingerprint: str | None = None,
    ) -> None:
        with self._lock:
            self._require_catalog()
            if self._pending_semantic_fingerprint is not None:
                if embedding_fingerprint != self._pending_semantic_fingerprint:
                    raise ValueError("semantic embedding configuration changed")
                self._semantic_fingerprint = self._pending_semantic_fingerprint
                self._pending_semantic_fingerprint = None
            if (
                embedding_fingerprint is not None
                and embedding_fingerprint != self._semantic_fingerprint
            ):
                self._vectors.clear()
                self._semantic_fingerprint = embedding_fingerprint
            self._state = ReadinessState.SEMANTIC_INDEXING

    def request_semantic_refresh(self, *, embedding_fingerprint: str) -> None:
        """Queue a new embedding space without interrupting an active text pass."""
        if not embedding_fingerprint:
            raise ValueError("embedding fingerprint must be non-empty")
        with self._lock:
            self._require_catalog()
            self._vectors.clear()
            if self._state is ReadinessState.TEXT_INDEXING:
                self._pending_semantic_fingerprint = embedding_fingerprint
                return
            self._pending_semantic_fingerprint = None
            self._semantic_fingerprint = embedding_fingerprint
            self._state = ReadinessState.SEMANTIC_INDEXING

    def invalidate_semantic_index(self, *, embedding_fingerprint: str) -> None:
        """Discard vectors produced by another effective embedding configuration."""
        self.request_semantic_refresh(
            embedding_fingerprint=embedding_fingerprint,
        )

    def mark_family_failure(self, *, family: str, object_id: str) -> None:
        if not object_id:
            raise ValueError("failed object ID must be non-empty")
        with self._lock:
            self._require_catalog()
            current = self._families.get(family)
            if current is None:
                raise ValueError(f"family is absent from catalog: {family}")
            unavailable = tuple(sorted({*current.unavailable_object_ids, object_id}))
            self._families[family] = FamilyReadiness(
                total=current.total,
                processed=current.processed,
                failed=len(unavailable),
                degraded=True,
                unavailable_object_ids=unavailable,
            )

    def clear_family_failure(self, *, family: str, object_id: str) -> None:
        """Clear a transient object failure after a successful retry."""
        with self._lock:
            self._require_catalog()
            current = self._families.get(family)
            if current is None:
                raise ValueError(f"family is absent from catalog: {family}")
            unavailable = tuple(
                value for value in current.unavailable_object_ids if value != object_id
            )
            self._families[family] = FamilyReadiness(
                total=current.total,
                processed=current.processed,
                failed=len(unavailable),
                degraded=bool(unavailable),
                unavailable_object_ids=unavailable,
            )

    def index_vector(
        self,
        *,
        object_id: str,
        vector: tuple[float, ...],
        embedding_fingerprint: str | None = None,
    ) -> None:
        if not vector:
            raise ValueError("semantic vector must be non-empty")
        with self._lock:
            self._require_state(ReadinessState.SEMANTIC_INDEXING)
            if (
                embedding_fingerprint is not None
                and embedding_fingerprint != self._semantic_fingerprint
            ):
                raise ValueError("semantic embedding configuration changed")
            if object_id not in self._documents:
                raise ValueError("semantic vector requires indexed text")
            normalized = tuple(float(value) for value in vector)
            if self._vectors:
                expected_dimension = len(next(iter(self._vectors.values())))
                if len(normalized) != expected_dimension:
                    raise ValueError(
                        "semantic vector dimension does not match index generation"
                    )
            self._vectors[object_id] = normalized

    def finish(self) -> None:
        with self._lock:
            self._require_catalog()
            self._state = ReadinessState.READY

    def cancel(self) -> IndexCheckpoint:
        with self._lock:
            self._require_unlocked()
            if self._catalog_generation is None:
                raise ValueError("catalog is not published")
            return IndexCheckpoint(
                core_id=self.core_id,
                catalog_generation=self._catalog_generation,
                state=self._state,
                cursor=self._last_cursor,
                processed_objects=len(self._processed_revisions),
            )

    def resume(self, checkpoint: IndexCheckpoint) -> None:
        with self._lock:
            self._require_unlocked()
            if checkpoint.core_id != self.core_id:
                raise ValueError("checkpoint belongs to another Core")
            if checkpoint.catalog_generation != self._catalog_generation:
                raise ValueError("checkpoint catalog generation is stale")
            if checkpoint.processed_objects != len(self._processed_revisions):
                raise ValueError("checkpoint progress does not match in-memory state")
            self._state = checkpoint.state
            self._last_cursor = checkpoint.cursor

    def begin_query(self) -> str:
        with self._lock:
            self._require_unlocked()
            query_id = uuid4().hex
            self._queries.add(query_id)
            return query_id

    def finish_query(self, query_id: str) -> None:
        with self._lock:
            self._require_unlocked()
            self._queries.discard(query_id)

    def blind_token(self, value: str) -> bytes:
        normalized = value.casefold().strip()
        if not normalized:
            raise ValueError("blind index value must be non-empty")
        with self._lock:
            key = self._require_search_key_locked()
            return hmac.digest(key, normalized.encode("utf-8"), "sha256")

    def private_lookup_token(self, value: str, *, namespace: str) -> bytes:
        """Return a domain-separated opaque token without normalizing identity."""
        if not value:
            raise ValueError("private lookup value must be non-empty")
        if not namespace:
            raise ValueError("private lookup namespace must be non-empty")
        namespace_bytes = namespace.encode("utf-8")
        value_bytes = value.encode("utf-8")
        payload = (
            b"runtime-private-lookup:v1\x00"
            + len(namespace_bytes).to_bytes(4, "big")
            + namespace_bytes
            + len(value_bytes).to_bytes(8, "big")
            + value_bytes
        )
        with self._lock:
            key = self._require_search_key_locked()
            return hmac.digest(key, payload, "sha256")

    def begin_blind_generation(
        self,
        *,
        generation: int,
        expected_count: int,
    ) -> None:
        if generation <= 0:
            raise ValueError("blind generation must be positive")
        if expected_count < 0:
            raise ValueError("blind generation count must be non-negative")
        with self._lock:
            self._require_unlocked()
            if (
                self._active_blind_generation is not None
                and generation <= self._active_blind_generation
            ):
                raise ValueError("blind generation must be newer than active")
            if (
                self._pending_blind_generation is not None
                and self._pending_blind_generation != generation
            ):
                raise ValueError("another blind generation is already pending")
            self._blind_generations[generation] = {}
            self._pending_blind_generation = generation
            self._pending_blind_expected_count = expected_count

    def add_blind_token(
        self,
        *,
        generation: int,
        value: str,
        object_id: str,
    ) -> None:
        if not object_id:
            raise ValueError("blind token object ID must be non-empty")
        with self._lock:
            self._require_unlocked()
            if generation != self._pending_blind_generation:
                raise ValueError("blind token generation is not pending")
            token = self.blind_token(value)
            generation_entries = self._blind_generations[generation]
            generation_entries.setdefault(token, set()).add(object_id)

    def commit_blind_generation(self, generation: int) -> None:
        with self._lock:
            self._require_unlocked()
            if generation != self._pending_blind_generation:
                raise ValueError("blind token generation is not pending")
            entries = self._blind_generations[generation]
            actual_count = sum(len(object_ids) for object_ids in entries.values())
            if actual_count != self._pending_blind_expected_count:
                raise ValueError("blind token generation is incomplete")
            self._active_blind_generation = generation
            self._pending_blind_generation = None
            self._pending_blind_expected_count = None
            self._blind_generations = {generation: entries}

    def load_blind_generation(
        self,
        *,
        generation: int,
        entries: tuple[tuple[bytes, str], ...],
    ) -> None:
        if generation <= 0:
            raise ValueError("blind generation must be positive")
        with self._lock:
            self._require_unlocked()
            loaded: dict[bytes, set[str]] = {}
            for token, object_id in entries:
                if len(token) != 32 or not object_id:
                    raise ValueError("invalid persisted blind token")
                loaded.setdefault(bytes(token), set()).add(object_id)
            self._blind_generations = {generation: loaded}
            self._active_blind_generation = generation
            self._pending_blind_generation = None
            self._pending_blind_expected_count = None

    def lookup_exact(self, value: str) -> tuple[str, ...]:
        with self._lock:
            self._require_unlocked()
            if self._active_blind_generation is None:
                return ()
            token = self.blind_token(value)
            return tuple(
                sorted(
                    self._blind_generations[self._active_blind_generation].get(
                        token,
                        (),
                    )
                )
            )

    def snapshot(self) -> ReadinessSnapshot:
        with self._lock:
            return ReadinessSnapshot(
                core_id=self.core_id,
                state=self._state,
                catalog_generation=self._catalog_generation,
                processed_objects=len(self._processed_revisions),
                capabilities=self._capabilities_locked(),
                families=MappingProxyType(dict(self._families)),
                blind_index_generation=self._active_blind_generation,
                blind_index_pending_generation=self._pending_blind_generation,
                blind_index_progress=(
                    0
                    if self._pending_blind_generation is None
                    else sum(
                        len(object_ids)
                        for object_ids in self._blind_generations[
                            self._pending_blind_generation
                        ].values()
                    )
                ),
            )

    def sensitive_buffer_counts(self) -> dict[str, int]:
        with self._lock:
            return {
                "documents": len(self._documents),
                "vectors": len(self._vectors) + len(self._runtime_embeddings),
                "queries": len(self._queries),
                "blind_tokens": sum(
                    len(object_ids)
                    for entries in self._blind_generations.values()
                    for object_ids in entries.values()
                ),
                "search_keys": int(self._search_key is not None),
                "sealing_keys": int(self._sealer.installed),
            }

    def clear_unlocked_state(self) -> None:
        with self._lock:
            for object_id, (revision, family, text) in tuple(self._documents.items()):
                del revision, family, text
                self._documents.pop(object_id, None)
            self._vectors.clear()
            self._runtime_embeddings.clear()
            self._runtime_embedding_fingerprint = None
            self._pending_runtime_embedding_fingerprint = None
            self._runtime_embedding_expected_count = 0
            self._runtime_embedding_failures.clear()
            self._semantic_fingerprint = None
            self._pending_semantic_fingerprint = None
            self._processed_revisions.clear()
            self._queries.clear()
            self._blind_generations.clear()
            self._active_blind_generation = None
            self._pending_blind_generation = None
            self._pending_blind_expected_count = None
            self._families.clear()
            self._catalog_generation = None
            self._local_instance_id = None
            self._last_cursor = None
            if self._search_key is not None:
                self._search_key[:] = b"\0" * len(self._search_key)
                self._search_key = None
            self._sealer.clear()
            self._state = ReadinessState.LOCKED

    def _update_runtime_embedding_family_locked(self, *, force: bool = False) -> None:
        family = "runtime_embeddings"
        if not force and family not in self._families:
            return
        unavailable = tuple(
            f"{source_type}:{source_id}"
            for source_type, source_id in sorted(self._runtime_embedding_failures)
        )
        total = self._runtime_embedding_expected_count
        self._families[family] = FamilyReadiness(
            total=total,
            processed=max(total - len(unavailable), 0),
            failed=len(unavailable),
            degraded=bool(unavailable),
            unavailable_object_ids=unavailable,
        )

    def _increment_family_processed(self, family: str) -> None:
        current = self._families.get(family)
        if current is None:
            raise ValueError(f"family is absent from catalog: {family}")
        self._families[family] = FamilyReadiness(
            total=current.total,
            processed=min(current.processed + 1, current.total),
            failed=current.failed,
            degraded=current.degraded,
            unavailable_object_ids=current.unavailable_object_ids,
        )

    def _capabilities_locked(self) -> frozenset[IndexCapability]:
        capabilities: set[IndexCapability] = set()
        if self._catalog_generation is not None:
            capabilities.add(IndexCapability.NAVIGATION)
            if self._active_blind_generation == self._catalog_generation:
                capabilities.add(IndexCapability.EXACT_SEARCH)
        if self._documents:
            capabilities.add(IndexCapability.TEXT_SEARCH)
        if self._vectors:
            capabilities.add(IndexCapability.SEMANTIC_SEARCH)
        return frozenset(capabilities)

    def _require_catalog(self) -> None:
        self._require_unlocked()
        if self._catalog_generation is None:
            raise ValueError("catalog is not published")

    def _require_unlocked(self) -> None:
        if self._state is ReadinessState.LOCKED:
            raise CoreFSRuntimeLocked("CoreFS Runtime index is locked")

    def _require_search_key_locked(self) -> bytes:
        self._require_unlocked()
        if self._search_key is None:
            raise CoreFSRuntimeLocked("CoreFS blind-index key is unavailable")
        return bytes(self._search_key)

    def _require_state(self, state: ReadinessState) -> None:
        self._require_unlocked()
        if self._state is not state:
            raise ValueError(f"invalid readiness transition: expected {state}, got {self._state}")
