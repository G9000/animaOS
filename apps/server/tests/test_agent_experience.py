from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from anima_server.db.base import Base
from anima_server.models import AgentExperience, AgentSkill, ExperienceClusterState, User
from anima_server.services.data_crypto import df
from sqlalchemy import create_engine, select
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


def _make_user(db: Session) -> User:
    user = User(username="experience-user", display_name="Experience User", password_hash="x")
    db.add(user)
    db.flush()
    return user


def test_store_tool_failure_experience_encrypts_text_and_logs_growth() -> None:
    from anima_server.models.soul_consciousness import GrowthLogEntry
    from anima_server.services.agent.agent_experience import (
        AgentExperienceCandidate,
        store_agent_experience,
    )

    with _db_session() as db:
        user = _make_user(db)
        experience = store_agent_experience(
            db,
            user_id=user.id,
            candidate=AgentExperienceCandidate(
                task_intent="Recover from a failed local search tool call",
                approach=(
                    "1. Tried the local search tool.\n"
                    "2. The tool failed with a timeout.\n"
                    "3. Recovered by narrowing the query and rerunning it.\n\n"
                    "Outcome: answered with the narrower result."
                ),
                quality_score=0.72,
                source_thread_id=17,
                source_run_id=23,
                tool_names=("search_memory",),
                turn_count=1,
                embedding=[1.0, 0.0, 0.0],
            ),
        )
        growth = list(db.scalars(select(GrowthLogEntry)).all())

    assert experience.id is not None
    assert experience.source_thread_id == 17
    assert experience.source_run_id == 23
    assert experience.tool_names_json == ["search_memory"]
    assert experience.embedding_json == [1.0, 0.0, 0.0]
    assert df(user.id, experience.task_intent, table="agent_experiences", field="task_intent") == (
        "Recover from a failed local search tool call"
    )
    assert "timeout" in df(
        user.id,
        experience.approach,
        table="agent_experiences",
        field="approach",
    )
    assert [entry.entry for entry in growth] == [
        "Learned from experience: Recover from a failed local search tool call (quality: 0.72)"
    ]


def test_experience_clustering_is_stable_across_state_reload() -> None:
    from anima_server.services.agent.agent_experience import (
        AgentExperienceCandidate,
        assign_experience_to_cluster,
        store_agent_experience,
    )

    with _db_session() as db:
        user = _make_user(db)
        first = store_agent_experience(
            db,
            user_id=user.id,
            candidate=AgentExperienceCandidate(
                task_intent="Plan a weekend trip within a budget",
                approach="Ask dates, budget, and constraints before suggesting options.",
                quality_score=0.84,
                source_thread_id=1,
                source_run_id=1,
                tool_names=("calendar",),
                turn_count=2,
                embedding=[1.0, 0.0, 0.0],
            ),
        )
        first_cluster = assign_experience_to_cluster(db, user_id=user.id, experience=first)
        db.commit()

        second = store_agent_experience(
            db,
            user_id=user.id,
            candidate=AgentExperienceCandidate(
                task_intent="Help plan another budget-friendly weekend trip",
                approach="Start with dates and budget, then compare options.",
                quality_score=0.88,
                source_thread_id=2,
                source_run_id=2,
                tool_names=("calendar",),
                turn_count=2,
                embedding=[0.98, 0.02, 0.0],
                created_at=datetime.now(UTC) + timedelta(days=3),
            ),
        )
        second_cluster = assign_experience_to_cluster(db, user_id=user.id, experience=second)
        distant = store_agent_experience(
            db,
            user_id=user.id,
            candidate=AgentExperienceCandidate(
                task_intent="Debug a failed database migration",
                approach="Inspect the migration chain and reproduce the failing revision.",
                quality_score=0.78,
                source_thread_id=3,
                source_run_id=3,
                tool_names=("shell",),
                turn_count=3,
                embedding=[0.0, 1.0, 0.0],
            ),
        )
        distant_cluster = assign_experience_to_cluster(db, user_id=user.id, experience=distant)
        db.commit()
        reloaded_second = db.get(AgentExperience, second.id)

    assert first_cluster == second_cluster
    assert distant_cluster != first_cluster
    assert reloaded_second is not None
    assert reloaded_second.cluster_id == first_cluster


