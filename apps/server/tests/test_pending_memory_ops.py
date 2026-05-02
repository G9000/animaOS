from __future__ import annotations

import ast
import asyncio
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from anima_server.db.base import Base
from anima_server.models import User
from anima_server.models.consciousness import SelfModelBlock
from anima_server.services.agent.tool_context import (
    ToolContext,
    clear_tool_context,
    set_tool_context,
)
from conftest_runtime import runtime_db_session
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@contextmanager
def _soul_db_session() -> Generator[Session, None, None]:
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


def test_create_pending_op_append() -> None:
    with runtime_db_session() as runtime_db:
        from anima_server.services.agent.pending_ops import create_pending_op, get_pending_ops

        op = create_pending_op(
            runtime_db,
            user_id=7,
            op_type="append",
            target_block="human",
            content="Likes green tea",
            old_content=None,
            source_run_id=11,
            source_tool_call_id="tc-1",
        )
        runtime_db.flush()

        pending = get_pending_ops(runtime_db, user_id=7)

    assert op.id is not None
    assert len(pending) == 1
    assert pending[0].op_type == "append"
    assert pending[0].target_block == "human"
    assert pending[0].content == "Likes green tea"
    assert pending[0].old_content is None
    assert pending[0].source_run_id == 11
    assert pending[0].source_tool_call_id == "tc-1"


def test_get_pending_ops_ordered_and_excludes_terminal_rows() -> None:
    with runtime_db_session() as runtime_db:
        from anima_server.models import PendingMemoryOp
        from anima_server.services.agent.pending_ops import get_pending_ops

        runtime_db.add_all(
            [
                PendingMemoryOp(
                    user_id=1,
                    op_type="append",
                    target_block="human",
                    content="first",
                    source_tool_call_id="tc-1",
                ),
                PendingMemoryOp(
                    user_id=1,
                    op_type="append",
                    target_block="human",
                    content="skip-consolidated",
                    consolidated=True,
                    source_tool_call_id="tc-2",
                ),
                PendingMemoryOp(
                    user_id=1,
                    op_type="append",
                    target_block="human",
                    content="second",
                    source_tool_call_id="tc-3",
                ),
                PendingMemoryOp(
                    user_id=1,
                    op_type="append",
                    target_block="human",
                    content="skip-failed",
                    failed=True,
                    failure_reason="boom",
                    source_tool_call_id="tc-4",
                ),
            ]
        )
        runtime_db.flush()

        pending = get_pending_ops(runtime_db, user_id=1)

    assert [op.content for op in pending] == ["first", "second"]
    assert [op.source_tool_call_id for op in pending] == ["tc-1", "tc-3"]


def test_core_memory_append_creates_pending_op_without_writing_soul() -> None:
    with _soul_db_session() as soul_db, runtime_db_session() as runtime_db:
        from anima_server.models import PendingMemoryOp
        from anima_server.services.agent.tools import core_memory_append

        user = User(username="append-pending", password_hash="x",
                    display_name="Append Pending")
        soul_db.add(user)
        soul_db.flush()
        soul_db.add(
            SelfModelBlock(
                user_id=user.id,
                section="human",
                content="Name: Alice",
                version=1,
                updated_by="seed",
            )
        )
        soul_db.flush()

        ctx = ToolContext(
            db=soul_db,
            runtime_db=runtime_db,
            user_id=user.id,
            thread_id=1,
            run_id=22,
            current_tool_call_id="tool-append-1",
        )
        set_tool_context(ctx)
        try:
            result = core_memory_append("human", "Has a dog named Biscuit.")
            runtime_db.flush()
        finally:
            clear_tool_context()

        soul_block = soul_db.scalar(
            select(SelfModelBlock).where(
                SelfModelBlock.user_id == user.id,
                SelfModelBlock.section == "human",
            )
        )
        pending = runtime_db.scalars(select(PendingMemoryOp)).all()

    assert result == "Appended to human memory. It will be visible in your next step."
    assert ctx.memory_modified is True
    assert soul_block is not None
    assert soul_block.content == "Name: Alice"
    assert len(pending) == 1
    assert pending[0].op_type == "append"
    assert pending[0].target_block == "human"
    assert pending[0].content == "Has a dog named Biscuit."
    assert pending[0].source_run_id == 22
    assert pending[0].source_tool_call_id == "tool-append-1"


