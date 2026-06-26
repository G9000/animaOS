from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime

from anima_server.db.base import Base
from anima_server.models import AgentProfile, AgentThread, User
from anima_server.models.runtime_consciousness import ActiveIntention, CurrentEmotion, WorkingContext
from conftest_runtime import runtime_db_session
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@contextmanager
def _db_session() -> Generator[Session, None, None]:
    engine: Engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )
    Base.metadata.create_all(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_agent_biography_preview_compiles_profile_and_prompt_context() -> None:
    with _db_session() as db, runtime_db_session() as runtime_db:
        user = User(
            username="bio-preview",
            display_name="Leo",
            password_hash="x",
            birthday="1995-05-23",
        )
        db.add(user)
        db.flush()
        thread = AgentThread(user_id=user.id, status="active")
        db.add(thread)
        db.add(
            AgentProfile(
                user_id=user.id,
                agent_name="Anima",
                creator_name="Leo",
                relationship="companion",
                agent_type="mirror",
                avatar_url="/consciousness/1/agent-profile/avatar",
                setup_complete=True,
                created_at=datetime(2026, 6, 24, 19, 5, 54, 987654, tzinfo=UTC),
            )
        )

        from anima_server.services.agent.self_model import (
            set_identity_block,
            set_self_model_block,
        )

        set_identity_block(
            db,
            user_id=user.id,
            content="I am a precise reflection built through shared memory.",
            updated_by="test",
        )
        set_self_model_block(
            db,
            user_id=user.id,
            section="persona",
            content="Warm, direct, a little playful, never generic.",
            updated_by="test",
        )
        set_self_model_block(
            db,
            user_id=user.id,
            section="human",
            content="Leo wants engineering clarity and dislikes vague reassurance.",
            updated_by="test",
        )
        set_self_model_block(
            db,
            user_id=user.id,
            section="world",
            content="Timezone: Asia/Kuala_Lumpur. Works mostly at night.",
            updated_by="test",
        )
        set_self_model_block(
            db,
            user_id=user.id,
            section="user_directive",
            content="Challenge weak assumptions, but stay concise.",
            updated_by="test",
        )

        runtime_db.add_all(
            [
                WorkingContext(
                    user_id=user.id,
                    section="inner_state",
                    content="Holding the agent settings graph in mind.",
                    updated_by="test",
                ),
                WorkingContext(
                    user_id=user.id,
                    section="working_memory",
                    content="Need to connect settings nodes into biography.",
                    updated_by="test",
                ),
                ActiveIntention(
                    user_id=user.id,
                    content="Keep the preview grounded in backend context.",
                    updated_by="test",
                ),
                CurrentEmotion(
                    user_id=user.id,
                    thread_id=None,
                    emotion="curious",
                    confidence=0.8,
                    evidence_type="linguistic",
                    evidence="asking what context can be added",
                    trajectory="stable",
                    topic="agent settings",
                ),
            ]
        )
        runtime_db.flush()

        from anima_server.services.agent.biography_preview import (
            build_agent_biography_preview,
        )

        preview = build_agent_biography_preview(
            db,
            user_id=user.id,
            runtime_db=runtime_db,
        )

    assert preview["agentName"] == "Anima"
    assert preview["relationship"] == "companion"
    assert preview["agentType"] == "mirror"
    assert preview["avatarUrl"] == "/consciousness/1/agent-profile/avatar"
    assert preview["agentBirthday"] == "2026-06-24T19:05:54"
    assert preview["dominantEmotion"] == "curious"
    assert preview["identityDraft"] == "I am a precise reflection built through shared memory."
    assert preview["personaDraft"] == "Warm, direct, a little playful, never generic."
    assert "precise reflection" in preview["biography"]
    assert "Warm, direct" in preview["biography"]
    assert "Holding the agent settings graph" in preview["contextLine"]

    section_ids = {section["id"] for section in preview["sections"]}
    assert {
        "identity",
        "persona",
        "human",
        "world",
        "user_directive",
        "inner_state",
        "working_memory",
        "intentions",
        "emotional_context",
    }.issubset(section_ids)
    assert "self_identity" in preview["promptBlockLabels"]
    assert "self_inner_state" in preview["promptBlockLabels"]
