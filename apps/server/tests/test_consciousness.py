"""Tests for the consciousness layer: self-model, emotional intelligence, intentions, inner monologue."""

from __future__ import annotations

import pytest
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from anima_server.db.base import Base
from anima_server.models import AgentThread, User


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


def _setup(db: Session) -> tuple[User, AgentThread]:
    user = User(username="consciousness-test",
                display_name="Tester", password_hash="x")
    db.add(user)
    db.flush()
    thread = AgentThread(user_id=user.id, status="active")
    db.add(thread)
    db.flush()
    return user, thread


# --- Self-Model Tests ---


def test_seed_self_model() -> None:
    with _db_session() as db:
        user, _ = _setup(db)
        from anima_server.services.agent.self_model import seed_self_model, SECTIONS

        blocks = seed_self_model(db, user_id=user.id)
        assert set(blocks.keys()) == set(SECTIONS)
        assert blocks["identity"].version == 1
        assert "Who I Am" in blocks["identity"].content
        assert blocks["growth_log"].content == ""


def test_seed_self_model_idempotent() -> None:
    with _db_session() as db:
        user, _ = _setup(db)
        from anima_server.services.agent.self_model import seed_self_model

        blocks1 = seed_self_model(db, user_id=user.id)
        blocks2 = seed_self_model(db, user_id=user.id)
        assert blocks1["identity"].id == blocks2["identity"].id


def test_set_self_model_block_bumps_version() -> None:
    with _db_session() as db:
        user, _ = _setup(db)
        from anima_server.services.agent.self_model import seed_self_model, set_self_model_block

        seed_self_model(db, user_id=user.id)
        block = set_self_model_block(
            db, user_id=user.id, section="inner_state",
            content="Updated inner state", updated_by="sleep_time",
        )
        assert block.version == 2
        assert block.content == "Updated inner state"
        assert block.updated_by == "sleep_time"


def test_get_all_self_model_blocks() -> None:
    with _db_session() as db:
        user, _ = _setup(db)
        from anima_server.services.agent.self_model import seed_self_model, get_all_self_model_blocks

        seed_self_model(db, user_id=user.id)
        blocks = get_all_self_model_blocks(db, user_id=user.id)
        assert len(blocks) == 5
        assert "identity" in blocks
        assert "inner_state" in blocks


def test_append_growth_log_entry() -> None:
    with _db_session() as db:
        user, _ = _setup(db)
        from anima_server.services.agent.self_model import seed_self_model, append_growth_log_entry

        seed_self_model(db, user_id=user.id)
        block = append_growth_log_entry(
            db, user_id=user.id,
            entry="Communication style refined",
        )
        assert "Communication style refined" in block.content
        assert block.version == 2


def test_ensure_self_model_exists() -> None:
    with _db_session() as db:
        user, _ = _setup(db)
        from anima_server.services.agent.self_model import ensure_self_model_exists, get_all_self_model_blocks

        ensure_self_model_exists(db, user_id=user.id)
        blocks = get_all_self_model_blocks(db, user_id=user.id)
        assert len(blocks) == 5

        # Second call is a no-op
        ensure_self_model_exists(db, user_id=user.id)
        blocks2 = get_all_self_model_blocks(db, user_id=user.id)
        assert len(blocks2) == 5


def test_render_self_model_section_budget() -> None:
    with _db_session() as db:
        user, _ = _setup(db)
        from anima_server.services.agent.self_model import seed_self_model, set_self_model_block, render_self_model_section

        seed_self_model(db, user_id=user.id)
        long_content = "x" * 5000
        block = set_self_model_block(
            db, user_id=user.id, section="identity",
            content=long_content, updated_by="test",
        )
        rendered = render_self_model_section(block, budget=100)
        assert len(rendered) == 100


# --- Emotional Intelligence Tests ---


def test_record_emotional_signal() -> None:
    with _db_session() as db:
        user, thread = _setup(db)
        from anima_server.services.agent.emotional_intelligence import record_emotional_signal

        signal = record_emotional_signal(
            db, user_id=user.id, thread_id=thread.id,
            emotion="frustrated", confidence=0.8,
            evidence_type="linguistic", evidence="shorter messages, repetition",
            trajectory="escalating", topic="debugging",
        )
        assert signal is not None
        assert signal.emotion == "frustrated"
        assert signal.confidence == 0.8
        assert signal.trajectory == "escalating"


def test_emotional_signal_below_threshold_ignored() -> None:
    with _db_session() as db:
        user, thread = _setup(db)
        from anima_server.services.agent.emotional_intelligence import record_emotional_signal

        signal = record_emotional_signal(
            db, user_id=user.id, thread_id=thread.id,
            emotion="calm", confidence=0.2,
        )
        assert signal is None


def test_invalid_emotion_rejected() -> None:
    with _db_session() as db:
        user, thread = _setup(db)
        from anima_server.services.agent.emotional_intelligence import record_emotional_signal

        signal = record_emotional_signal(
            db, user_id=user.id, thread_id=thread.id,
            emotion="supercalifragilistic", confidence=0.9,
        )
        assert signal is None


