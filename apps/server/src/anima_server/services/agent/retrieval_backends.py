from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from anima_server.services import anima_core_retrieval

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MemoryRetrievalDocument:
    record_id: int
    user_id: int
    text: str
    embedding: list[float] | None
    source_type: str
    category: str
    importance: int
    created_at: int


@dataclass(frozen=True, slots=True)
class MemoryRetrievalHit:
    record_id: int
    score: float


class MemoryRetrievalBackend(Protocol):
    """Derived memory retrieval index contract.

    SQLCipher memory tables stay canonical. Implementations are rebuildable
    indexes that can be dropped and recreated from ``MemoryRetrievalDocument``
    rows loaded out of the canonical store.
    """

    def memory_documents_exist(self) -> bool: ...

    def memory_index_is_dirty(self) -> bool: ...

    def mark_memory_index_dirty(self) -> None: ...

    def clear_memory_index_dirty(self) -> None: ...

    def upsert_memory_document(self, document: MemoryRetrievalDocument) -> bool: ...

    def delete_memory_document(self, *, user_id: int, record_id: int) -> bool: ...

    def delete_user_memory_documents(self, *, user_id: int) -> bool: ...

    def search_memory(
        self,
        *,
        user_id: int,
        query: str,
        limit: int,
    ) -> list[MemoryRetrievalHit]: ...

    def search_memory_by_vector(
        self,
        *,
        user_id: int,
        query_embedding: list[float],
        limit: int,
    ) -> list[MemoryRetrievalHit]: ...


def _coerce_hits(hits: list[dict[str, object]]) -> list[MemoryRetrievalHit]:
    coerced: list[MemoryRetrievalHit] = []
    for hit in hits:
        record_id = hit.get("record_id")
        score = hit.get("score", 0.0)
        if record_id is None:
            continue
        try:
            coerced.append(MemoryRetrievalHit(record_id=int(record_id), score=float(score)))
        except (TypeError, ValueError):
            continue
    return coerced


class NativeMemoryRetrievalBackend:
    """Native local retrieval index backed by ``anima_core_retrieval``."""

    def __init__(self, *, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else anima_core_retrieval.get_retrieval_root()

    def memory_documents_exist(self) -> bool:
        return (self.root / "memory" / "documents.json").exists()

    def memory_index_is_dirty(self) -> bool:
        return anima_core_retrieval.is_retrieval_family_dirty(root=self.root, family="memory")

    def mark_memory_index_dirty(self) -> None:
        anima_core_retrieval.mark_retrieval_index_dirty(root=self.root, family="memory")

    def clear_memory_index_dirty(self) -> None:
        anima_core_retrieval.clear_retrieval_index_dirty(root=self.root, family="memory")

    def upsert_memory_document(self, document: MemoryRetrievalDocument) -> bool:
        try:
            anima_core_retrieval.memory_index_upsert(
                root=self.root,
                record_id=document.record_id,
                user_id=document.user_id,
                text=document.text,
                embedding=document.embedding,
                source_type=document.source_type,
                category=document.category,
                importance=document.importance,
                created_at=document.created_at,
            )
            return True
        except RuntimeError:
            logger.debug("Native memory retrieval upsert is unavailable for item %s", document.record_id)
            return False
        except Exception:
            logger.warning(
                "Failed to upsert memory item %s into the native retrieval index",
                document.record_id,
                exc_info=True,
            )
            return False

    def delete_memory_document(self, *, user_id: int, record_id: int) -> bool:
        try:
            anima_core_retrieval.memory_index_delete(
                root=self.root,
                record_id=record_id,
                user_id=user_id,
            )
            return True
        except RuntimeError:
            logger.debug("Native memory retrieval delete is unavailable for item %s", record_id)
            return False
        except Exception:
            logger.warning(
                "Failed to delete memory item %s from the native retrieval index",
                record_id,
                exc_info=True,
            )
            return False

    def delete_user_memory_documents(self, *, user_id: int) -> bool:
        try:
            anima_core_retrieval.memory_index_delete_user_documents(
                root=self.root,
                user_id=user_id,
            )
            return True
        except RuntimeError:
            logger.debug("Native memory retrieval user purge is unavailable for user %s", user_id)
            return False
        except Exception:
            logger.warning(
                "Failed to purge native memory retrieval documents for user %s",
                user_id,
                exc_info=True,
            )
            return False

    def search_memory(
        self,
        *,
        user_id: int,
        query: str,
        limit: int,
    ) -> list[MemoryRetrievalHit]:
        return _coerce_hits(
            anima_core_retrieval.memory_index_search(
                root=self.root,
                user_id=user_id,
                query=query,
                limit=limit,
            )
        )

    def search_memory_by_vector(
        self,
        *,
        user_id: int,
        query_embedding: list[float],
        limit: int,
    ) -> list[MemoryRetrievalHit]:
        return _coerce_hits(
            anima_core_retrieval.memory_index_vector_search(
                root=self.root,
                user_id=user_id,
                query_embedding=query_embedding,
                limit=limit,
            )
        )


def get_memory_retrieval_backend(
    *,
    root: Path | str | None = None,
) -> MemoryRetrievalBackend:
    return NativeMemoryRetrievalBackend(root=root)
