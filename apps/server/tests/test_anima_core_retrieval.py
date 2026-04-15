from __future__ import annotations

import importlib
from pathlib import Path


def test_retrieval_adapter_module_exists() -> None:
    module = importlib.import_module("anima_server.services.anima_core_retrieval")

    assert module is not None


def test_retrieval_adapter_exposes_status() -> None:
    module = importlib.import_module("anima_server.services.anima_core_retrieval")

    status = module.get_retrieval_status()

    assert isinstance(status, dict)
    assert "available" in status
    assert "capabilities" in status


def test_memory_index_round_trip(tmp_path: Path) -> None:
    module = importlib.import_module("anima_server.services.anima_core_retrieval")

    root = tmp_path / "indices"
    module.memory_index_upsert(
        root=root,
        record_id=101,
        user_id=7,
        text="user likes pour over coffee",
        source_type="memory_item",
        category="preference",
        importance=4,
        created_at=1_710_000_000,
    )

    hits = module.memory_index_search(root=root, user_id=7, query="coffee", limit=5)

    assert len(hits) == 1
    assert hits[0]["record_id"] == 101
    assert hits[0]["source_type"] == "memory_item"


def test_memory_index_delete_removes_document(tmp_path: Path) -> None:
    module = importlib.import_module("anima_server.services.anima_core_retrieval")

    root = tmp_path / "indices"
    module.memory_index_upsert(
        root=root,
        record_id=101,
        user_id=7,
        text="user likes pour over coffee",
        source_type="memory_item",
        category="preference",
        importance=4,
        created_at=1_710_000_000,
    )

    deleted = module.memory_index_delete(root=root, record_id=101, user_id=7)
    hits = module.memory_index_search(root=root, user_id=7, query="coffee", limit=5)

    assert deleted is True
    assert hits == []


def test_memory_index_vector_search_round_trip(tmp_path: Path) -> None:
    module = importlib.import_module("anima_server.services.anima_core_retrieval")

    root = tmp_path / "indices"
    module.memory_index_upsert(
        root=root,
        record_id=101,
        user_id=7,
        text="user likes pour over coffee",
        embedding=[1.0, 0.0, 0.0],
        source_type="memory_item",
        category="preference",
        importance=4,
        created_at=1_710_000_000,
    )
    module.memory_index_upsert(
        root=root,
        record_id=102,
        user_id=7,
        text="user works as a designer",
        embedding=[0.0, 1.0, 0.0],
        source_type="memory_item",
        category="fact",
        importance=3,
        created_at=1_710_000_100,
    )

    hits = module.memory_index_vector_search(
        root=root,
        user_id=7,
        query_embedding=[0.9, 0.1, 0.0],
        limit=5,
    )

    assert len(hits) == 2
    assert hits[0]["record_id"] == 101
    assert hits[0]["score"] > hits[1]["score"]


def test_retrieval_adapter_dirty_controls_round_trip(tmp_path: Path) -> None:
    module = importlib.import_module("anima_server.services.anima_core_retrieval")

    root = tmp_path / "indices"
    module.mark_retrieval_index_dirty(root=root, family="memory")

    assert module.is_retrieval_family_dirty(root=root, family="memory") is True

    module.clear_retrieval_index_dirty(root=root, family="memory")

    status = module.retrieval_manifest_status(root=root)
    assert module.is_retrieval_family_dirty(root=root, family="memory") is False
    assert status["families"]["memory"]["generation"] == 1


def test_transcript_index_round_trip(tmp_path: Path) -> None:
    module = importlib.import_module("anima_server.services.anima_core_retrieval")

    root = tmp_path / "indices"
    module.transcript_index_upsert(
        root=root,
        thread_id=42,
        user_id=7,
        transcript_ref="2026-03-28_thread-42.jsonl.enc",
        summary="Conversation about quantum physics",
        keywords=["quantum", "physics"],
        text="User asked about quantum physics and assistant explained the basics.",
        date_start=1_711_621_600,
    )

    hits = module.transcript_index_search(
        root=root,
        user_id=7,
        query="quantum physics",
        limit=5,
    )

    assert len(hits) == 1
    assert hits[0]["thread_id"] == 42
    assert hits[0]["transcript_ref"] == "2026-03-28_thread-42.jsonl.enc"