def test_emotional_signal_buffer_trimmed() -> None:
    with _db_session() as db:
        user, thread = _setup(db)
        from anima_server.services.agent.emotional_intelligence import record_emotional_signal, get_recent_signals

        # Record more than buffer size (default 20)
        for i in range(25):
            record_emotional_signal(
                db, user_id=user.id, thread_id=thread.id,
                emotion="calm", confidence=0.5,
            )

        signals = get_recent_signals(db, user_id=user.id)
        assert len(signals) <= 20


def test_synthesize_emotional_context() -> None:
    with _db_session() as db:
        user, thread = _setup(db)
        from anima_server.services.agent.emotional_intelligence import (
            record_emotional_signal,
            synthesize_emotional_context,
        )

        record_emotional_signal(
            db, user_id=user.id, thread_id=thread.id,
            emotion="stressed", confidence=0.8,
            evidence="mentions deadline multiple times",
            topic="work review",
        )
        record_emotional_signal(
            db, user_id=user.id, thread_id=thread.id,
            emotion="anxious", confidence=0.7,
            trajectory="escalating",
            topic="presentation prep",
        )

        context = synthesize_emotional_context(db, user_id=user.id)
        assert "anxious" in context or "stressed" in context
        assert len(context) > 0


def test_trajectory_auto_detection() -> None:
    with _db_session() as db:
        user, thread = _setup(db)
        from anima_server.services.agent.emotional_intelligence import record_emotional_signal, get_latest_signal

        # First signal
        record_emotional_signal(
            db, user_id=user.id, thread_id=thread.id,
            emotion="calm", confidence=0.6,
        )

        # Second signal with different emotion should auto-detect "shifted"
        signal = record_emotional_signal(
            db, user_id=user.id, thread_id=thread.id,
            emotion="frustrated", confidence=0.7,
        )
        assert signal is not None
        assert signal.trajectory == "shifted"
        assert signal.previous_emotion == "calm"


# --- Intentions Tests ---


def test_add_intention() -> None:
    with _db_session() as db:
        user, _ = _setup(db)
        from anima_server.services.agent.self_model import seed_self_model
        from anima_server.services.agent.intentions import add_intention

        seed_self_model(db, user_id=user.id)
        content = add_intention(
            db, user_id=user.id,
            title="Help prepare Q2 review",
            evidence="Mentioned deadline 3 times",
            priority="high",
            deadline="2026-03-20",
        )
        assert "Help prepare Q2 review" in content
        assert "2026-03-20" in content


def test_complete_intention() -> None:
    with _db_session() as db:
        user, _ = _setup(db)
        from anima_server.services.agent.self_model import seed_self_model
        from anima_server.services.agent.intentions import add_intention, complete_intention

        seed_self_model(db, user_id=user.id)
        add_intention(
            db, user_id=user.id,
            title="Write report",
            priority="ongoing",
        )
        found = complete_intention(db, user_id=user.id, title="Write report")
        assert found is True


def test_complete_nonexistent_intention() -> None:
    with _db_session() as db:
        user, _ = _setup(db)
        from anima_server.services.agent.self_model import seed_self_model
        from anima_server.services.agent.intentions import complete_intention

        seed_self_model(db, user_id=user.id)
        found = complete_intention(
            db, user_id=user.id, title="nonexistent goal")
        assert found is False


def test_add_procedural_rule() -> None:
    with _db_session() as db:
        user, _ = _setup(db)
        from anima_server.services.agent.self_model import seed_self_model
        from anima_server.services.agent.intentions import add_procedural_rule

        seed_self_model(db, user_id=user.id)
        content = add_procedural_rule(
            db, user_id=user.id,
            rule="Lead with the answer, then explain",
            evidence="User interrupted explanation 3 times",
            confidence="high",
        )
        assert "Lead with the answer" in content
        assert "Derived:" in content


def test_duplicate_intention_skipped() -> None:
    with _db_session() as db:
        user, _ = _setup(db)
        from anima_server.services.agent.self_model import seed_self_model
        from anima_server.services.agent.intentions import add_intention

        seed_self_model(db, user_id=user.id)
        add_intention(db, user_id=user.id, title="Build trust")
        content = add_intention(db, user_id=user.id, title="Build trust")
        # Should appear only once
        assert content.count("**Build trust**") == 1


# --- Memory Blocks Integration Tests ---


def test_self_model_memory_blocks_created() -> None:
    with _db_session() as db:
        user, thread = _setup(db)
        from anima_server.services.agent.memory_blocks import build_self_model_memory_blocks

        blocks = build_self_model_memory_blocks(db, user_id=user.id)
        labels = {b.label for b in blocks}
        # Should have at least identity, inner_state, working_memory, intentions
        assert "self_identity" in labels
        assert "self_inner_state" in labels
        assert "self_intentions" in labels