def test_core_memory_append_invalid_label_explains_runtime_block_misuse() -> None:
    from anima_server.services.agent.tools import core_memory_append

    result = core_memory_append(
        "self_working_memory", "Remember this for later.")

    assert "Invalid label 'self_working_memory'." in result
    assert "runtime block label" in result
    assert "note_to_self" in result
    assert "save_to_memory" in result


def test_core_memory_replace_validates_against_merged_view() -> None:
    with _soul_db_session() as soul_db, runtime_db_session() as runtime_db:
        from anima_server.models import PendingMemoryOp
        from anima_server.services.agent.pending_ops import create_pending_op
        from anima_server.services.agent.tools import core_memory_replace

        user = User(username="replace-merged", password_hash="x",
                    display_name="Replace Merged")
        soul_db.add(user)
        soul_db.flush()
        soul_db.add(
            SelfModelBlock(
                user_id=user.id,
                section="human",
                content="Name: Alice",
                version=1,
                updated_by="seed",
            )
        )
        soul_db.flush()

        create_pending_op(
            runtime_db,
            user_id=user.id,
            op_type="append",
            target_block="human",
            content="Works at Google",
            old_content=None,
            source_run_id=30,
            source_tool_call_id="append-1",
        )
        runtime_db.flush()

        ctx = ToolContext(
            db=soul_db,
            runtime_db=runtime_db,
            user_id=user.id,
            thread_id=1,
            run_id=31,
            current_tool_call_id="replace-1",
        )
        set_tool_context(ctx)
        try:
            result = core_memory_replace(
                "human",
                "Works at Google",
                "Works at Apple (switched March 2026)",
            )
            runtime_db.flush()
        finally:
            clear_tool_context()

        pending = runtime_db.scalars(
            select(PendingMemoryOp).order_by(PendingMemoryOp.id.asc())
        ).all()

    assert result == "Replaced text in human memory. It will be visible in your next step."
    assert len(pending) == 2
    assert pending[1].op_type == "replace"
    assert pending[1].old_content == "Works at Google"
    assert pending[1].content == "Works at Apple (switched March 2026)"
    assert pending[1].source_run_id == 31
    assert pending[1].source_tool_call_id == "replace-1"


def test_core_memory_replace_guides_when_old_text_is_missing() -> None:
    with _soul_db_session() as soul_db, runtime_db_session() as runtime_db:
        from anima_server.services.agent.tools import core_memory_replace

        user = User(username="replace-missing", password_hash="x",
                    display_name="Replace Missing")
        soul_db.add(user)
        soul_db.flush()
        soul_db.add(
            SelfModelBlock(
                user_id=user.id,
                section="human",
                content="Name: Alice",
                version=1,
                updated_by="seed",
            )
        )
        soul_db.flush()

        ctx = ToolContext(
            db=soul_db,
            runtime_db=runtime_db,
            user_id=user.id,
            thread_id=1,
            run_id=45,
            current_tool_call_id="replace-missing-1",
        )
        set_tool_context(ctx)
        try:
            result = core_memory_replace(
                "human",
                "Works at Google",
                "Works at Apple",
            )
        finally:
            clear_tool_context()

    assert "Could not find the exact text to replace in human memory." in result
    assert "core_memory_append" in result
    assert "update_human_memory" in result


def test_read_core_memory_returns_merged_block_content() -> None:
    with _soul_db_session() as soul_db, runtime_db_session() as runtime_db:
        from anima_server.services.agent.pending_ops import create_pending_op
        from anima_server.services.agent.tools import read_core_memory

        user = User(username="read-core-memory", password_hash="x",
                    display_name="Read Core Memory")
        soul_db.add(user)
        soul_db.flush()
        soul_db.add(
            SelfModelBlock(
                user_id=user.id,
                section="human",
                content="Name: Alice",
                version=1,
                updated_by="seed",
            )
        )
        soul_db.flush()

        create_pending_op(
            runtime_db,
            user_id=user.id,
            op_type="append",
            target_block="human",
            content="Has a dog named Biscuit.",
            old_content=None,
            source_run_id=51,
            source_tool_call_id="read-core-memory-1",
        )
        runtime_db.flush()

        ctx = ToolContext(
            db=soul_db,
            runtime_db=runtime_db,
            user_id=user.id,
            thread_id=1,
            run_id=52,
            current_tool_call_id="read-core-memory-2",
        )
        set_tool_context(ctx)
        try:
            result = read_core_memory("human")
        finally:
            clear_tool_context()

    assert result == "Name: Alice\nHas a dog named Biscuit."


