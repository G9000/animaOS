from types import SimpleNamespace

import pytest
from anima_server.db.base import Base
from anima_server.models import MemoryItem, MemoryItemEvidence, SoulOwner
from anima_server.models.runtime import RuntimeMessage, RuntimeThread
from anima_server.models.runtime_memory import MemoryCandidate
from anima_server.services.agent.provenance import add_candidate_memory_item_evidence
from anima_server.services.corefs import soul_provenance
from conftest_runtime import runtime_db_session
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

LIVE_URI = "corefs://object/current-thread#message=native-live"
COLLISION_URI = "corefs://object/older-thread#message=native-old"


def _proof():
    return SimpleNamespace(
        user_id=1,
        threads={"7": "corefs://object/current-thread"},
        messages={"1": COLLISION_URI},
        native_messages={"native-live": LIVE_URI},
    )


def _message(db, *, owner=1, stable_id="native-live"):
    db.add(RuntimeThread(id=7, user_id=owner, status="active"))
    row = RuntimeMessage(
        id=1,
        thread_id=7,
        user_id=owner,
        sequence_id=1,
        role="user",
        content_text="I collect fountain pens.",
        corefs_message_id=stable_id,
    )
    db.add(row)
    db.flush()
    return row


@pytest.mark.parametrize(
    "owner,stable_id,have_row,have_proof,expected",
    [
        (1, "native-live", True, True, LIVE_URI),
        (2, "native-live", True, True, None),
        (1, "native-unknown", True, True, None),
        (1, "native-live", False, True, None),
        (1, "native-live", True, False, None),
    ],
)
def test_live_source_never_interprets_runtime_pk_as_canonical_numeric(
    monkeypatch,
    owner,
    stable_id,
    have_row,
    have_proof,
    expected,
):
    with runtime_db_session() as runtime_db:
        if have_row:
            _message(runtime_db, owner=owner, stable_id=stable_id)
            runtime_db.commit()
        monkeypatch.setattr(
            soul_provenance, "current_source_proof", lambda *_: _proof() if have_proof else None
        )
        monkeypatch.setattr(
            "anima_server.db.runtime.get_runtime_session_factory",
            lambda: lambda: Session(runtime_db.get_bind()),
        )
        evidence = MemoryItemEvidence(user_id=1, runtime_message_id=1, runtime_message_ids_json=[1])
        assert evidence.source_message_uri == expected
        assert evidence.source_message_uris == ([expected] if expected else [])
        assert evidence.runtime_message_id == 1
        assert evidence.runtime_message_ids_json == [1]
        state = "authenticated" if expected else "source_unavailable"
        assert evidence.provenance_metadata["provenanceState"] == state