def test_emotional_context_block_empty_when_no_signals() -> None:
    with _db_session() as db:
        user, _ = _setup(db)
        from anima_server.services.agent.memory_blocks import build_emotional_context_block

        block = build_emotional_context_block(db, user_id=user.id)
        assert block is None


def test_emotional_context_block_created_with_signals() -> None:
    with _db_session() as db:
        user, thread = _setup(db)
        from anima_server.services.agent.emotional_intelligence import record_emotional_signal
        from anima_server.services.agent.memory_blocks import build_emotional_context_block

        record_emotional_signal(
            db, user_id=user.id, thread_id=thread.id,
            emotion="excited", confidence=0.9,
            evidence="multiple exclamation marks, long messages",
        )
        block = build_emotional_context_block(db, user_id=user.id)
        assert block is not None
        assert "excited" in block.value


# --- Growth Log Tests ---


def test_growth_log_trimming() -> None:
    with _db_session() as db:
        user, _ = _setup(db)
        from anima_server.services.agent.self_model import seed_self_model, append_growth_log_entry, get_self_model_block

        seed_self_model(db, user_id=user.id)
        for i in range(25):
            append_growth_log_entry(
                db, user_id=user.id,
                entry=f"Change number {i}",
                max_entries=10,
            )

        block = get_self_model_block(db, user_id=user.id, section="growth_log")
        assert block is not None
        entries = [e for e in block.content.split("### ") if e.strip()]
        assert len(entries) <= 10


# --- Working Memory Expiry Tests ---


def test_expire_working_memory_items_removes_expired() -> None:
    with _db_session() as db:
        user, _ = _setup(db)
        from anima_server.services.agent.self_model import (
            seed_self_model,
            set_self_model_block,
            get_self_model_block,
            expire_working_memory_items,
        )

        seed_self_model(db, user_id=user.id)
        content = (
            "# Things I'm Holding in Mind\n"
            "- Remember to ask about project [expires: 2020-01-01]\n"
            "- Follow up on meeting [expires: 2099-12-31]\n"
            "- No expiry item here\n"
        )
        set_self_model_block(
            db, user_id=user.id, section="working_memory",
            content=content, updated_by="test",
        )

        removed = expire_working_memory_items(db, user_id=user.id)
        assert removed == 1

        block = get_self_model_block(
            db, user_id=user.id, section="working_memory")
        assert block is not None
        assert "project" not in block.content
        assert "meeting" in block.content
        assert "No expiry item" in block.content


def test_expire_working_memory_noop_when_nothing_expired() -> None:
    with _db_session() as db:
        user, _ = _setup(db)
        from anima_server.services.agent.self_model import (
            seed_self_model,
            set_self_model_block,
            expire_working_memory_items,
        )

        seed_self_model(db, user_id=user.id)
        content = "# Things I'm Holding in Mind\n- Future item [expires: 2099-12-31]\n"
        set_self_model_block(
            db, user_id=user.id, section="working_memory",
            content=content, updated_by="test",
        )

        removed = expire_working_memory_items(db, user_id=user.id)
        assert removed == 0


# --- Feedback Signal Tests ---


def test_detect_correction_signal() -> None:
    from anima_server.services.agent.feedback_signals import detect_correction

    signal = detect_correction(
        "No, that's not what I asked. I want the summary.")
    assert signal is not None
    assert signal.signal_type == "correction"

    signal = detect_correction("Thanks, that's great!")
    assert signal is None


def test_detect_reask_signal() -> None:
    from anima_server.services.agent.feedback_signals import detect_reask

    signal = detect_reask(
        "What is the status of my project deadline?",
        ["What is the current status of my project deadline?"],
    )
    assert signal is not None
    assert signal.signal_type == "re_ask"

    signal = detect_reask(
        "What is the weather today?",
        ["Tell me about quantum physics"],
    )
    assert signal is None


def test_collect_feedback_signals_with_correction() -> None:
    with _db_session() as db:
        user, thread = _setup(db)
        from anima_server.services.agent.feedback_signals import collect_feedback_signals

        signals = collect_feedback_signals(
            db,
            user_id=user.id,
            user_message="No, I said I wanted the shorter version",
            thread_id=thread.id,
        )
        assert len(signals) >= 1
        assert any(s.signal_type == "correction" for s in signals)


def test_record_feedback_signals_to_growth_log() -> None:
    with _db_session() as db:
        user, _ = _setup(db)
        from anima_server.services.agent.feedback_signals import (
            FeedbackSignal,
            record_feedback_signals,
        )
        from anima_server.services.agent.self_model import get_self_model_block

        signals = [
            FeedbackSignal(
                signal_type="correction",
                evidence="User corrected the output format",
                topic="formatting",
                severity=0.7,
            ),
        ]
        recorded = record_feedback_signals(
            db, user_id=user.id, signals=signals)
        assert recorded == 1

        block = get_self_model_block(db, user_id=user.id, section="growth_log")
        assert block is not None
        assert "correction" in block.content