def test_list_pending_memory_ops_formats_core_queue() -> None:
    with _soul_db_session() as soul_db, runtime_db_session() as runtime_db:
        from anima_server.services.agent.pending_ops import create_pending_op
        from anima_server.services.agent.tools import list_pending_memory_ops

        user = User(username="list-pending-memory", password_hash="x",
                    display_name="List Pending Memory")
        soul_db.add(user)
        soul_db.flush()

        create_pending_op(
            runtime_db,
            user_id=user.id,
            op_type="append",
            target_block="human",
            content="Has a dog named Biscuit.",
            old_content=None,
            source_run_id=60,
            source_tool_call_id="pending-1",
        )
        create_pending_op(
            runtime_db,
            user_id=user.id,
            op_type="replace",
            target_block="persona",
            content="Speak more directly.",
            old_content="Speak softly.",
            source_run_id=61,
            source_tool_call_id="pending-2",
        )
        runtime_db.flush()

        ctx = ToolContext(
            db=soul_db,
            runtime_db=runtime_db,
            user_id=user.id,
            thread_id=1,
            run_id=62,
            current_tool_call_id="pending-3",
        )
        set_tool_context(ctx)
        try:
            result = list_pending_memory_ops()
        finally:
            clear_tool_context()

    assert "Pending core-memory ops (2):" in result
    assert "[human] append: Has a dog named Biscuit." in result
    assert "[persona] replace: Speak softly. -> Speak more directly." in result


def test_set_user_timezone_updates_world_section() -> None:
    with _soul_db_session() as soul_db, runtime_db_session() as runtime_db:
        from anima_server.models import PendingMemoryOp
        from anima_server.services.agent.memory_blocks import build_world_context_block
        from anima_server.services.agent.tools import set_user_timezone

        user = User(username="set-user-timezone", password_hash="x",
                    display_name="Set User Timezone")
        soul_db.add(user)
        soul_db.flush()
        soul_db.add(
            SelfModelBlock(
                user_id=user.id,
                section="human",
                content="Name: Alice",
                version=1,
                updated_by="seed",
            )
        )
        soul_db.flush()

        ctx = ToolContext(
            db=soul_db,
            runtime_db=runtime_db,
            user_id=user.id,
            thread_id=1,
            run_id=70,
            current_tool_call_id="timezone-1",
        )
        set_tool_context(ctx)
        try:
            result = set_user_timezone("Asia/Kuala_Lumpur")
            runtime_db.flush()
        finally:
            clear_tool_context()

        pending = runtime_db.scalars(select(PendingMemoryOp)).all()
        world_block = build_world_context_block(soul_db, user_id=user.id)

    assert "Saved user timezone as Asia/Kuala_Lumpur." in result
    assert "+08:00" in result
    assert world_block is not None
    assert world_block.value == "Timezone: Asia/Kuala_Lumpur"
    assert pending == []


def test_get_user_timezone_and_current_datetime_use_saved_timezone() -> None:
    with _soul_db_session() as soul_db, runtime_db_session() as runtime_db:
        from anima_server.services.agent.tools import current_datetime, get_user_timezone

        user = User(username="get-user-timezone", password_hash="x",
                    display_name="Get User Timezone")
        soul_db.add(user)
        soul_db.flush()
        soul_db.add(
            SelfModelBlock(
                user_id=user.id,
                section="world",
                content="Timezone: Asia/Kuala_Lumpur",
                version=1,
                updated_by="seed",
            )
        )
        soul_db.flush()

        ctx = ToolContext(
            db=soul_db,
            runtime_db=runtime_db,
            user_id=user.id,
            thread_id=1,
            run_id=71,
            current_tool_call_id="timezone-2",
        )
        set_tool_context(ctx)
        try:
            timezone_result = get_user_timezone()
            datetime_result = current_datetime()
        finally:
            clear_tool_context()

    assert "Saved user timezone (Asia/Kuala_Lumpur):" in timezone_result
    assert "+08:00" in timezone_result
    assert "Saved user timezone (Asia/Kuala_Lumpur):" in datetime_result
    assert "+08:00" in datetime_result
    assert "UTC:" in datetime_result


