"""ARH-003: optimistic locking for soul-block and working-context writes.

Background reflections snapshot identity blocks, hold them across a slow
LLM call, then full-replace — without a version check, a user-driven write
landing mid-reflection was silently erased.
"""

from __future__ import annotations

import pytest
from anima_server.db.base import Base
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models import User
from anima_server.services.agent.intentions import (
    MAX_PROCEDURAL_RULES,
    merge_learned_rules,
)
from anima_server.services.agent.self_model import (
    get_working_context,
    set_active_intentions,
    set_working_context,
)
from anima_server.services.agent.soul_blocks import (
    SoulBlockConflict,
    append_to_soul_block,
    full_replace_soul_block,
    set_soul_block,
)
from anima_server.services.data_crypto import df
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture()
def soul_db():
    """In-memory SQLite session with soul tables."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    user = User(id=1, username="test", display_name="Test", password_hash="x")
    session.add(user)
    session.commit()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def runtime_db():
    """In-memory SQLite session with runtime consciousness tables."""
    import anima_server.models.runtime_consciousness  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    RuntimeBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    yield session
    session.close()
    engine.dispose()


def _plaintext(user_id: int, block) -> str:
    return df(user_id, block.content, table="self_model_blocks", field="content")


# --------------------------------------------------------------------------- #
# set_soul_block / full_replace_soul_block (soul DB)
# --------------------------------------------------------------------------- #


class TestSoulBlockLocking:
    def test_expected_absent_creates(self, soul_db: Session) -> None:
        block = set_soul_block(
            soul_db,
            user_id=1,
            section="persona",
            content="warm and curious",
            updated_by="test",
            expected_version=0,
        )
        assert block.version == 1

    def test_expected_absent_conflicts_when_block_exists(
        self, soul_db: Session
    ) -> None:
        set_soul_block(
            soul_db, user_id=1, section="persona", content="v1", updated_by="test"
        )
        with pytest.raises(SoulBlockConflict):
            set_soul_block(
                soul_db,
                user_id=1,
                section="persona",
                content="clobber",
                updated_by="test",
                expected_version=0,
            )

    def test_matching_version_writes_and_bumps(self, soul_db: Session) -> None:
        set_soul_block(
            soul_db, user_id=1, section="persona", content="v1", updated_by="test"
        )
        block = set_soul_block(
            soul_db,
            user_id=1,
            section="persona",
            content="v2",
            updated_by="test",
            expected_version=1,
        )
        assert block.version == 2
        assert _plaintext(1, block) == "v2"

    def test_stale_version_refused(self, soul_db: Session) -> None:
        set_soul_block(
            soul_db, user_id=1, section="persona", content="v1", updated_by="test"
        )
        set_soul_block(
            soul_db,
            user_id=1,
            section="persona",
            content="v2",
            updated_by="test",
        )
        with pytest.raises(SoulBlockConflict) as exc_info:
            full_replace_soul_block(
                soul_db,
                user_id=1,
                section="persona",
                content="stale rewrite",
                expected_version=1,
            )
        assert exc_info.value.actual_version == 2
        assert exc_info.value.expected_version == 1

    def test_none_skips_check(self, soul_db: Session) -> None:
        set_soul_block(
            soul_db, user_id=1, section="persona", content="v1", updated_by="test"
        )
        block = full_replace_soul_block(
            soul_db, user_id=1, section="persona", content="unversioned write"
        )
        assert block.version == 2

    def test_concurrent_append_survives_stale_full_replace(
        self, soul_db: Session
    ) -> None:
        """The motivating race: a reflection snapshots the block, a pending
        op appends 'call me Jay' mid-flight, and the reflection's stale
        full-replace must be refused — not silently erase the append."""
        block = set_soul_block(
            soul_db,
            user_id=1,
            section="human",
            content="Prefers direct answers.",
            updated_by="test",
        )
        snapshot_version = block.version

        # Concurrent user-driven write while the "LLM call" is in flight.
        append_to_soul_block(
            soul_db,
            user_id=1,
            section="human",
            content="Wants to be called Jay.",
        )

        with pytest.raises(SoulBlockConflict):
            full_replace_soul_block(
                soul_db,
                user_id=1,
                section="human",
                content="Prefers direct answers. Enjoys hiking.",
                expected_version=snapshot_version,
            )

        from anima_server.services.agent.soul_blocks import _get_soul_block

        current = _get_soul_block(soul_db, user_id=1, section="human")
        assert current is not None
        assert "Wants to be called Jay." in _plaintext(1, current)


# --------------------------------------------------------------------------- #
# set_working_context / set_active_intentions (runtime DB)
# --------------------------------------------------------------------------- #


class TestWorkingContextLocking:
    def test_working_context_conflict(self, runtime_db: Session) -> None:
        set_working_context(
            runtime_db,
            user_id=1,
            section="working_memory",
            content="- note one",
        )
        set_working_context(
            runtime_db,
            user_id=1,
            section="working_memory",
            content="- note one\n- note two",
        )
        with pytest.raises(SoulBlockConflict):
            set_working_context(
                runtime_db,
                user_id=1,
                section="working_memory",
                content="stale rewrite",
                expected_version=1,
            )
        row = set_working_context(
            runtime_db,
            user_id=1,
            section="working_memory",
            content="fresh rewrite",
            expected_version=2,
        )
        assert row.version == 3

    def test_active_intentions_conflict(self, runtime_db: Session) -> None:
        set_active_intentions(runtime_db, user_id=1, content="# Intentions")
        with pytest.raises(SoulBlockConflict):
            set_active_intentions(
                runtime_db,
                user_id=1,
                content="stale",
                expected_version=0,
            )
        row = set_active_intentions(
            runtime_db, user_id=1, content="updated", expected_version=1
        )
        assert row.version == 2


# --------------------------------------------------------------------------- #
# merge_learned_rules
# --------------------------------------------------------------------------- #


class TestMergeLearnedRules:
    def test_replaces_existing_section_instead_of_duplicating(self) -> None:
        base = "# Intentions\n- stay curious\n\n## Learned Rules\n- rule a"
        merged = merge_learned_rules(base, ["- rule b"])
        assert merged.count("## Learned Rules") == 1
        assert "- rule a" in merged
        assert "- rule b" in merged
        assert "- stay curious" in merged

    def test_idempotent_across_repeated_runs(self) -> None:
        text = "# Intentions\n- stay curious"
        for _ in range(5):
            text = merge_learned_rules(text, ["- always confirm names"])
        assert text.count("## Learned Rules") == 1
        assert text.count("- always confirm names") == 1

    def test_dedup_is_case_insensitive(self) -> None:
        base = "## Learned Rules\n- Ask Before Acting"
        merged = merge_learned_rules(base, ["- ask before acting"])
        assert merged.lower().count("- ask before acting") == 1

    def test_caps_at_max_keeping_most_recent(self) -> None:
        existing = "\n".join(f"- old rule {i}" for i in range(MAX_PROCEDURAL_RULES))
        base = f"# Intentions\n\n## Learned Rules\n{existing}"
        merged = merge_learned_rules(base, ["- brand new rule"])
        rules = [
            line
            for line in merged.splitlines()
            if line.strip().startswith("- ") and "rule" in line
        ]
        assert len(rules) == MAX_PROCEDURAL_RULES
        assert "- brand new rule" in merged
        assert "- old rule 0" not in merged

    def test_no_rules_returns_base_without_section(self) -> None:
        assert merge_learned_rules("# Intentions\n- goal", []) == "# Intentions\n- goal"


# --------------------------------------------------------------------------- #
# Quick reflection re-applies deltas after a concurrent write
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_quick_reflection_reapplies_wm_deltas_after_concurrent_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A working-memory write landing during the reflection's LLM call must
    survive: the reflection re-applies its add/remove deltas onto the fresh
    content instead of clobbering it with the stale snapshot."""
    import json

    from anima_server.config import settings
    from anima_server.services.agent import inner_monologue

    soul_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(soul_engine)
    soul_factory = sessionmaker(bind=soul_engine, expire_on_commit=False)

    import anima_server.models.runtime_consciousness  # noqa: F401

    runtime_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    RuntimeBase.metadata.create_all(runtime_engine)
    runtime_factory = sessionmaker(bind=runtime_engine, expire_on_commit=False)

    try:
        with soul_factory() as soul_db:
            user = User(username="wm-race", display_name="WM Race", password_hash="x")
            soul_db.add(user)
            soul_db.commit()
            user_id = user.id

        with runtime_factory() as pg_db:
            set_working_context(
                pg_db,
                user_id=user_id,
                section="working_memory",
                content="# Things I'm Holding in Mind\n- existing note",
            )
            pg_db.commit()

        async def fake_llm(prompt: str, system: str) -> str:
            # Simulate a turn-driven write landing while the reflection
            # LLM call is in flight.
            with runtime_factory() as pg_db:
                set_working_context(
                    pg_db,
                    user_id=user_id,
                    section="working_memory",
                    content=(
                        "# Things I'm Holding in Mind\n"
                        "- existing note\n"
                        "- user wants to be called Jay"
                    ),
                )
                pg_db.commit()
            return json.dumps(
                {
                    "working_memory_updates": [
                        {"action": "add", "item": "follow up on the demo"}
                    ],
                    "quick_take": "ok",
                }
            )

        monkeypatch.setattr(settings, "agent_provider", "openai")
        monkeypatch.setattr(inner_monologue, "_call_llm", fake_llm)

        result = await inner_monologue.run_quick_reflection(
            user_id=user_id,
            conversation_text="User: remember the demo\nAssistant: noted",
            db_factory=soul_factory,
            runtime_db_factory=runtime_factory,
        )

        assert result.working_memory_updated is True

        with runtime_factory() as pg_db:
            row = get_working_context(pg_db, user_id=user_id)["working_memory"]

        assert "- user wants to be called Jay" in row.content
        assert "- follow up on the demo" in row.content
    finally:
        soul_engine.dispose()
        runtime_engine.dispose()