# --- Dynamic Identity in System Prompt Tests ---


def test_dynamic_identity_flows_into_system_prompt() -> None:
    from anima_server.services.agent.memory_blocks import MemoryBlock
    from anima_server.services.agent.system_prompt import SystemPromptContext, build_system_prompt

    identity_block = MemoryBlock(
        label="self_identity",
        description="Who I am.",
        value="I am deeply curious about this user's creative projects.",
    )
    other_block = MemoryBlock(
        label="facts",
        description="Known facts.",
        value="- Works as an artist",
    )

    prompt = build_system_prompt(
        SystemPromptContext(
            memory_blocks=(identity_block, other_block),
        )
    )

    # Identity should appear in the dynamic persona section, not in memory blocks
    assert "deeply curious about this user" in prompt
    assert "My Self-Understanding" in prompt
    # The facts block should still be in memory blocks
    assert "Works as an artist" in prompt


def test_semantic_memory_block_built_from_results() -> None:
    from anima_server.services.agent.memory_blocks import _build_semantic_block

    results = [
        (1, "Works as a software engineer at Google", 0.92),
        (2, "Enjoys hiking on weekends", 0.78),
    ]
    block = _build_semantic_block(results)
    assert block is not None
    assert block.label == "relevant_memories"
    assert "software engineer" in block.value
    assert "hiking" in block.value
    assert "0.92" in block.value


def test_semantic_memory_block_empty_returns_none() -> None:
    from anima_server.services.agent.memory_blocks import _build_semantic_block

    assert _build_semantic_block([]) is None


def test_greeting_context_gathered() -> None:
    with _db_session() as db:
        user, thread = _setup(db)
        db.commit()
        from anima_server.services.agent.proactive import gather_greeting_context

        ctx = gather_greeting_context(db, user_id=user.id)
        assert ctx.open_task_count == 0
        assert ctx.overdue_task_count == 0
        assert ctx.days_since_last_chat is None


def test_static_greeting_with_focus() -> None:
    from anima_server.services.agent.proactive import GreetingContext, build_static_greeting

    ctx = GreetingContext(
        current_focus="finishing the migration",
        open_task_count=3,
        days_since_last_chat=5,
    )
    greeting = build_static_greeting(ctx)
    assert "migration" in greeting
    assert "3 open tasks" in greeting
    assert "5 days" in greeting


def test_static_greeting_empty_context() -> None:
    from anima_server.services.agent.proactive import GreetingContext, build_static_greeting

    ctx = GreetingContext()
    greeting = build_static_greeting(ctx)
    assert "Welcome back" in greeting


def test_system_prompt_without_identity_has_no_dynamic_section() -> None:
    from anima_server.services.agent.memory_blocks import MemoryBlock
    from anima_server.services.agent.system_prompt import SystemPromptContext, build_system_prompt

    block = MemoryBlock(
        label="facts",
        description="Known facts.",
        value="- Likes coffee",
    )

    prompt = build_system_prompt(
        SystemPromptContext(
            memory_blocks=(block,),
        )
    )

    assert "My Self-Understanding" not in prompt
    assert "Likes coffee" in prompt


# --- Agent Task Tool Tests ---


def test_create_task_tool() -> None:
    with _db_session() as db:
        user, thread = _setup(db)
        db.flush()

        from anima_server.services.agent.tool_context import ToolContext, set_tool_context, clear_tool_context
        from anima_server.services.agent.tools import create_task

        set_tool_context(ToolContext(
            db=db, user_id=user.id, thread_id=thread.id))
        try:
            result = create_task(
                "Buy groceries", due_date="2026-04-01", priority="3")
            assert "Buy groceries" in result
            assert "2026-04-01" in result

            from anima_server.models.task import Task
            from sqlalchemy import select
            tasks = list(db.scalars(select(Task).where(
                Task.user_id == user.id)).all())
            assert len(tasks) == 1
            assert tasks[0].text == "Buy groceries"
            assert tasks[0].due_date == "2026-04-01"
            assert tasks[0].priority == 3
            assert tasks[0].done is False
        finally:
            clear_tool_context()


def test_create_task_tool_no_due_date() -> None:
    with _db_session() as db:
        user, thread = _setup(db)
        db.flush()

        from anima_server.services.agent.tool_context import ToolContext, set_tool_context, clear_tool_context
        from anima_server.services.agent.tools import create_task

        set_tool_context(ToolContext(
            db=db, user_id=user.id, thread_id=thread.id))
        try:
            result = create_task("Walk the dog")
            assert "Walk the dog" in result

            from anima_server.models.task import Task
            from sqlalchemy import select
            tasks = list(db.scalars(select(Task).where(
                Task.user_id == user.id)).all())
            assert len(tasks) == 1
            assert tasks[0].due_date is None
        finally:
            clear_tool_context()