def test_existing_experience_cluster_state_updates_persist_across_sessions() -> None:
    from anima_server.services.agent.agent_experience import (
        AgentExperienceCandidate,
        assign_experience_to_cluster,
        store_agent_experience,
    )

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
    try:
        with factory() as db:
            user = _make_user(db)
            first = store_agent_experience(
                db,
                user_id=user.id,
                candidate=AgentExperienceCandidate(
                    task_intent="Plan a weekend trip within a budget",
                    approach="Ask dates, budget, and constraints before suggesting options.",
                    quality_score=0.84,
                    embedding=[1.0, 0.0],
                ),
            )
            cluster_id = assign_experience_to_cluster(db, user_id=user.id, experience=first)
            user_id = user.id
            first_id = first.id
            db.commit()

        with factory() as db:
            second = store_agent_experience(
                db,
                user_id=user_id,
                candidate=AgentExperienceCandidate(
                    task_intent="Help plan another budget-friendly weekend trip",
                    approach="Start with dates and budget, then compare options.",
                    quality_score=0.88,
                    embedding=[0.98, 0.02],
                    created_at=datetime.now(UTC) + timedelta(days=3),
                ),
            )
            assert assign_experience_to_cluster(db, user_id=user_id, experience=second) == cluster_id
            second_id = second.id
            db.commit()

        with factory() as db:
            state = db.scalar(
                select(ExperienceClusterState).where(ExperienceClusterState.user_id == user_id)
            )

        assert state is not None
        cluster = state.state_json["clusters"][cluster_id]
        assert cluster["count"] == 2
        assert cluster["experience_ids"] == [first_id, second_id]
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_unembedded_experiences_do_not_enter_skill_clusters() -> None:
    from anima_server.services.agent.agent_experience import (
        AgentExperienceCandidate,
        assign_experience_to_cluster,
        maybe_distill_skill_for_cluster,
        store_agent_experience,
    )

    with _db_session() as db:
        user = _make_user(db)
        for idx in range(3):
            experience = store_agent_experience(
                db,
                user_id=user.id,
                candidate=AgentExperienceCandidate(
                    task_intent=f"Unembedded task {idx}",
                    approach="Embedding provider was unavailable.",
                    quality_score=0.7,
                    embedding=None,
                ),
            )
            assert assign_experience_to_cluster(db, user_id=user.id, experience=experience) is None
            assert experience.cluster_id is None

        embedded = store_agent_experience(
            db,
            user_id=user.id,
            candidate=AgentExperienceCandidate(
                task_intent="Plan a weekend trip within a budget",
                approach="Ask dates and budget, then compare options.",
                quality_score=0.84,
                embedding=[1.0, 0.0],
            ),
        )
        cluster_id = assign_experience_to_cluster(db, user_id=user.id, experience=embedded)
        skill = maybe_distill_skill_for_cluster(db, user_id=user.id, cluster_id=cluster_id)
        db.flush()
        states = list(db.scalars(select(ExperienceClusterState)).all())
        skills = list(db.scalars(select(AgentSkill)).all())

    assert cluster_id == f"cluster_{user.id}_000"
    assert skill is None
    assert len(states) == 1
    assert skills == []


def test_skill_distillation_waits_for_three_experiences_then_creates_skill() -> None:
    from anima_server.services.agent.agent_experience import (
        AgentExperienceCandidate,
        assign_experience_to_cluster,
        maybe_distill_skill_for_cluster,
        store_agent_experience,
    )

    with _db_session() as db:
        user = _make_user(db)
        cluster_id = None
        for idx, quality in enumerate((0.9, 0.82), start=1):
            experience = store_agent_experience(
                db,
                user_id=user.id,
                candidate=AgentExperienceCandidate(
                    task_intent=f"Plan trip case {idx}",
                    approach="Ask constraints before suggesting options.",
                    quality_score=quality,
                    source_thread_id=idx,
                    source_run_id=idx,
                    tool_names=("calendar",),
                    turn_count=2,
                    embedding=[1.0, 0.0],
                ),
            )
            cluster_id = assign_experience_to_cluster(db, user_id=user.id, experience=experience)

        assert cluster_id is not None
        assert maybe_distill_skill_for_cluster(db, user_id=user.id, cluster_id=cluster_id) is None

        third = store_agent_experience(
            db,
            user_id=user.id,
            candidate=AgentExperienceCandidate(
                task_intent="Plan a birthday trip within budget",
                approach="Ask constraints, compare options, and avoid over-budget suggestions.",
                quality_score=0.4,
                source_thread_id=3,
                source_run_id=3,
                tool_names=("calendar",),
                turn_count=2,
                embedding=[0.99, 0.01],
            ),
        )
        cluster_id = assign_experience_to_cluster(db, user_id=user.id, experience=third)
        skill = maybe_distill_skill_for_cluster(db, user_id=user.id, cluster_id=cluster_id)
        db.flush()

    assert skill is not None
    assert skill.cluster_id == cluster_id
    assert skill.experience_count == 3
    assert skill.confidence >= 0.6
    assert "constraints" in df(user.id, skill.content, table="agent_skills", field="content")
    assert "Pitfalls" in df(user.id, skill.content, table="agent_skills", field="content")


def test_learned_skill_block_takes_priority_over_raw_experiences() -> None:
    from anima_server.services.agent.agent_experience import (
        AgentExperienceCandidate,
        AgentSkillCandidate,
        store_agent_experience,
        upsert_agent_skill,
    )
    from anima_server.services.agent.memory_blocks import (
        build_learned_skills_block,
        build_past_approaches_block,
    )

    with _db_session() as db:
        user = _make_user(db)
        store_agent_experience(
            db,
            user_id=user.id,
            candidate=AgentExperienceCandidate(
                task_intent="Plan a weekend trip within a budget",
                approach="Ask dates and budget, then compare options.",
                quality_score=0.84,
                source_thread_id=1,
                source_run_id=1,
                tool_names=("calendar",),
                turn_count=2,
                embedding=[1.0, 0.0],
                cluster_id="cluster_1_000",
            ),
        )
        upsert_agent_skill(
            db,
            user_id=user.id,
            skill=AgentSkillCandidate(
                cluster_id="cluster_1_000",
                name="Trip Planning",
                description="Use when helping plan budget-conscious trips.",
                content="1. Ask constraints.\n2. Compare options.\nPitfalls:\n- Avoid over-budget ideas.",
                confidence=0.82,
                experience_count=3,
                embedding=[1.0, 0.0],
            ),
        )
        skill_block = build_learned_skills_block(
            db,
            user_id=user.id,
            query_embedding=[1.0, 0.0],
        )
        approaches_block = build_past_approaches_block(
            db,
            user_id=user.id,
            query_embedding=[1.0, 0.0],
        )

    assert skill_block is not None
    assert skill_block.label == "learned_skills"
    assert "Trip Planning" in skill_block.value
    assert "confidence: 0.82" in skill_block.value
    assert approaches_block is None