def test_set_user_timezone_rejects_invalid_values() -> None:
    with _soul_db_session() as soul_db, runtime_db_session() as runtime_db:
        from anima_server.services.agent.tools import set_user_timezone

        user = User(username="invalid-user-timezone", password_hash="x",
                    display_name="Invalid User Timezone")
        soul_db.add(user)
        soul_db.flush()

        ctx = ToolContext(
            db=soul_db,
            runtime_db=runtime_db,
            user_id=user.id,
            thread_id=1,
            run_id=72,
            current_tool_call_id="timezone-3",
        )
        set_tool_context(ctx)
        try:
            with pytest.raises(ValueError, match="Invalid timezone"):
                set_user_timezone("Mars/Olympus_Mons")
        finally:
            clear_tool_context()


def test_current_datetime_falls_back_when_saved_timezone_is_invalid() -> None:
    with _soul_db_session() as soul_db, runtime_db_session() as runtime_db:
        from anima_server.services.agent.tools import current_datetime

        user = User(username="invalid-stored-timezone", password_hash="x",
                    display_name="Invalid Stored Timezone")
        soul_db.add(user)
        soul_db.flush()
        soul_db.add(
            SelfModelBlock(
                user_id=user.id,
                section="world",
                content="Timezone: Mars/Olympus_Mons",
                version=1,
                updated_by="seed",
            )
        )
        soul_db.flush()

        ctx = ToolContext(
            db=soul_db,
            runtime_db=runtime_db,
            user_id=user.id,
            thread_id=1,
            run_id=73,
            current_tool_call_id="timezone-4",
        )
        set_tool_context(ctx)
        try:
            result = current_datetime()
        finally:
            clear_tool_context()

    assert "Local time:" in result
    assert "UTC:" in result


def test_world_context_block_is_separate_from_human_block() -> None:
    with _soul_db_session() as soul_db:
        from anima_server.services.agent.memory_blocks import (
            build_human_core_block,
            build_world_context_block,
        )

        user = User(
            username="timezone-human-block",
            password_hash="x",
            display_name="Alice",
        )
        soul_db.add(user)
        soul_db.flush()
        soul_db.add(
            SelfModelBlock(
                user_id=user.id,
                section="world",
                content="Timezone: Asia/Kuala_Lumpur\nLocale: en-MY",
                version=1,
                updated_by="seed",
            )
        )
        soul_db.add(
            SelfModelBlock(
                user_id=user.id,
                section="human",
                content="Likes tea.",
                version=1,
                updated_by="seed",
            )
        )
        soul_db.flush()

        world_block = build_world_context_block(soul_db, user_id=user.id)
        block = build_human_core_block(soul_db, user_id=user.id)

    assert world_block is not None
    assert "Timezone: Asia/Kuala_Lumpur" in world_block.value
    assert block is not None
    assert "Timezone:" not in block.value
    assert "Likes tea." in block.value
    assert "Name: Alice" in block.value


def test_consolidate_pending_memory_runs_soul_writer() -> None:
    with _soul_db_session() as soul_db, runtime_db_session() as runtime_db:
        from anima_server.services.agent.soul_writer import SoulWriterResult
        from anima_server.services.agent.tools import consolidate_pending_memory

        user = User(username="consolidate-pending", password_hash="x",
                    display_name="Consolidate Pending")
        soul_db.add(user)
        soul_db.flush()

        ctx = ToolContext(
            db=soul_db,
            runtime_db=runtime_db,
            user_id=user.id,
            thread_id=1,
            run_id=74,
            current_tool_call_id="consolidate-1",
        )
        set_tool_context(ctx)
        try:
            with patch(
                "anima_server.services.agent.soul_writer.run_soul_writer",
                new=AsyncMock(
                    return_value=SoulWriterResult(
                        ops_processed=2,
                        candidates_promoted=1,
                    )
                ),
                create=True,
            ):
                result = consolidate_pending_memory()
        finally:
            clear_tool_context()

    assert "Soul Writer finished." in result
    assert "Ops processed=2" in result
    assert "candidates promoted=1" in result