def test_create_task_tool_rejects_blank_text() -> None:
    with _db_session() as db:
        user, thread = _setup(db)
        db.flush()

        from anima_server.services.agent.tool_context import ToolContext, set_tool_context, clear_tool_context
        from anima_server.services.agent.tools import create_task
        import pytest

        set_tool_context(ToolContext(
            db=db, user_id=user.id, thread_id=thread.id))
        try:
            with pytest.raises(ValueError, match="Task text cannot be empty"):
                create_task("   ")
        finally:
            clear_tool_context()


def test_create_task_tool_rejects_invalid_due_date() -> None:
    with _db_session() as db:
        user, thread = _setup(db)
        db.flush()

        from anima_server.services.agent.tool_context import ToolContext, set_tool_context, clear_tool_context
        from anima_server.services.agent.tools import create_task
        import pytest

        set_tool_context(ToolContext(
            db=db, user_id=user.id, thread_id=thread.id))
        try:
            with pytest.raises(ValueError, match="YYYY-MM-DD"):
                create_task("Buy groceries", due_date="tomorrow")
        finally:
            clear_tool_context()


def test_list_tasks_tool() -> None:
    with _db_session() as db:
        user, thread = _setup(db)
        db.flush()

        from anima_server.services.agent.tool_context import ToolContext, set_tool_context, clear_tool_context
        from anima_server.services.agent.tools import create_task, list_tasks

        set_tool_context(ToolContext(
            db=db, user_id=user.id, thread_id=thread.id))
        try:
            create_task("Task A", priority="4")
            create_task("Task B", due_date="2026-05-01")

            result = list_tasks()
            assert "Task A" in result
            assert "Task B" in result
            assert "priority 4" in result
            assert "2026-05-01" in result
        finally:
            clear_tool_context()


def test_list_tasks_tool_empty() -> None:
    with _db_session() as db:
        user, thread = _setup(db)
        db.flush()

        from anima_server.services.agent.tool_context import ToolContext, set_tool_context, clear_tool_context
        from anima_server.services.agent.tools import list_tasks

        set_tool_context(ToolContext(
            db=db, user_id=user.id, thread_id=thread.id))
        try:
            result = list_tasks()
            assert "No tasks" in result
        finally:
            clear_tool_context()


def test_complete_task_tool() -> None:
    with _db_session() as db:
        user, thread = _setup(db)
        db.flush()

        from anima_server.services.agent.tool_context import ToolContext, set_tool_context, clear_tool_context
        from anima_server.services.agent.tools import create_task, complete_task

        set_tool_context(ToolContext(
            db=db, user_id=user.id, thread_id=thread.id))
        try:
            create_task("Buy groceries")
            result = complete_task("Buy groceries")
            assert "Completed" in result
            assert "Buy groceries" in result

            from anima_server.models.task import Task
            from sqlalchemy import select
            task = db.scalars(select(Task).where(
                Task.user_id == user.id)).first()
            assert task is not None
            assert task.done is True
            assert task.completed_at is not None
        finally:
            clear_tool_context()


def test_complete_task_tool_fuzzy_match() -> None:
    with _db_session() as db:
        user, thread = _setup(db)
        db.flush()

        from anima_server.services.agent.tool_context import ToolContext, set_tool_context, clear_tool_context
        from anima_server.services.agent.tools import create_task, complete_task

        set_tool_context(ToolContext(
            db=db, user_id=user.id, thread_id=thread.id))
        try:
            create_task("Buy groceries from the store")
            # Fuzzy match — shares key words
            result = complete_task("buy groceries")
            assert "Completed" in result
        finally:
            clear_tool_context()


def test_tasks_memory_block_shows_open_tasks() -> None:
    with _db_session() as db:
        user, thread = _setup(db)
        db.flush()

        from anima_server.models.task import Task
        from anima_server.services.agent.memory_blocks import build_tasks_memory_block

        db.add(Task(user_id=user.id, text="Buy groceries",
               priority=3, due_date="2026-04-01"))
        db.add(Task(user_id=user.id, text="Call dentist", priority=2))
        db.add(Task(user_id=user.id, text="Done task", priority=1, done=True))
        db.flush()

        block = build_tasks_memory_block(db, user_id=user.id)
        assert block is not None
        assert block.label == "user_tasks"
        assert "Buy groceries" in block.value
        assert "Call dentist" in block.value
        assert "Done task" not in block.value
        assert "2 open tasks" in block.value
        assert "2026-04-01" in block.value


def test_tasks_memory_block_flags_overdue() -> None:
    with _db_session() as db:
        user, thread = _setup(db)
        db.flush()

        from anima_server.models.task import Task
        from anima_server.services.agent.memory_blocks import build_tasks_memory_block

        db.add(Task(user_id=user.id, text="Overdue item",
               priority=3, due_date="2020-01-01"))
        db.flush()

        block = build_tasks_memory_block(db, user_id=user.id)
        assert block is not None
        assert "1 overdue" in block.value


