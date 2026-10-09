from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_diary_without_current_authority_never_reads_legacy_rows(monkeypatch):
    from anima_server.api.routes import diary

    session = SimpleNamespace(user_id=7)
    monkeypatch.setattr(diary, "require_unlocked_session_async", AsyncMock(return_value=session))
    monkeypatch.setattr(diary, "writing_corefs_authority_active", lambda session: False)
    if hasattr(diary, "list_diary_entries"):
        monkeypatch.setattr(diary, "list_diary_entries", lambda *args, **kwargs: [])
    with pytest.raises(HTTPException) as denied:
        await diary.list_entries(request=SimpleNamespace(), userId=7, limit=5)
    assert denied.value.status_code == 409


@pytest.mark.asyncio
async def test_chat_history_without_current_authority_never_reads_runtime_transcripts(monkeypatch):
    from anima_server.api.routes import chat

    session = SimpleNamespace(user_id=7)
    monkeypatch.setattr(chat, "require_unlocked_user_async", AsyncMock(return_value=session))
    monkeypatch.setattr(chat, "conversation_corefs_authority_active", lambda session: False)
    if hasattr(chat, "list_agent_history"):
        monkeypatch.setattr(chat, "list_agent_history", lambda *args, **kwargs: [])
    with pytest.raises(HTTPException) as denied:
        await chat.get_chat_history(request=SimpleNamespace(), userId=7, limit=5)
    assert denied.value.status_code == 409


def test_presence_without_current_authority_never_uses_defaults_or_legacy_rows(monkeypatch):
    from anima_server.services.corefs import authority, preferences
    from anima_server.services.presence_config import get_presence_config_values

    monkeypatch.setattr(preferences, "active_preference_authority_session", lambda user_id: None)
    monkeypatch.setattr(authority, "core_authority_state_or_none", lambda: None)
    db = SimpleNamespace(scalar=lambda statement: None)
    with pytest.raises(RuntimeError, match="unlocked CoreFS"):
        get_presence_config_values(db, 7)


def test_agent_content_consumers_have_no_obsolete_soul_orm_paths():
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "src" / "anima_server"
    forbidden = {
        "Task",
        "AgentMessage",
        "AgentRun",
        "AgentStep",
        "AgentThread",
        "BackgroundTaskRun",
    }
    for name in (
        "services/eval_reset.py",
        "services/agent/proactive.py",
        "services/agent/tools.py",
        "services/agent/memory_blocks.py",
    ):
        tree = ast.parse((source / name).read_text(encoding="utf-8"))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith("anima_server.models")
            for alias in node.names
        }
        assert not imports.intersection(forbidden), name


def test_agent_task_tools_commit_to_current_corefs_only():
    from anima_server.services.agent import tools
    from anima_server.services.agent.tool_context import (
        ToolContext,
        clear_tool_context,
        set_tool_context,
    )
    from anima_server.services.corefs.task_authority import list_canonical_tasks
    from anima_server.services.sessions import unlock_session_store
    from conftest import managed_test_client

    class NoSoulTaskDatabase:
        def __getattr__(self, name):
            raise AssertionError(f"Agent task tool accessed legacy database method {name}")

    with managed_test_client("anima-agent-native-tasks-") as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": "native-agent-task", "password": "pw123456", "name": "Task owner"},
        )
        assert registered.status_code == 201, registered.text
        payload = registered.json()
        user_id = int(payload["id"])
        session = unlock_session_store.resolve(str(payload["unlockToken"]))
        assert session is not None
        ctx = ToolContext(
            db=NoSoulTaskDatabase(), runtime_db=NoSoulTaskDatabase(), user_id=user_id, thread_id=1
        )
        set_tool_context(ctx)
        try:
            assert (
                tools.create_task(text="Check migration", due_date="2026-10-09", priority="4")
                == "Task created: Check migration (due 2026-10-09)"
            )
            assert tools.list_tasks() == "- [open] Check migration (priority 4) due 2026-10-09"
            assert (
                tools.update_task(
                    text="Check migration",
                    new_text="Verify migration",
                    due_date="clear",
                    priority="5",
                )
                == "Updated task 'Verify migration': text='Verify migration', priority=5, due date cleared"
            )
            assert tools.complete_task(text="Verify migration") == "Completed: Verify migration"
            tasks = list_canonical_tasks(session=session)
            assert len(tasks) == 1 and tasks[0].done and tasks[0].completed_at is not None
            assert tools.delete_task(text="Verify migration") == "Deleted task: Verify migration"
            assert list_canonical_tasks(session=session) == ()
            assert ctx.memory_modified is True
        finally:
            clear_tool_context()