def test_update_human_memory_creates_full_replace_pending_op() -> None:
    with _soul_db_session() as soul_db, runtime_db_session() as runtime_db:
        from anima_server.models import PendingMemoryOp
        from anima_server.services.agent.tools import update_human_memory

        user = User(username="full-replace", password_hash="x",
                    display_name="Full Replace")
        soul_db.add(user)
        soul_db.flush()

        ctx = ToolContext(
            db=soul_db,
            runtime_db=runtime_db,
            user_id=user.id,
            thread_id=1,
            run_id=44,
            current_tool_call_id="full-replace-1",
        )
        set_tool_context(ctx)
        try:
            result = update_human_memory("Name: Alice\nWorks at Apple")
            runtime_db.flush()
        finally:
            clear_tool_context()

        op = runtime_db.scalar(select(PendingMemoryOp))

    assert result == "Human memory updated."
    assert op is not None
    assert op.op_type == "full_replace"
    assert op.target_block == "human"
    assert op.content == "Name: Alice\nWorks at Apple"
    assert op.old_content is None
    assert op.source_run_id == 44
    assert op.source_tool_call_id == "full-replace-1"


def test_build_merged_block_content_applies_ops_in_order() -> None:
    with _soul_db_session() as soul_db, runtime_db_session() as runtime_db:
        from anima_server.services.agent.memory_blocks import build_merged_block_content
        from anima_server.services.agent.pending_ops import create_pending_op

        user = User(username="merged-order", password_hash="x",
                    display_name="Merged Order")
        soul_db.add(user)
        soul_db.flush()
        soul_db.add(
            SelfModelBlock(
                user_id=user.id,
                section="human",
                content="Works at Google",
                version=1,
                updated_by="seed",
            )
        )
        soul_db.flush()

        create_pending_op(
            runtime_db,
            user_id=user.id,
            op_type="append",
            target_block="human",
            content="Lives in Kuala Lumpur",
            old_content=None,
            source_run_id=1,
            source_tool_call_id="tc-1",
        )
        create_pending_op(
            runtime_db,
            user_id=user.id,
            op_type="replace",
            target_block="human",
            content="Works at Apple",
            old_content="Works at Google",
            source_run_id=1,
            source_tool_call_id="tc-2",
        )
        create_pending_op(
            runtime_db,
            user_id=user.id,
            op_type="append",
            target_block="human",
            content="Has a dog named Biscuit",
            old_content=None,
            source_run_id=1,
            source_tool_call_id="tc-3",
        )
        runtime_db.flush()

        merged = build_merged_block_content(
            soul_db,
            runtime_db,
            user_id=user.id,
            section="human",
        )

    assert merged == "Works at Apple\nLives in Kuala Lumpur\nHas a dog named Biscuit"


def test_build_pending_ops_block_renders_orphaned_updates() -> None:
    with _soul_db_session() as soul_db, runtime_db_session() as runtime_db:
        from anima_server.services.agent.memory_blocks import build_pending_ops_block
        from anima_server.services.agent.pending_ops import create_pending_op

        user = User(username="pending-block", password_hash="x",
                    display_name="Pending Block")
        soul_db.add(user)
        soul_db.flush()

        create_pending_op(
            runtime_db,
            user_id=user.id,
            op_type="append",
            target_block="persona",
            content="I am warmer in late-night conversations.",
            old_content=None,
            source_run_id=1,
            source_tool_call_id="tc-1",
        )
        runtime_db.flush()

        block = build_pending_ops_block(soul_db, runtime_db, user_id=user.id)

    assert block is not None
    assert block.label == "pending_memory_updates"
    assert "persona" in block.value
    assert "late-night conversations" in block.value


def test_build_pending_ops_block_returns_none_when_empty() -> None:
    with _soul_db_session() as soul_db, runtime_db_session() as runtime_db:
        from anima_server.services.agent.memory_blocks import build_pending_ops_block

        user = User(username="pending-empty", password_hash="x",
                    display_name="Pending Empty")
        soul_db.add(user)
        soul_db.flush()

        block = build_pending_ops_block(soul_db, runtime_db, user_id=user.id)

    assert block is None