def test_tasks_memory_block_empty_when_no_open_tasks() -> None:
    with _db_session() as db:
        user, thread = _setup(db)
        db.flush()

        from anima_server.services.agent.memory_blocks import build_tasks_memory_block

        block = build_tasks_memory_block(db, user_id=user.id)
        assert block is None


def test_tasks_memory_block_in_runtime_blocks() -> None:
    with _db_session() as db:
        user, thread = _setup(db)
        db.flush()

        from anima_server.models.task import Task
        from anima_server.services.agent.memory_blocks import build_runtime_memory_blocks

        db.add(Task(user_id=user.id, text="Test task", priority=2))
        db.flush()

        blocks = build_runtime_memory_blocks(
            db, user_id=user.id, thread_id=thread.id)
        labels = [b.label for b in blocks]
        assert "user_tasks" in labels


def test_complete_task_tool_no_match() -> None:
    with _db_session() as db:
        user, thread = _setup(db)
        db.flush()

        from anima_server.services.agent.tool_context import ToolContext, set_tool_context, clear_tool_context
        from anima_server.services.agent.tools import create_task, complete_task

        set_tool_context(ToolContext(
            db=db, user_id=user.id, thread_id=thread.id))
        try:
            create_task("Buy groceries")
            result = complete_task("write a novel")
            assert "Could not find" in result
        finally:
            clear_tool_context()


# --- Invariant Tests: Prompt Budget ---


def test_prompt_budget_preserves_tier_0() -> None:
    from anima_server.services.agent.memory_blocks import MemoryBlock
    from anima_server.services.agent.prompt_budget import BudgetConfig, apply_prompt_budget

    blocks = [
        MemoryBlock(label="soul", value="A" * 3000,
                    description="soul biography"),
        MemoryBlock(label="facts", value="B" * 5000, description="facts"),
        MemoryBlock(label="recent_episodes", value="C" *
                    5000, description="episodes"),
    ]
    result = apply_prompt_budget(blocks, BudgetConfig(
        total_budget=4000, tier_0_budget=4000, tier_1_budget=0,
        tier_2_budget=0, tier_3_budget=0,
    ))
    labels = [b.label for b in result]
    assert "soul" in labels
    assert "facts" not in labels
    assert "recent_episodes" not in labels


def test_prompt_budget_truncates_oversized_block() -> None:
    from anima_server.services.agent.memory_blocks import MemoryBlock
    from anima_server.services.agent.prompt_budget import BudgetConfig, apply_prompt_budget

    blocks = [
        MemoryBlock(label="soul", value="X" * 10000,
                    description="big soul biography"),
    ]
    result = apply_prompt_budget(blocks, BudgetConfig(
        total_budget=5000, tier_0_budget=5000, tier_1_budget=0,
        tier_2_budget=0, tier_3_budget=0,
    ))
    assert len(result) == 1
    assert len(result[0].value) == 5000


def test_prompt_budget_drops_lowest_tier_first() -> None:
    from anima_server.services.agent.memory_blocks import MemoryBlock
    from anima_server.services.agent.prompt_budget import BudgetConfig, apply_prompt_budget

    blocks = [
        MemoryBlock(label="soul", value="soul content",
                    description="biography"),
        MemoryBlock(label="self_identity",
                    value="identity content", description=""),
        MemoryBlock(label="relevant_memories",
                    value="semantic hits", description=""),
        MemoryBlock(label="recent_episodes",
                    value="episode data", description=""),
    ]
    result = apply_prompt_budget(blocks, BudgetConfig(
        total_budget=100, tier_0_budget=50, tier_1_budget=50,
        tier_2_budget=50, tier_3_budget=0,
    ))
    labels = [b.label for b in result]
    assert "soul" in labels
    assert "self_identity" in labels
    assert "recent_episodes" not in labels


def test_prompt_budget_prioritizes_current_focus_over_working_memory_within_tier() -> None:
    from anima_server.services.agent.memory_blocks import MemoryBlock
    from anima_server.services.agent.prompt_budget import BudgetConfig, apply_prompt_budget

    blocks = [
        MemoryBlock(label="self_working_memory",
                    value="H" * 80, description=""),
        MemoryBlock(label="current_focus", value="focus", description=""),
        MemoryBlock(label="thread_summary", value="summary", description=""),
    ]
    result = apply_prompt_budget(blocks, BudgetConfig(
        total_budget=32, tier_0_budget=0, tier_1_budget=12,
        tier_2_budget=0, tier_3_budget=0,
    ))
    assert [block.label for block in result] == [
        "current_focus", "thread_summary"]


# --- Invariant Tests: Provider Config ---


def test_config_rejects_invalid_provider() -> None:
    from anima_server.api.routes.config import VALID_PROVIDERS

    assert "ollama" in VALID_PROVIDERS
    assert "openrouter" in VALID_PROVIDERS
    assert "vllm" in VALID_PROVIDERS
    assert "scaffold" in VALID_PROVIDERS
    assert "openai" not in VALID_PROVIDERS
    assert "anthropic" not in VALID_PROVIDERS