def test_uncommitted_candidate_source_uses_supplied_runtime_transaction(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as soul_db, runtime_db_session() as runtime_db:
            monkeypatch.setattr(soul_provenance, "current_source_proof", lambda *_: _proof())
            monkeypatch.setattr(
                "anima_server.db.runtime.get_runtime_session_factory",
                lambda: pytest.fail("opened another Runtime transaction"),
            )
            soul_db.add(SoulOwner(id=1, core_owner_id="00000000-0000-4000-8000-000000000001"))
            item = MemoryItem(
                user_id=1,
                content="I collect fountain pens.",
                category="fact",
                importance=4,
                source="extraction",
            )
            soul_db.add(item)
            soul_db.flush()
            _message(runtime_db)
            candidate = MemoryCandidate(
                user_id=1, content=item.content, source="llm", source_message_ids=[1]
            )
            evidence = add_candidate_memory_item_evidence(
                soul_db,
                runtime_db=runtime_db,
                candidate=candidate,
                memory_item=item,
            )
            assert evidence.source_message_uri == LIVE_URI
            assert evidence.source_message_uris == [LIVE_URI]
            assert evidence.runtime_message_ids_json == [1]
    finally:
        engine.dispose()


def test_historical_mapping_still_uses_canonical_numeric_identity():
    uri, evidence = soul_provenance.preserve_source_reference(
        proof=_proof(),
        field="runtime_message_id",
        family="message",
        value=1,
    )
    assert uri == COLLISION_URI
    assert evidence["historicalSourceIds"]["runtime_message_id"] == 1


def test_live_profile_promotion_uses_supplied_runtime_transaction(monkeypatch):
    from anima_server.models import UserProfileFieldEvidence
    from anima_server.models.runtime_memory import ProfileUpdateCandidate
    from anima_server.services.agent.user_profile import promote_profile_update_candidate

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as soul_db, runtime_db_session() as runtime_db:
            monkeypatch.setattr(soul_provenance, "current_source_proof", lambda *_: _proof())
            monkeypatch.setattr(
                "anima_server.db.runtime.get_runtime_session_factory",
                lambda: pytest.fail("opened another Runtime transaction"),
            )
            soul_db.add(SoulOwner(id=1, core_owner_id="00000000-0000-4000-8000-000000000001"))
            _message(runtime_db)
            candidate = ProfileUpdateCandidate(
                user_id=1,
                category="preferences",
                key="hobby",
                value="fountain pens",
                confidence=0.8,
                source="llm",
                source_message_ids=[1],
            )
            promote_profile_update_candidate(soul_db, candidate=candidate, runtime_db=runtime_db)
            evidence = soul_db.query(UserProfileFieldEvidence).one()
            assert evidence.source_message_uri == LIVE_URI
    finally:
        engine.dispose()


def test_native_authenticated_catalog_resolves_live_message_stable_id():
    from anima_server.services.corefs.conversation_mutations import (
        append_canonical_message,
        create_canonical_thread,
    )
    from anima_server.services.sessions import unlock_session_store
    from conftest import managed_test_client

    with managed_test_client("anima-live-source-") as client:
        response = client.post(
            "/api/auth/register",
            json={
                "username": "source-owner",
                "password": "password-one",
                "name": "Source owner",
            },
        )
        assert response.status_code == 201, response.text
        session = unlock_session_store.resolve(response.json()["unlockToken"])
        thread = create_canonical_thread(session=session)
        message = append_canonical_message(
            session=session,
            thread_id=thread.document.thread_id,
            role="user",
            content="I collect fountain pens.",
            legacy_message_id=987654321,
        )
        with runtime_db_session() as runtime_db:
            native_thread_id = thread.document.legacy_thread_id
            runtime_db.add(
                RuntimeThread(id=native_thread_id, user_id=session.user_id, status="active")
            )
            runtime_db.add(
                RuntimeMessage(
                    id=1,
                    thread_id=native_thread_id,
                    user_id=session.user_id,
                    sequence_id=1,
                    role="user",
                    corefs_message_id=message.message_id,
                )
            )
            runtime_db.flush()
            with soul_provenance.runtime_source_transaction(runtime_db):
                evidence = MemoryItemEvidence(user_id=session.user_id, runtime_message_id=1)
            assert evidence.source_message_uri == (
                f"corefs://object/{thread.document.thread_id}#message={message.message_id}"
            )
            assert evidence.runtime_message_id == 1
            proof = soul_provenance.current_source_proof(session.user_id)
            assert proof.messages["987654321"] == evidence.source_message_uri
            assert proof.native_messages[message.message_id] == evidence.source_message_uri


def test_no_filesystem_scope_and_soul_only_evolution_keep_sources_unavailable(monkeypatch):
    monkeypatch.setattr(
        "anima_server.services.sessions.active_unlock_sessions",
        lambda *_: [SimpleNamespace(corefs_keys=None)],
    )
    assert soul_provenance.current_source_proof(1) is None


def test_transcript_history_hydrates_native_reference_without_persisting_body(monkeypatch):
    from anima_server.services.agent.persistence import list_transcript_messages
    from anima_server.services.corefs import conversation_authority

    with runtime_db_session() as runtime_db:
        row = _message(runtime_db)
        row.content_text = None
        row.content_json = None
        runtime_db.commit()
        message = SimpleNamespace(
            message_id="native-live",
            content="Authenticated body",
            role="user",
            created_at="2026-10-09T01:02:03+00:00",
            attachment_uris=(),
            display_metadata={"source": "desktop", "pills": []},
        )
        view = SimpleNamespace(messages=(message,), document=SimpleNamespace(legacy_thread_id=7))
        monkeypatch.setattr(
            conversation_authority,
            "active_conversation_authority_session",
            lambda user: SimpleNamespace(user_id=user),
        )
        monkeypatch.setattr(conversation_authority, "list_canonical_threads", lambda **_: (view,))
        history = list_transcript_messages(runtime_db, user_id=1, limit=50)
        assert [entry.content_text for entry in history] == ["Authenticated body"]
        assert history[0].source == "desktop"
        runtime_db.flush()
        runtime_db.expire(row)
        assert row.content_text is None and row.content_json is None
    with soul_provenance.authenticated_soul_only_evolution(user_id=1, owner_id="verified-owner"):
        proof = soul_provenance.current_source_proof(1)
        assert proof.owner_id == "verified-owner"
        assert proof.profile is None and not proof.native_messages
        evidence = MemoryItemEvidence(user_id=1, runtime_message_id=1)
        assert evidence.source_message_uri is None
        assert evidence.provenance_metadata["provenanceState"] == "source_unavailable"
    assert soul_provenance.current_source_proof(1) is None