def test_runtime_memory_blocks_show_pending_writes_in_later_turns() -> None:
    with _soul_db_session() as soul_db, runtime_db_session() as runtime_db:
        from anima_server.services.agent.memory_blocks import build_runtime_memory_blocks
        from anima_server.services.agent.pending_ops import create_pending_op

        user = User(username="pending-future-turn",
                    password_hash="x", display_name="Future Turn")
        soul_db.add(user)
        soul_db.flush()
        soul_db.add(
            SelfModelBlock(
                user_id=user.id,
                section="human",
                content="Name: Alice",
                version=1,
                updated_by="seed",
            )
        )
        soul_db.flush()

        create_pending_op(
            runtime_db,
            user_id=user.id,
            op_type="append",
            target_block="human",
            content="Has a dog named Biscuit",
            old_content=None,
            source_run_id=50,
            source_tool_call_id="future-turn-1",
        )
        runtime_db.flush()

        blocks = build_runtime_memory_blocks(
            soul_db,
            user_id=user.id,
            thread_id=99,
            runtime_db=runtime_db,
        )

    human_block = next(block for block in blocks if block.label == "human")
    assert "Name: Alice" in human_block.value
    assert "Has a dog named Biscuit" in human_block.value


def test_consolidate_pending_ops_applies_ops_in_order_and_is_idempotent() -> None:
    with _soul_db_session() as soul_db, runtime_db_session() as runtime_db:
        from anima_server.models import PendingMemoryOp
        from anima_server.services.agent.consolidation import consolidate_pending_ops
        from anima_server.services.agent.pending_ops import create_pending_op

        user = User(
            username="consolidate-order",
            password_hash="x",
            display_name="Consolidate Order",
        )
        soul_db.add(user)
        soul_db.flush()
        soul_db.add(
            SelfModelBlock(
                user_id=user.id,
                section="human",
                content="Works at Google",
                version=1,
                updated_by="seed",
            )
        )
        soul_db.commit()

        create_pending_op(
            runtime_db,
            user_id=user.id,
            op_type="append",
            target_block="human",
            content="Lives in Kuala Lumpur",
            old_content=None,
            source_run_id=1,
            source_tool_call_id="tc-1",
        )
        create_pending_op(
            runtime_db,
            user_id=user.id,
            op_type="replace",
            target_block="human",
            content="Works at Apple",
            old_content="Works at Google",
            source_run_id=1,
            source_tool_call_id="tc-2",
        )
        create_pending_op(
            runtime_db,
            user_id=user.id,
            op_type="append",
            target_block="human",
            content="Has a dog named Biscuit",
            old_content=None,
            source_run_id=1,
            source_tool_call_id="tc-3",
        )
        runtime_db.commit()

        soul_factory = sessionmaker(
            bind=soul_db.get_bind(), autoflush=False, expire_on_commit=False)
        runtime_factory = sessionmaker(
            bind=runtime_db.get_bind(),
            autoflush=False,
            expire_on_commit=False,
        )

        asyncio.run(
            consolidate_pending_ops(
                user_id=user.id,
                soul_db_factory=soul_factory,
                runtime_db_factory=runtime_factory,
            )
        )
        asyncio.run(
            consolidate_pending_ops(
                user_id=user.id,
                soul_db_factory=soul_factory,
                runtime_db_factory=runtime_factory,
            )
        )

        final_block = soul_db.scalar(
            select(SelfModelBlock).where(
                SelfModelBlock.user_id == user.id,
                SelfModelBlock.section == "human",
            )
        )
        ops = runtime_db.scalars(
            select(PendingMemoryOp).order_by(PendingMemoryOp.id.asc())
        ).all()

    assert final_block is not None
    assert final_block.content == "Works at Apple\nLives in Kuala Lumpur\nHas a dog named Biscuit"
    assert [op.consolidated for op in ops] == [True, True, True]
    assert [op.failed for op in ops] == [False, False, False]