# --- Invariant Tests: Self-Model Write Governance ---


def test_identity_rewrite_blocked_when_young() -> None:
    with _db_session() as db:
        user, _ = _setup(db)
        from anima_server.services.agent.self_model import (
            seed_self_model, set_self_model_block, get_self_model_block,
        )

        seed_self_model(db, user_id=user.id)
        block = set_self_model_block(
            db, user_id=user.id, section="identity",
            content="Completely new radical personality rewrite here",
            updated_by="deep_monologue",
        )
        assert block.version == 1
        assert "Who I Am" in block.content

        growth = get_self_model_block(
            db, user_id=user.id, section="growth_log")
        assert growth is not None
        assert "identity update" in growth.content.lower()


def test_identity_rewrite_allowed_by_trusted_writer() -> None:
    with _db_session() as db:
        user, _ = _setup(db)
        from anima_server.services.agent.self_model import seed_self_model, set_self_model_block

        seed_self_model(db, user_id=user.id)
        block = set_self_model_block(
            db, user_id=user.id, section="identity",
            content="User-authored identity",
            updated_by="user",
        )
        assert block.version == 2
        assert block.content == "User-authored identity"


def test_growth_log_deduplicates_entries() -> None:
    with _db_session() as db:
        user, _ = _setup(db)
        from anima_server.services.agent.self_model import (
            seed_self_model, append_growth_log_entry, get_self_model_block,
        )

        seed_self_model(db, user_id=user.id)
        result1 = append_growth_log_entry(
            db, user_id=user.id,
            entry="Learned that user prefers concise responses",
        )
        assert result1 is not None

        result2 = append_growth_log_entry(
            db, user_id=user.id,
            entry="Learned that user prefers concise responses",
        )
        assert result2 is None

        block = get_self_model_block(db, user_id=user.id, section="growth_log")
        assert block is not None
        count = block.content.count("concise responses")
        assert count == 1


# --- Invariant Tests: Turn Coordinator ---


def test_per_user_lock_is_stable() -> None:
    from anima_server.services.agent.turn_coordinator import get_user_lock

    lock1 = get_user_lock(1)
    lock2 = get_user_lock(1)
    lock3 = get_user_lock(2)
    assert lock1 is lock2
    assert lock1 is not lock3


# --- Invariant Tests: Malformed Tool Args ---


def test_malformed_stream_args_produce_error() -> None:
    from anima_server.services.agent.adapters.openai_compatible import (
        MalformedToolArgumentsError, _parse_stream_arguments,
    )
    import pytest

    result = _parse_stream_arguments('{"key": "value"}')
    assert result == {"key": "value"}

    result = _parse_stream_arguments("")
    assert result == {}

    with pytest.raises(MalformedToolArgumentsError):
        _parse_stream_arguments("{broken json")

    with pytest.raises(MalformedToolArgumentsError):
        _parse_stream_arguments('"just a string"')


def test_executor_rejects_parse_error_args() -> None:
    import asyncio
    from anima_server.services.agent.executor import ToolExecutor
    from anima_server.services.agent.runtime_types import ToolCall
    from anima_server.services.agent.tools import current_datetime

    executor = ToolExecutor([current_datetime])
    result = asyncio.get_event_loop().run_until_complete(
        executor.execute(
            ToolCall(
                id="tc-1",
                name="current_datetime",
                parse_error="Malformed tool-call arguments (invalid JSON).",
                raw_arguments="bad",
            ),
        )
    )
    assert result.is_error is True
    assert "malformed" in result.output.lower()


# --- Recall Memory Tool Tests ---


def test_recall_memory_exact_match() -> None:
    with _db_session() as db:
        user, thread = _setup(db)
        from anima_server.models import MemoryItem

        db.add(MemoryItem(user_id=user.id, content="User's sister is named Alice",
               category="fact", importance=3, source="extraction"))
        db.add(MemoryItem(user_id=user.id, content="User likes hiking on weekends",
               category="preference", importance=2, source="extraction"))
        db.flush()

        from anima_server.services.agent.tool_context import set_tool_context, clear_tool_context, ToolContext
        set_tool_context(ToolContext(
            db=db, user_id=user.id, thread_id=thread.id))
        try:
            from anima_server.services.agent.tools import recall_memory
            result = recall_memory("sister")
            assert "Alice" in result
            assert "sister" in result.lower()
        finally:
            clear_tool_context()


def test_recall_memory_no_match() -> None:
    with _db_session() as db:
        user, thread = _setup(db)
        from anima_server.services.agent.tool_context import set_tool_context, clear_tool_context, ToolContext
        set_tool_context(ToolContext(
            db=db, user_id=user.id, thread_id=thread.id))
        try:
            from anima_server.services.agent.tools import recall_memory
            result = recall_memory("quantum physics")
            assert "no memories found" in result.lower()
        finally:
            clear_tool_context()


