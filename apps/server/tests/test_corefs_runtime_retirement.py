from __future__ import annotations

import pytest
from anima_server.models.corefs_runtime import CoreFSRuntimeBinding, CoreFSSealedPayload
from anima_server.models.runtime import RuntimeKnowledgeBundleRun
from anima_server.services.corefs import sealed_runtime
from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
from conftest_runtime import runtime_db_session
from sqlalchemy import select


def test_unsupported_runtime_plaintext_is_unusable_without_conversion(monkeypatch):
    with runtime_db_session() as db:
        row = RuntimeKnowledgeBundleRun(
            user_id=7,
            run_type="adapter:obsolete",
            status="failed",
            input_json={"private": "preserve original cache bytes"},
        )
        db.add_all(
            [
                row,
                CoreFSRuntimeBinding(
                    binding_slot=1, core_id="core-a", local_instance_id="instance-a"
                ),
            ]
        )
        db.flush()
        index = CoreFSProgressiveIndex("core-a")
        index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
        assert sealed_runtime.validate_current_runtime_rows(db, index=index, user_id=7) == 1
        assert db.scalar(select(CoreFSSealedPayload.id)) is None
        assert db.execute(
            select(RuntimeKnowledgeBundleRun.__table__.c.input_json)
        ).scalar_one() == {"private": "preserve original cache bytes"}
        index.begin_catalog()
        index.publish_catalog(catalog_generation=1, families={"conversations": 0})
        assert index.snapshot().families["runtime_cache"].unavailable_object_ids
        monkeypatch.setattr(sealed_runtime, "_active_runtime_index", lambda _owner: index)
        db.expunge_all()
        with pytest.raises(sealed_runtime.RuntimeCacheRebuildRequired, match="rebuild"):
            db.get(RuntimeKnowledgeBundleRun, row.id)
        index.clear_unlocked_state()


def test_current_sealed_runtime_payload_remains_available(monkeypatch):
    with runtime_db_session() as db:
        row = RuntimeKnowledgeBundleRun(user_id=7, run_type="adapter:current", status="failed")
        db.add_all(
            [
                row,
                CoreFSRuntimeBinding(
                    binding_slot=1, core_id="core-a", local_instance_id="instance-a"
                ),
            ]
        )
        db.flush()
        index = CoreFSProgressiveIndex("core-a")
        index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
        sealed_runtime.seal_runtime_record(
            db,
            index=index,
            row_type="runtime_knowledge_bundle_run",
            row_id=row.id,
            owner_id=7,
            payload={
                "input_json": {"current": "authenticated"},
                "result_json": None,
                "error_json": None,
            },
        )
        assert sealed_runtime.validate_current_runtime_rows(db, index=index, user_id=7) == 0
        monkeypatch.setattr(sealed_runtime, "_active_runtime_index", lambda _owner: index)
        db.expunge_all()
        assert db.get(RuntimeKnowledgeBundleRun, row.id).input_json == {"current": "authenticated"}
        index.clear_unlocked_state()


def test_unsupported_runtime_cache_does_not_prevent_soul_session_publication(monkeypatch):
    from anima_server.services.sessions import UnlockSessionStore
    from sqlalchemy.orm import sessionmaker

    with runtime_db_session() as db:
        row = RuntimeKnowledgeBundleRun(
            user_id=7,
            run_type="adapter:obsolete",
            status="failed",
            error_json={"private": "unsupported cache"},
        )
        db.add_all(
            [
                row,
                CoreFSRuntimeBinding(
                    binding_slot=1, core_id="core-a", local_instance_id="instance-a"
                ),
            ]
        )
        db.commit()
        monkeypatch.setattr(
            "anima_server.db.runtime.get_runtime_session_factory",
            lambda: sessionmaker(db.get_bind(), expire_on_commit=False),
        )
        index = CoreFSProgressiveIndex("core-a")
        index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
        store = UnlockSessionStore(runtime_index_factory=lambda _keys, _key: index)
        store.set_sqlcipher_key(b"k" * 32)
        try:
            token = store.create(7, {"memories": b"m" * 32})
            session = store.resolve(token)
            assert session.user_id == 7
            assert session.deks["memories"] == b"m" * 32
            assert session.corefs_keys is None
            assert session.runtime_index.snapshot().families["runtime_cache"].degraded
            assert db.scalar(select(CoreFSSealedPayload.id)) is None
        finally:
            store.clear()
            store.clear_sqlcipher_key()


def test_runtime_cache_validation_rejects_foreign_instance_without_mutation():
    with runtime_db_session() as db:
        db.add(
            CoreFSRuntimeBinding(
                binding_slot=1, core_id="other-core", local_instance_id="instance-a"
            )
        )
        db.flush()
        index = CoreFSProgressiveIndex("core-a")
        index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
        with pytest.raises(ValueError, match="binding"):
            sealed_runtime.validate_current_runtime_rows(db, index=index, user_id=7)
        assert db.scalar(select(CoreFSRuntimeBinding.core_id)) == "other-core"
        index.clear_unlocked_state()