def test_consolidate_pending_ops_marks_failed_replace_and_continues() -> None:
    with _soul_db_session() as soul_db, runtime_db_session() as runtime_db:
        from anima_server.models import PendingMemoryOp
        from anima_server.services.agent.consolidation import consolidate_pending_ops
        from anima_server.services.agent.pending_ops import create_pending_op

        user = User(
            username="consolidate-failure",
            password_hash="x",
            display_name="Consolidate Failure",
        )
        soul_db.add(user)
        soul_db.flush()
        soul_db.add(
            SelfModelBlock(
                user_id=user.id,
                section="human",
                content="Name: Alice",
                version=1,
                updated_by="seed",
            )
        )
        soul_db.commit()

        create_pending_op(
            runtime_db,
            user_id=user.id,
            op_type="append",
            target_block="human",
            content="Has a dog named Biscuit",
            old_content=None,
            source_run_id=1,
            source_tool_call_id="tc-1",
        )
        create_pending_op(
            runtime_db,
            user_id=user.id,
            op_type="replace",
            target_block="human",
            content="Works at Apple",
            old_content="Works at Google",
            source_run_id=1,
            source_tool_call_id="tc-2",
        )
        create_pending_op(
            runtime_db,
            user_id=user.id,
            op_type="append",
            target_block="human",
            content="Likes green tea",
            old_content=None,
            source_run_id=1,
            source_tool_call_id="tc-3",
        )
        runtime_db.commit()

        soul_factory = sessionmaker(
            bind=soul_db.get_bind(), autoflush=False, expire_on_commit=False)
        runtime_factory = sessionmaker(
            bind=runtime_db.get_bind(),
            autoflush=False,
            expire_on_commit=False,
        )

        asyncio.run(
            consolidate_pending_ops(
                user_id=user.id,
                soul_db_factory=soul_factory,
                runtime_db_factory=runtime_factory,
            )
        )

        final_block = soul_db.scalar(
            select(SelfModelBlock).where(
                SelfModelBlock.user_id == user.id,
                SelfModelBlock.section == "human",
            )
        )
        ops = runtime_db.scalars(
            select(PendingMemoryOp).order_by(PendingMemoryOp.id.asc())
        ).all()

    assert final_block is not None
    assert final_block.content == "Name: Alice\nHas a dog named Biscuit\nLikes green tea"
    assert ops[0].consolidated is True
    assert ops[1].failed is True
    assert "old_content" in (ops[1].failure_reason or "")
    assert ops[2].consolidated is True


@pytest.mark.asyncio
async def test_run_agent_records_pending_op_traceability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.models import PendingMemoryOp
    from anima_server.models.runtime import RuntimeRun
    from anima_server.services.agent import service as agent_service
    from anima_server.services.agent.executor import ToolExecutor
    from anima_server.services.agent.runtime_types import StepTrace, ToolCall
    from anima_server.services.agent.state import AgentResult
    from anima_server.services.agent.tools import core_memory_append

    class RecordingRunner:
        def __init__(self) -> None:
            self.executor = ToolExecutor([core_memory_append])

        async def invoke(self, *args, **kwargs) -> AgentResult:
            del args, kwargs
            result = await self.executor.execute(
                ToolCall(
                    id="call-1",
                    name="core_memory_append",
                    arguments={"label": "human",
                               "content": "Has a dog named Biscuit"},
                )
            )
            assert result.is_error is False
            return AgentResult(
                response="Noted.",
                model="test-model",
                provider="test-provider",
                stop_reason="end_turn",
                step_traces=[StepTrace(step_index=0, assistant_text="Noted.")],
            )

    runner = RecordingRunner()
    monkeypatch.setattr(agent_service, "get_or_build_runner", lambda: runner)
    monkeypatch.setattr(
        agent_service, "_run_post_turn_hooks", lambda **kwargs: None)

    with _soul_db_session() as soul_db, runtime_db_session() as runtime_db:
        user = User(username="service-trace", password_hash="x",
                    display_name="Service Trace")
        soul_db.add(user)
        soul_db.commit()

        result = await agent_service.run_agent("remember this", user.id, soul_db, runtime_db)

        run = runtime_db.scalar(
            select(RuntimeRun).order_by(RuntimeRun.id.desc()))
        op = runtime_db.scalar(select(PendingMemoryOp))

    assert result.response == "Noted."
    assert run is not None
    assert op is not None
    assert op.source_run_id == run.id
    assert op.source_tool_call_id == "call-1"


def test_write_boundary_enforcement() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "anima_server"
    forbidden_files = (
        root / "services" / "agent" / "tools.py",
        root / "services" / "agent" / "service.py",
        root / "services" / "agent" / "executor.py",
    )
    forbidden_symbols = {
        "soul_writer",
        "append_to_soul_block",
        "replace_in_soul_block",
        "full_replace_soul_block",
        "set_soul_block",
    }

    for path in forbidden_files:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))

        imported_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported_names.add(node.module)
                imported_names.update(alias.name for alias in node.names)

        overlap = forbidden_symbols & imported_names
        assert not overlap, f"{path.name} must not import soul write helpers: {sorted(overlap)}"

    tools_source = forbidden_files[0].read_text(encoding="utf-8")
    assert "SelfModelBlock" not in tools_source
    assert "set_self_model_block" not in tools_source