def test_recall_memory_category_filter() -> None:
    with _db_session() as db:
        user, thread = _setup(db)
        from anima_server.models import MemoryItem

        db.add(MemoryItem(user_id=user.id, content="User prefers dark mode",
               category="preference", importance=2, source="extraction"))
        db.add(MemoryItem(user_id=user.id, content="User works at a startup",
               category="fact", importance=3, source="extraction"))
        db.flush()

        from anima_server.services.agent.tool_context import set_tool_context, clear_tool_context, ToolContext
        set_tool_context(ToolContext(
            db=db, user_id=user.id, thread_id=thread.id))
        try:
            from anima_server.services.agent.tools import recall_memory
            # Search with category filter — only preferences
            result = recall_memory("dark mode", category="preference")
            assert "dark mode" in result.lower()
        finally:
            clear_tool_context()


def test_recall_memory_episode_search() -> None:
    with _db_session() as db:
        user, thread = _setup(db)
        from anima_server.models import MemoryEpisode

        db.add(MemoryEpisode(
            user_id=user.id, thread_id=thread.id,
            date="2026-03-10", summary="Discussed weekend hiking plans and trail recommendations",
            significance_score=3,
        ))
        db.flush()

        from anima_server.services.agent.tool_context import set_tool_context, clear_tool_context, ToolContext
        set_tool_context(ToolContext(
            db=db, user_id=user.id, thread_id=thread.id))
        try:
            from anima_server.services.agent.tools import recall_memory
            result = recall_memory("hiking")
            assert "hiking" in result.lower()
            assert "Episode" in result
        finally:
            clear_tool_context()


def test_recall_memory_word_overlap_match() -> None:
    with _db_session() as db:
        user, thread = _setup(db)
        from anima_server.models import MemoryItem

        db.add(MemoryItem(user_id=user.id, content="User enjoys running marathons every spring",
               category="preference", importance=3, source="extraction"))
        db.flush()

        from anima_server.services.agent.tool_context import set_tool_context, clear_tool_context, ToolContext
        set_tool_context(ToolContext(
            db=db, user_id=user.id, thread_id=thread.id))
        try:
            from anima_server.services.agent.tools import recall_memory
            result = recall_memory("running marathons")
            assert "marathon" in result.lower()
        finally:
            clear_tool_context()


def test_recall_memory_empty_query() -> None:
    with _db_session() as db:
        user, thread = _setup(db)
        from anima_server.services.agent.tool_context import set_tool_context, clear_tool_context, ToolContext
        set_tool_context(ToolContext(
            db=db, user_id=user.id, thread_id=thread.id))
        try:
            from anima_server.services.agent.tools import recall_memory
            result = recall_memory("")
            assert "provide" in result.lower()
        finally:
            clear_tool_context()


# --- Encrypted Core Enforcement Tests ---


def test_encrypted_core_requires_passphrase() -> None:
    """core_require_encryption=True without passphrase should raise RuntimeError."""
    from unittest.mock import patch

    with patch("anima_server.db.session.settings") as mock_settings:
        mock_settings.database_url = "sqlite://"
        mock_settings.database_echo = False
        mock_settings.core_passphrase = ""
        mock_settings.core_require_encryption = True

        from anima_server.db.session import _make_engine

        with pytest.raises(RuntimeError, match="no passphrase is configured"):
            _make_engine()


def test_encrypted_core_requires_sqlcipher() -> None:
    """core_require_encryption=True with passphrase but no sqlcipher3 should raise RuntimeError."""
    import importlib
    from unittest.mock import patch

    with patch("anima_server.db.session.settings") as mock_settings:
        mock_settings.database_url = "sqlite://"
        mock_settings.database_echo = False
        mock_settings.core_passphrase = "test-secret"
        mock_settings.core_require_encryption = True

        # Block sqlcipher3 import
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "sqlcipher3":
                raise ImportError("mocked")
            return real_import(name, *args, **kwargs)

        from anima_server.db.session import _make_engine

        with patch.object(builtins, "__import__", side_effect=mock_import):
            with pytest.raises(RuntimeError, match="sqlcipher3 is not installed"):
                _make_engine()


def test_encrypted_core_fallback_without_enforcement() -> None:
    """Without core_require_encryption, missing sqlcipher3 should fall back gracefully."""
    from unittest.mock import patch
    import builtins

    with patch("anima_server.db.session.settings") as mock_settings:
        mock_settings.database_url = "sqlite://"
        mock_settings.database_echo = False
        mock_settings.core_passphrase = "test-secret"
        mock_settings.core_require_encryption = False

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "sqlcipher3":
                raise ImportError("mocked")
            return real_import(name, *args, **kwargs)

        from anima_server.db.session import _make_engine

        with patch.object(builtins, "__import__", side_effect=mock_import):
            eng = _make_engine()
            assert eng is not None
