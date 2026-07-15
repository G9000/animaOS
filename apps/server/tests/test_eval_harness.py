from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
from conftest import managed_test_client
from conftest_runtime import runtime_db_session
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

EVAL_DIR = Path(__file__).resolve().parents[1] / "eval"
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

from anima_server.config import settings
from anima_server.db.base import Base
from anima_server.db.runtime import get_runtime_session_factory
from anima_server.models import (
    AffectStateRow,
    AgentExperience,
    AgentSkill,
    ExperienceClusterState,
    ForesightSignal,
    MemoryItem,
    MemoryItemEvidence,
    RuntimeDocument,
    RuntimeDocumentChunk,
    RuntimeMessage,
    RuntimeRun,
    RuntimeThread,
    RuntimeWorkflowCheckpoint,
    RuntimeWorkflowRun,
    User,
    UserProfileField,
    UserProfileFieldEvidence,
)
from anima_server.models.runtime_memory import MemoryCandidate, ProfileUpdateCandidate
from eval_client import HttpAnimaClient
from run_agent_eval import build_eval_plan


def _json_body(request: httpx.Request) -> dict[str, object]:
    return json.loads(request.content.decode("utf-8"))


def test_eval_profile_pr_runs_in_process_smoke_only() -> None:
    plan = build_eval_plan(
        profile="pr",
        output_dir=Path("eval-results"),
        create_user=True,
        agent_provider="ollama",
        agent_model="local-model",
        agent_base_url="http://ollama.test",
    )

    assert [command.name for command in plan] == ["conversation-smoke"]
    assert "run_conversation_eval.py" in plan[0].argv[1]
    assert "--mode" in plan[0].argv
    assert "in-process" in plan[0].argv
    assert "--agent-provider" in plan[0].argv
    assert "ollama" in plan[0].argv
    assert "--agent-model" in plan[0].argv
    assert "local-model" in plan[0].argv
    assert "--agent-base-url" in plan[0].argv
    assert "http://ollama.test" in plan[0].argv
    assert "--disable-background-memory" in plan[0].argv
    assert "--create-user" not in plan[0].argv


def test_eval_profile_nightly_uses_longmemeval_primary() -> None:
    plan = build_eval_plan(
        profile="nightly",
        output_dir=Path("eval-results"),
        base_url="http://anima.test",
        username="eval-user",
        password="secret",
        create_user=True,
    )

    assert [command.name for command in plan] == ["longmemeval-nightly"]
    command = plan[0]
    assert "run_longmemeval.py" in command.argv[1]
    assert "--dataset" in command.argv
    assert "oracle" in command.argv
    assert "--limit" in command.argv
    assert "50" in command.argv
    assert "--base-url" in command.argv
    assert "http://anima.test" in command.argv
    assert "--create-user" in command.argv
    assert all("run_locomo.py" not in item for item in command.argv)


def test_eval_profile_release_runs_memory_then_locomo_with_scoring() -> None:
    plan = build_eval_plan(
        profile="release",
        output_dir=Path("eval-results"),
        score=True,
        judge_model="judge-model",
    )

    assert [command.name for command in plan] == [
        "longmemeval-release",
        "score-longmemeval-release",
        "locomo-release",
        "score-locomo-release",
    ]
    longmemeval = plan[0]
    locomo = plan[2]
    assert "run_longmemeval.py" in longmemeval.argv[1]
    assert "--limit" not in longmemeval.argv
    assert "run_locomo.py" in locomo.argv[1]
    assert "--categories" in locomo.argv
    assert "1,2,3,5" in locomo.argv
    assert "score_results.py" in plan[1].argv[1]
    assert "score_results.py" in plan[3].argv[1]
    assert "judge-model" in plan[1].argv
    assert "judge-model" in plan[3].argv


def test_eval_profile_ablation_uses_same_longmemeval_slice_per_config() -> None:
    plan = build_eval_plan(
        profile="ablation",
        output_dir=Path("eval-results"),
        ablation_configs=("baseline", "memory_only", "memory_reflection", "full"),
    )

    assert [command.name for command in plan] == [
        "longmemeval-ablation-baseline",
        "longmemeval-ablation-memory_only",
        "longmemeval-ablation-memory_reflection",
        "longmemeval-ablation-full",
    ]
    for command in plan:
        assert "run_longmemeval.py" in command.argv[1]
        assert "--dataset" in command.argv
        assert "oracle" in command.argv
        assert "--limit" in command.argv
        assert "50" in command.argv


def test_http_eval_client_uses_benchmark_timeout_default() -> None:
    client = HttpAnimaClient("http://anima.test", username="eval", password="secret")

    assert client.timeout == 600.0


def test_score_results_disables_ollama_thinking_for_judge(monkeypatch) -> None:
    from score_results import judge_with_ollama

    requests: list[dict[str, object]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"response": "YES"}

    def fake_post(*args: object, **kwargs: object) -> FakeResponse:
        del args
        requests.append(kwargs["json"])
        return FakeResponse()

    monkeypatch.setattr("score_results.httpx.post", fake_post)

    verdict = judge_with_ollama(
        question="What issue happened?",
        expected="GPS system not functioning correctly",
        response="That was the GPS issue.",
        prompt_key="default",
        model="qwen3.5:latest",
        base_url="http://localhost:11434",
    )

    assert verdict["is_correct"] is True
    assert requests[0]["think"] is False


def test_longmemeval_builds_original_transcript_import_sessions() -> None:
    from run_longmemeval import build_import_sessions

    sessions = build_import_sessions(
        {
            "haystack_dates": ["2023/04/10 (Mon) 14:47"],
            "haystack_sessions": [
                [
                    {"role": "user", "content": "I had a GPS issue."},
                    {"role": "assistant", "content": "It was fixed by the dealership."},
                ]
            ],
        }
    )

    assert sessions == [
        {
            "date": "2023/04/10 (Mon) 14:47",
            "turns": [
                {"role": "user", "content": "I had a GPS issue."},
                {"role": "assistant", "content": "It was fixed by the dealership."},
            ],
        }
    ]


def test_longmemeval_select_dataset_applies_offset_and_limit() -> None:
    from run_longmemeval import select_dataset

    dataset = [
        {"question_id": f"q{i}", "question_type": "temporal-reasoning"}
        for i in range(5)
    ]

    selected = select_dataset(dataset, offset=2, limit=2, sample="sequential")

    assert [(index, item["question_id"]) for index, item in selected] == [
        (2, "q2"),
        (3, "q3"),
    ]


def test_longmemeval_select_dataset_can_build_mixed_slice() -> None:
    from run_longmemeval import select_dataset

    dataset = [
        {"question_id": "t1", "question_type": "temporal-reasoning"},
        {"question_id": "t2", "question_type": "temporal-reasoning"},
        {"question_id": "t3", "question_type": "temporal-reasoning"},
        {"question_id": "single1", "question_type": "single-session-user"},
        {"question_id": "single2", "question_type": "single-session-user"},
        {"question_id": "multi1", "question_type": "multi-session"},
    ]

    selected = select_dataset(dataset, offset=0, limit=4, sample="mixed")

    assert [item["question_id"] for _index, item in selected] == [
        "t1",
        "single1",
        "multi1",
        "t2",
    ]
    assert [index for index, _item in selected] == [0, 3, 5, 1]


def test_longmemeval_pending_selection_skips_existing_question_ids() -> None:
    from run_longmemeval import pending_selected_items

    selected = [
        (0, {"question_id": "q0"}),
        (1, {"question_id": "q1"}),
        (2, {"question_id": "q2"}),
    ]
    existing_results = [
        {"question_id": "q0", "ai_response": "already done"},
        {"question_id": "q2", "ai_response": "already done"},
    ]

    pending = pending_selected_items(selected, existing_results)

    assert pending == [(1, {"question_id": "q1"})]


def test_longmemeval_estimates_anthropic_haiku_cost_from_usage() -> None:
    from run_longmemeval import estimate_response_cost

    cost = estimate_response_cost(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        usage={
            "promptTokens": 1_000,
            "completionTokens": 500,
            "totalTokens": 1_500,
        },
    )

    assert cost == {
        "currency": "USD",
        "inputUsdPerMillionTokens": 1.0,
        "outputUsdPerMillionTokens": 5.0,
        "promptUsd": 0.001,
        "completionUsd": 0.0025,
        "totalUsd": 0.0035,
        "pricingSource": "anthropic:claude-haiku-4-5",
    }


@pytest.mark.asyncio
async def test_longmemeval_run_question_records_import_timing() -> None:
    from run_longmemeval import run_question

    class FakeClient:
        def __init__(self) -> None:
            self.imported_sessions: list[dict[str, object]] | None = None
            self.import_mode: str | None = None

        async def reset_memory(self) -> None:
            return None

        async def import_transcript_sessions(
            self,
            sessions: list[dict[str, object]],
            *,
            extraction_mode: str = "llm_pairs",
            embed_raw_chunks: bool = False,
        ) -> dict[str, object]:
            assert embed_raw_chunks is False
            self.imported_sessions = sessions
            self.import_mode = extraction_mode
            return {
                "sessionsImported": len(sessions),
                "messagesImported": 2,
                "turnPairsImported": 0,
                "memoryItemsImported": 1,
            }

        async def send_message_data(self, text: str) -> dict[str, object]:
            assert text == "What issue happened?"
            return {
                "response": "It was the GPS issue.",
                "model": "claude-haiku-4-5-20251001",
                "provider": "anthropic",
                "usage": {
                    "promptTokens": 1_000,
                    "completionTokens": 500,
                    "totalTokens": 1_500,
                },
            }

    client = FakeClient()
    result = await run_question(
        client,
        {
            "question": "What issue happened?",
            "answer": "GPS issue",
            "question_id": "q1",
            "question_type": "temporal-reasoning",
            "haystack_dates": ["2023-04-10"],
            "haystack_sessions": [
                [
                    {"role": "user", "content": "I had a GPS issue."},
                    {"role": "assistant", "content": "That was fixed."},
                ]
            ],
        },
        0,
        import_mode="raw_chunks",
    )

    assert client.import_mode == "raw_chunks"
    assert client.imported_sessions == [
        {
            "date": "2023-04-10",
            "turns": [
                {"role": "user", "content": "I had a GPS issue."},
                {"role": "assistant", "content": "That was fixed."},
            ],
        }
    ]
    assert result["import"]["memoryItemsImported"] == 1
    assert result["timing"]["totalSeconds"] >= 0
    assert result["timing"]["importSeconds"] >= 0
    assert result["usage"] == {
        "promptTokens": 1_000,
        "completionTokens": 500,
        "totalTokens": 1_500,
    }
    assert result["cost"]["totalUsd"] == 0.0035


def test_locomo_builds_original_transcript_import_sessions() -> None:
    from run_locomo import build_import_sessions

    sessions = build_import_sessions(
        {
            "speaker_a": "Alice",
            "speaker_b": "Bob",
            "session_1_date_time": "2023-04-10",
            "session_1": [
                {"speaker": "Alice", "text": "I prefer Shell gas."},
                {"speaker": "Bob", "text": "You mentioned a GPS issue too."},
            ],
        }
    )

    assert sessions == [
        {
            "date": "2023-04-10",
            "turns": [
                {"role": "user", "content": "I prefer Shell gas."},
                {"role": "assistant", "content": "You mentioned a GPS issue too."},
            ],
        }
    ]


@pytest.mark.asyncio
async def test_http_eval_client_imports_transcript_via_eval_endpoint() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/auth/login":
            return httpx.Response(
                200,
                json={
                    "id": 17,
                    "username": "eval",
                    "name": "Eval User",
                    "unlockToken": "unlock-17",
                    "message": "Login successful",
                },
            )
        if request.url.path == "/api/eval/import-transcript":
            assert request.headers["x-anima-unlock"] == "unlock-17"
            assert _json_body(request) == {
                "userId": 17,
                "extractionMode": "raw_chunks",
                "sessions": [
                    {
                        "date": "2023-04-10",
                        "turns": [
                            {"role": "user", "content": "I had a GPS issue."},
                            {"role": "assistant", "content": "It was fixed."},
                        ],
                    }
                ],
            }
            return httpx.Response(
                200,
                json={
                    "status": "imported",
                    "sessionsImported": 1,
                    "messagesImported": 2,
                    "turnPairsImported": 1,
                    "errors": [],
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    client = HttpAnimaClient(
        "http://anima.test",
        username="eval",
        password="eval-password",
        transport=httpx.MockTransport(handler),
    )

    try:
        result = await client.import_transcript_sessions(
            [
                {
                    "date": "2023-04-10",
                    "turns": [
                        {"role": "user", "content": "I had a GPS issue."},
                        {"role": "assistant", "content": "It was fixed."},
                    ],
                }
            ],
            extraction_mode="raw_chunks",
        )
    finally:
        await client.close()

    assert result["turnPairsImported"] == 1
    assert [request.url.path for request in requests] == [
        "/api/auth/login",
        "/api/eval/import-transcript",
    ]


@pytest.mark.asyncio
async def test_http_eval_client_uses_current_auth_and_chat_contract() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/auth/login":
            assert _json_body(request) == {
                "username": "eval",
                "password": "eval-password",
            }
            return httpx.Response(
                200,
                json={
                    "id": 17,
                    "username": "eval",
                    "name": "Eval User",
                    "unlockToken": "unlock-17",
                    "message": "Login successful",
                },
            )
        if request.url.path == "/api/chat":
            assert request.headers["x-anima-unlock"] == "unlock-17"
            assert _json_body(request) == {"message": "hello", "userId": 17}
            return httpx.Response(
                200,
                json={"response": "hi", "model": "test-model", "provider": "test"},
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    client = HttpAnimaClient(
        "http://anima.test",
        username="eval",
        password="eval-password",
        transport=httpx.MockTransport(handler),
    )

    try:
        assert await client.send_message("hello") == "hi"
    finally:
        await client.close()

    assert [request.url.path for request in requests] == [
        "/api/auth/login",
        "/api/chat",
    ]


@pytest.mark.asyncio
async def test_http_eval_client_can_return_chat_metadata() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(
                200,
                json={
                    "id": 17,
                    "username": "eval",
                    "name": "Eval User",
                    "unlockToken": "unlock-17",
                    "message": "Login successful",
                },
            )
        if request.url.path == "/api/chat":
            return httpx.Response(
                200,
                json={
                    "response": "hi",
                    "model": "test-model",
                    "provider": "test",
                    "toolsUsed": ["recall_memory"],
                    "retrieval": {"retriever": "hybrid"},
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    client = HttpAnimaClient(
        "http://anima.test",
        username="eval",
        password="eval-password",
        transport=httpx.MockTransport(handler),
    )

    try:
        data = await client.send_message_data("hello")
    finally:
        await client.close()

    assert data["response"] == "hi"
    assert data["model"] == "test-model"
    assert data["provider"] == "test"
    assert data["toolsUsed"] == ["recall_memory"]
    assert data["retrieval"] == {"retriever": "hybrid"}


@pytest.mark.asyncio
async def test_http_eval_client_resets_via_eval_reset_endpoint() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/auth/login":
            return httpx.Response(
                200,
                json={
                    "id": 23,
                    "username": "eval",
                    "name": "Eval User",
                    "unlockToken": "unlock-23",
                    "message": "Login successful",
                },
            )
        if request.url.path == "/api/eval/reset":
            assert request.headers["x-anima-unlock"] == "unlock-23"
            assert _json_body(request) == {"userId": 23}
            return httpx.Response(200, json={"status": "reset", "deleted": {}})
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    client = HttpAnimaClient(
        "http://anima.test",
        username="eval",
        password="eval-password",
        transport=httpx.MockTransport(handler),
    )

    try:
        await client.reset_memory()
    finally:
        await client.close()

    assert [request.url.path for request in requests] == [
        "/api/auth/login",
        "/api/eval/reset",
    ]


@pytest.mark.asyncio
async def test_http_eval_client_can_register_disposable_eval_user() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/auth/login":
            return httpx.Response(401, json={"error": "Invalid credentials"})
        if request.url.path == "/api/auth/register":
            assert _json_body(request) == {
                "username": "eval",
                "password": "eval-password",
                "name": "Eval User",
            }
            return httpx.Response(
                201,
                json={
                    "id": 31,
                    "username": "eval",
                    "name": "Eval User",
                    "unlockToken": "unlock-31",
                    "recoveryPhrase": "test phrase",
                },
            )
        if request.url.path == "/api/chat":
            assert request.headers["x-anima-unlock"] == "unlock-31"
            assert _json_body(request) == {"message": "ready?", "userId": 31}
            return httpx.Response(200, json={"response": "ready"})
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    client = HttpAnimaClient(
        "http://anima.test",
        username="eval",
        password="eval-password",
        create_user=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        assert await client.send_message("ready?") == "ready"
    finally:
        await client.close()

    assert [request.url.path for request in requests] == [
        "/api/auth/login",
        "/api/auth/register",
        "/api/chat",
    ]


def test_eval_reset_endpoint_is_disabled_by_default() -> None:
    with managed_test_client("eval-reset-disabled-") as client:
        register_response = client.post(
            "/api/auth/register",
            json={
                "username": "eval",
                "password": "eval-password",
                "name": "Eval User",
            },
        )
        assert register_response.status_code == 201
        user = register_response.json()

        response = client.post(
            "/api/eval/reset",
            json={"userId": int(user["id"])},
            headers={"x-anima-unlock": str(user["unlockToken"])},
        )

    assert response.status_code == 403
    assert "ANIMA_EVAL_RESET_ENABLED" in response.json()["error"]


def test_eval_reset_drains_background_memory_tasks(monkeypatch) -> None:
    events: list[str] = []

    async def fake_drain_background_memory_tasks() -> None:
        events.append("drain")

    def fake_reset_eval_user_state(**kwargs: object) -> dict[str, int]:
        del kwargs
        events.append("reset")
        assert events == ["drain", "reset"]
        return {}

    monkeypatch.setattr(
        "anima_server.services.agent.consolidation.drain_background_memory_tasks",
        fake_drain_background_memory_tasks,
    )
    monkeypatch.setattr(
        "anima_server.api.routes.eval.reset_eval_user_state",
        fake_reset_eval_user_state,
    )

    original_enabled = settings.eval_reset_enabled
    settings.eval_reset_enabled = True
    try:
        with managed_test_client("eval-reset-drain-") as client:
            register_response = client.post(
                "/api/auth/register",
                json={
                    "username": "eval",
                    "password": "eval-password",
                    "name": "Eval User",
                },
            )
            assert register_response.status_code == 201
            user = register_response.json()

            response = client.post(
                "/api/eval/reset",
                json={"userId": int(user["id"])},
                headers={"x-anima-unlock": str(user["unlockToken"])},
            )
    finally:
        settings.eval_reset_enabled = original_enabled

    assert response.status_code == 200
    assert events[:2] == ["drain", "reset"]


def test_eval_import_transcript_is_disabled_by_default() -> None:
    with managed_test_client("eval-import-disabled-") as client:
        register_response = client.post(
            "/api/auth/register",
            json={
                "username": "eval",
                "password": "eval-password",
                "name": "Eval User",
            },
        )
        assert register_response.status_code == 201
        user = register_response.json()

        response = client.post(
            "/api/eval/import-transcript",
            json={
                "userId": int(user["id"]),
                "sessions": [
                    {
                        "date": "2023-04-10",
                        "turns": [
                            {"role": "user", "content": "I had a GPS issue."},
                            {"role": "assistant", "content": "It was fixed."},
                        ],
                    }
                ],
            },
            headers={"x-anima-unlock": str(user["unlockToken"])},
        )

    assert response.status_code == 403
    assert "ANIMA_EVAL_RESET_ENABLED" in response.json()["error"]


def test_eval_import_transcript_processes_all_pairs(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    soul_writer_users: list[int] = []

    async def fake_run_background_extraction(**kwargs: object) -> None:
        calls.append(kwargs)

    async def fake_run_soul_writer(user_id: int) -> None:
        soul_writer_users.append(user_id)

    monkeypatch.setattr(
        "anima_server.services.agent.consolidation.run_background_extraction",
        fake_run_background_extraction,
    )
    monkeypatch.setattr(
        "anima_server.services.agent.soul_writer.run_soul_writer",
        fake_run_soul_writer,
    )

    original_enabled = settings.eval_reset_enabled
    settings.eval_reset_enabled = True
    try:
        with managed_test_client("eval-import-enabled-") as client:
            register_response = client.post(
                "/api/auth/register",
                json={
                    "username": "eval",
                    "password": "eval-password",
                    "name": "Eval User",
                },
            )
            assert register_response.status_code == 201
            user = register_response.json()
            user_id = int(user["id"])
            calls.clear()
            soul_writer_users.clear()
            turns: list[dict[str, str]] = []
            for index in range(25):
                turns.append({"role": "user", "content": f"Fact {index}"})
                turns.append({"role": "assistant", "content": f"Ack {index}"})

            response = client.post(
                "/api/eval/import-transcript",
                json={
                    "userId": user_id,
                    "sessions": [
                        {
                            "date": "2023/04/10 (Mon) 14:47",
                            "turns": turns,
                        }
                    ],
                },
                headers={"x-anima-unlock": str(user["unlockToken"])},
            )

            rt_factory = get_runtime_session_factory()
            with rt_factory() as runtime_db:
                imported = list(
                    runtime_db.scalars(
                        select(RuntimeMessage)
                        .where(RuntimeMessage.user_id == user_id)
                        .order_by(RuntimeMessage.sequence_id)
                    ).all()
                )
    finally:
        settings.eval_reset_enabled = original_enabled

    assert response.status_code == 200
    assert response.json()["turnPairsImported"] == 25
    assert len(calls) == 25
    assert calls[0]["user_message"] == "[Session date: 2023/04/10 (Mon) 14:47] Fact 0"
    assert calls[-1]["user_message"] == "[Session date: 2023/04/10 (Mon) 14:47] Fact 24"
    assert calls[0]["source_message_ids"] == [imported[0].id, imported[1].id]
    assert calls[-1]["source_message_ids"] == [imported[-2].id, imported[-1].id]
    assert all(call["trigger_soul_writer"] is False for call in calls)
    assert soul_writer_users
    assert soul_writer_users[-1] == user_id
    assert len(imported) == 50
    assert imported[0].source == "eval_import"
    assert imported[0].is_in_context is False


def test_eval_import_transcript_raw_chunks_avoids_llm_extraction(monkeypatch) -> None:
    async def fail_run_background_extraction(**kwargs: object) -> None:
        raise AssertionError(f"raw chunk import should not call extraction: {kwargs}")

    async def fail_run_soul_writer(user_id: int) -> None:
        raise AssertionError(f"raw chunk import should not call soul writer: {user_id}")

    async def fail_generate_embeddings_batch(texts: list[str]) -> list[None]:
        raise AssertionError(f"fast raw import should not embed by default: {texts}")

    monkeypatch.setattr(
        "anima_server.services.agent.consolidation.run_background_extraction",
        fail_run_background_extraction,
    )
    monkeypatch.setattr(
        "anima_server.services.agent.soul_writer.run_soul_writer",
        fail_run_soul_writer,
    )
    monkeypatch.setattr(
        "anima_server.services.agent.embeddings.generate_embeddings_batch",
        fail_generate_embeddings_batch,
    )

    original_enabled = settings.eval_reset_enabled
    settings.eval_reset_enabled = True
    try:
        with managed_test_client("eval-import-raw-") as client:
            register_response = client.post(
                "/api/auth/register",
                json={
                    "username": "eval",
                    "password": "eval-password",
                    "name": "Eval User",
                },
            )
            assert register_response.status_code == 201
            user = register_response.json()
            user_id = int(user["id"])

            response = client.post(
                "/api/eval/import-transcript",
                json={
                    "userId": user_id,
                    "extractionMode": "raw_chunks",
                    "sessions": [
                        {
                            "date": "2023/04/10 (Mon) 14:47",
                            "turns": [
                                {"role": "user", "content": "I had a GPS issue."},
                                {"role": "assistant", "content": "The dealership fixed it."},
                                {"role": "user", "content": "I prefer Shell gas."},
                                {"role": "assistant", "content": "Noted."},
                            ],
                        }
                    ],
                },
                headers={"x-anima-unlock": str(user["unlockToken"])},
            )

            from anima_server.db.session import get_user_session_factory
            from anima_server.services.data_crypto import df

            with get_user_session_factory(user_id)() as db:
                memory_items = list(
                    db.scalars(
                        select(MemoryItem)
                        .where(
                            MemoryItem.user_id == user_id,
                            MemoryItem.source == "eval_import_raw",
                        )
                        .order_by(MemoryItem.id)
                    ).all()
                )
                plaintext = [
                    df(user_id, item.content, table="memory_items", field="content")
                    for item in memory_items
                ]
                evidence_rows = list(
                    db.scalars(
                        select(MemoryItemEvidence)
                        .where(MemoryItemEvidence.user_id == user_id)
                        .order_by(MemoryItemEvidence.id)
                    ).all()
                )
                evidence_plaintext = [
                    df(
                        user_id,
                        evidence.evidence_text,
                        table="memory_item_evidence",
                        field="evidence_text",
                    )
                    for evidence in evidence_rows
                ]

            rt_factory = get_runtime_session_factory()
            with rt_factory() as runtime_db:
                imported_messages = list(
                    runtime_db.scalars(
                        select(RuntimeMessage)
                        .where(RuntimeMessage.user_id == user_id)
                        .order_by(RuntimeMessage.sequence_id)
                    ).all()
                )
    finally:
        settings.eval_reset_enabled = original_enabled

    assert response.status_code == 200
    assert response.json()["extractionMode"] == "raw_chunks"
    assert response.json()["turnPairsImported"] == 0
    assert response.json()["memoryItemsImported"] == 2
    assert response.json()["embeddingItemsImported"] == 0
    assert response.json()["errors"] == []
    assert len(plaintext) == 2
    assert plaintext[0].startswith("Session date: 2023/04/10 (Mon) 14:47")
    assert "User: I had a GPS issue." in plaintext[0]
    assert "Assistant: The dealership fixed it." in plaintext[0]
    assert "User: I prefer Shell gas." in plaintext[1]
    assert len(evidence_rows) == 2
    assert evidence_rows[0].source_kind == "eval_import"
    assert evidence_rows[0].runtime_message_id == imported_messages[0].id
    assert evidence_rows[0].runtime_message_ids_json == [
        imported_messages[0].id,
        imported_messages[1].id,
    ]
    assert evidence_rows[0].runtime_thread_id == imported_messages[0].thread_id
    assert evidence_rows[0].sequence_id == imported_messages[0].sequence_id
    assert evidence_rows[0].speaker == "user"
    assert evidence_rows[0].observed_at is not None
    assert evidence_rows[0].extractor == "eval_import"
    assert evidence_rows[0].metadata_json == {
        "source": "raw_chunks",
        "session_date": "2023/04/10 (Mon) 14:47",
    }
    assert evidence_plaintext[0] == plaintext[0]


def test_eval_import_transcript_raw_chunks_ignores_invalid_session_date(
    monkeypatch,
) -> None:
    from anima_server.api.routes.eval import _parse_session_observed_at

    async def fail_run_background_extraction(**kwargs: object) -> None:
        raise AssertionError(f"raw chunk import should not call extraction: {kwargs}")

    async def fail_run_soul_writer(user_id: int) -> None:
        raise AssertionError(f"raw chunk import should not call soul writer: {user_id}")

    async def fail_generate_embeddings_batch(texts: list[str]) -> list[None]:
        raise AssertionError(f"fast raw import should not embed by default: {texts}")

    monkeypatch.setattr(
        "anima_server.services.agent.consolidation.run_background_extraction",
        fail_run_background_extraction,
    )
    monkeypatch.setattr(
        "anima_server.services.agent.soul_writer.run_soul_writer",
        fail_run_soul_writer,
    )
    monkeypatch.setattr(
        "anima_server.services.agent.embeddings.generate_embeddings_batch",
        fail_generate_embeddings_batch,
    )

    original_enabled = settings.eval_reset_enabled
    settings.eval_reset_enabled = True
    try:
        with managed_test_client("eval-import-invalid-date-") as client:
            register_response = client.post(
                "/api/auth/register",
                json={
                    "username": "eval",
                    "password": "eval-password",
                    "name": "Eval User",
                },
            )
            assert register_response.status_code == 201
            user = register_response.json()
            user_id = int(user["id"])

            response = client.post(
                "/api/eval/import-transcript",
                json={
                    "userId": user_id,
                    "extractionMode": "raw_chunks",
                    "sessions": [
                        {
                            "date": "2023/99/99",
                            "turns": [
                                {"role": "user", "content": "I had a GPS issue."},
                                {"role": "assistant", "content": "The dealership fixed it."},
                            ],
                        }
                    ],
                },
                headers={"x-anima-unlock": str(user["unlockToken"])},
            )

            from anima_server.db.session import get_user_session_factory
            from anima_server.services.data_crypto import df

            with get_user_session_factory(user_id)() as db:
                memory_items = list(
                    db.scalars(
                        select(MemoryItem)
                        .where(
                            MemoryItem.user_id == user_id,
                            MemoryItem.source == "eval_import_raw",
                        )
                        .order_by(MemoryItem.id)
                    ).all()
                )
                plaintext = [
                    df(user_id, item.content, table="memory_items", field="content")
                    for item in memory_items
                ]
                evidence_rows = list(
                    db.scalars(
                        select(MemoryItemEvidence)
                        .where(MemoryItemEvidence.user_id == user_id)
                        .order_by(MemoryItemEvidence.id)
                    ).all()
                )
    finally:
        settings.eval_reset_enabled = original_enabled

    assert response.status_code == 200
    assert response.json()["memoryItemsImported"] == 1
    assert response.json()["errors"] == []
    assert plaintext == [
        "Session date: 2023/99/99\n"
        "User: I had a GPS issue.\n"
        "Assistant: The dealership fixed it."
    ]
    assert len(evidence_rows) == 1
    assert evidence_rows[0].observed_at is not None
    assert _parse_session_observed_at("2023/99/99") is None


def test_reset_eval_user_state_purges_soul_and_runtime_rows() -> None:
    from anima_server.services.eval_reset import reset_eval_user_state

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    try:
        with factory() as soul_db, runtime_db_session() as runtime_db:
            eval_item = MemoryItem(user_id=1, content="Eval fact", category="fact")
            other_item = MemoryItem(user_id=2, content="Other fact", category="fact")
            soul_db.add_all(
                [
                    User(id=1, username="eval", display_name="Eval User", password_hash="x"),
                    User(id=2, username="other", display_name="Other User", password_hash="x"),
                    eval_item,
                    other_item,
                ]
            )
            soul_db.flush()
            soul_db.add_all(
                [
                    MemoryItemEvidence(
                        user_id=1,
                        memory_item_id=eval_item.id,
                        source_kind="eval_import",
                        evidence_text="Eval evidence",
                    ),
                    MemoryItemEvidence(
                        user_id=2,
                        memory_item_id=other_item.id,
                        source_kind="eval_import",
                        evidence_text="Other evidence",
                    ),
                ]
            )
            eval_profile = UserProfileField(
                user_id=1,
                category="work",
                key="role",
                value_text="Eval role",
                status="active",
            )
            other_profile = UserProfileField(
                user_id=2,
                category="work",
                key="role",
                value_text="Other role",
                status="active",
            )
            soul_db.add_all([eval_profile, other_profile])
            soul_db.flush()
            soul_db.add_all(
                [
                    UserProfileFieldEvidence(
                        user_id=1,
                        profile_field_id=eval_profile.id,
                        source_kind="eval",
                        evidence_text="Eval profile evidence",
                    ),
                    UserProfileFieldEvidence(
                        user_id=2,
                        profile_field_id=other_profile.id,
                        source_kind="eval",
                        evidence_text="Other profile evidence",
                    ),
                ]
            )
            soul_db.add_all(
                [
                    ForesightSignal(
                        user_id=1,
                        content="Eval has a dentist appointment tomorrow",
                        evidence="Eval mentioned a dentist appointment",
                        source_message_ids_json=[101],
                    ),
                    ForesightSignal(
                        user_id=2,
                        content="Other has a dentist appointment tomorrow",
                        evidence="Other mentioned a dentist appointment",
                        source_message_ids_json=[201],
                    ),
                    AgentExperience(
                        user_id=1,
                        task_intent="answer eval case",
                        approach="used eval context",
                        quality_score=0.7,
                        cluster_id="eval-cluster",
                    ),
                    AgentExperience(
                        user_id=2,
                        task_intent="answer other case",
                        approach="used other context",
                        quality_score=0.7,
                        cluster_id="other-cluster",
                    ),
                    ExperienceClusterState(
                        user_id=1,
                        state_json={"clusters": {"eval-cluster": {"count": 1}}},
                    ),
                    ExperienceClusterState(
                        user_id=2,
                        state_json={"clusters": {"other-cluster": {"count": 1}}},
                    ),
                    AgentSkill(
                        user_id=1,
                        cluster_id="eval-cluster",
                        name="Eval skill",
                        description="Eval procedural memory",
                        content="Use eval evidence.",
                        confidence=0.8,
                        experience_count=1,
                    ),
                    AgentSkill(
                        user_id=2,
                        cluster_id="other-cluster",
                        name="Other skill",
                        description="Other procedural memory",
                        content="Use other evidence.",
                        confidence=0.8,
                        experience_count=1,
                    ),
                ]
            )

            runtime_db.add_all(
                [
                    RuntimeThread(id=10, user_id=1, status="active"),
                    RuntimeThread(id=20, user_id=2, status="active"),
                    MemoryCandidate(
                        user_id=1,
                        content="Eval candidate",
                        category="fact",
                        source="eval",
                        content_hash="hash-1",
                    ),
                    MemoryCandidate(
                        user_id=2,
                        content="Other candidate",
                        category="fact",
                        source="eval",
                        content_hash="hash-2",
                    ),
                    ProfileUpdateCandidate(
                        user_id=1,
                        category="work",
                        key="role",
                        value="Eval profile candidate",
                        evidence_text="Eval candidate evidence",
                        source="eval",
                        content_hash="profile-hash-1",
                    ),
                    ProfileUpdateCandidate(
                        user_id=2,
                        category="work",
                        key="role",
                        value="Other profile candidate",
                        evidence_text="Other candidate evidence",
                        source="eval",
                        content_hash="profile-hash-2",
                    ),
                ]
            )
            runtime_db.flush()
            runtime_db.add_all(
                [
                    RuntimeWorkflowRun(
                        id=101,
                        user_id=1,
                        thread_id=10,
                        workflow_type="document_ingestion",
                        status="completed",
                        current_state="indexed",
                    ),
                    RuntimeWorkflowRun(
                        id=201,
                        user_id=2,
                        thread_id=20,
                        workflow_type="document_ingestion",
                        status="completed",
                        current_state="indexed",
                    ),
                ]
            )
            runtime_db.flush()
            runtime_db.add_all(
                [
                    RuntimeWorkflowCheckpoint(
                        id=1001,
                        workflow_run_id=101,
                        checkpoint_index=1,
                        state_name="extract",
                        status="completed",
                        idempotency_key="eval-extract",
                        output_json={"chunks": 1},
                    ),
                    RuntimeWorkflowCheckpoint(
                        id=2001,
                        workflow_run_id=201,
                        checkpoint_index=1,
                        state_name="extract",
                        status="completed",
                        idempotency_key="other-extract",
                        output_json={"chunks": 1},
                    ),
                    RuntimeDocument(
                        id=10001,
                        user_id=1,
                        thread_id=10,
                        workflow_run_id=101,
                        filename="eval.pdf",
                        mime_type="application/pdf",
                        storage_path=".anima/documents/1/eval.pdf",
                        sha256="a" * 64,
                        size_bytes=128,
                        status="indexed",
                    ),
                    RuntimeDocument(
                        id=20001,
                        user_id=2,
                        thread_id=20,
                        workflow_run_id=201,
                        filename="other.pdf",
                        mime_type="application/pdf",
                        storage_path=".anima/documents/2/other.pdf",
                        sha256="b" * 64,
                        size_bytes=256,
                        status="indexed",
                    ),
                ]
            )
            runtime_db.flush()
            runtime_db.add_all(
                [
                    RuntimeDocumentChunk(
                        id=100001,
                        document_id=10001,
                        user_id=1,
                        chunk_index=0,
                        content_text="Eval PDF chunk text",
                        content_hash="c" * 64,
                    ),
                    RuntimeDocumentChunk(
                        id=200001,
                        document_id=20001,
                        user_id=2,
                        chunk_index=0,
                        content_text="Other PDF chunk text",
                        content_hash="d" * 64,
                    ),
                ]
            )
            runtime_db.flush()
            runtime_db.add(
                RuntimeRun(
                    id=11,
                    thread_id=10,
                    user_id=1,
                    provider="test",
                    model="test",
                    mode="chat",
                )
            )
            runtime_db.flush()
            runtime_db.add(
                RuntimeMessage(
                    thread_id=10,
                    user_id=1,
                    run_id=11,
                    sequence_id=1,
                    role="user",
                    content_text="hello",
                )
            )
            runtime_db.add_all(
                [
                    AffectStateRow(user_id=1, valence=0.5, arousal=0.8, energy=0.4),
                    AffectStateRow(user_id=2, valence=-0.2, arousal=0.3, energy=0.7),
                ]
            )
            runtime_db.commit()
            soul_db.commit()

            deleted = reset_eval_user_state(
                user_id=1,
                soul_db=soul_db,
                runtime_db=runtime_db,
            )

            assert deleted["memory_items"] == 1
            assert deleted["memory_item_evidence"] == 1
            assert deleted["user_profile_fields"] == 1
            assert deleted["user_profile_field_evidence"] == 1
            assert deleted["runtime_threads"] == 1
            assert deleted["memory_candidates"] == 1
            assert deleted["profile_update_candidates"] == 1
            assert deleted["runtime_document_chunks"] == 1
            assert deleted["runtime_documents"] == 1
            assert deleted["runtime_workflow_checkpoints"] == 1
            assert deleted["runtime_workflow_runs"] == 1
            assert deleted["foresight_signals"] == 1
            assert deleted["agent_experiences"] == 1
            assert deleted["experience_cluster_state"] == 1
            assert deleted["agent_skills"] == 1
            assert deleted["affect_state"] == 1
            assert soul_db.scalars(
                select(MemoryItem).where(MemoryItem.user_id == 1)
            ).all() == []
            assert soul_db.scalars(
                select(MemoryItemEvidence).where(MemoryItemEvidence.user_id == 1)
            ).all() == []
            assert soul_db.scalars(
                select(UserProfileField).where(UserProfileField.user_id == 1)
            ).all() == []
            assert soul_db.scalars(
                select(UserProfileFieldEvidence).where(
                    UserProfileFieldEvidence.user_id == 1
                )
            ).all() == []
            assert soul_db.scalars(
                select(ForesightSignal).where(ForesightSignal.user_id == 1)
            ).all() == []
            assert soul_db.scalars(
                select(AgentExperience).where(AgentExperience.user_id == 1)
            ).all() == []
            assert soul_db.scalars(
                select(ExperienceClusterState).where(ExperienceClusterState.user_id == 1)
            ).all() == []
            assert soul_db.scalars(
                select(AgentSkill).where(AgentSkill.user_id == 1)
            ).all() == []
            assert len(
                soul_db.scalars(select(MemoryItem).where(MemoryItem.user_id == 2)).all()
            ) == 1
            assert len(
                soul_db.scalars(
                    select(MemoryItemEvidence).where(MemoryItemEvidence.user_id == 2)
                ).all()
            ) == 1
            assert len(
                soul_db.scalars(
                    select(UserProfileField).where(UserProfileField.user_id == 2)
                ).all()
            ) == 1
            assert len(
                soul_db.scalars(
                    select(UserProfileFieldEvidence).where(
                        UserProfileFieldEvidence.user_id == 2
                    )
                ).all()
            ) == 1
            assert len(
                soul_db.scalars(
                    select(ForesightSignal).where(ForesightSignal.user_id == 2)
                ).all()
            ) == 1
            assert len(
                soul_db.scalars(
                    select(AgentExperience).where(AgentExperience.user_id == 2)
                ).all()
            ) == 1
            assert len(
                soul_db.scalars(
                    select(ExperienceClusterState).where(
                        ExperienceClusterState.user_id == 2
                    )
                ).all()
            ) == 1
            assert len(
                soul_db.scalars(
                    select(AgentSkill).where(AgentSkill.user_id == 2)
                ).all()
            ) == 1
            assert runtime_db.scalars(
                select(RuntimeThread).where(RuntimeThread.user_id == 1)
            ).all() == []
            assert runtime_db.scalars(
                select(ProfileUpdateCandidate).where(ProfileUpdateCandidate.user_id == 1)
            ).all() == []
            assert runtime_db.scalars(
                select(RuntimeDocument).where(RuntimeDocument.user_id == 1)
            ).all() == []
            assert runtime_db.scalars(
                select(RuntimeDocumentChunk).where(RuntimeDocumentChunk.user_id == 1)
            ).all() == []
            assert runtime_db.scalars(
                select(RuntimeWorkflowRun).where(RuntimeWorkflowRun.user_id == 1)
            ).all() == []
            assert runtime_db.scalars(
                select(RuntimeWorkflowCheckpoint).where(
                    RuntimeWorkflowCheckpoint.workflow_run_id == 101
                )
            ).all() == []
            assert runtime_db.scalars(
                select(AffectStateRow).where(AffectStateRow.user_id == 1)
            ).all() == []
            assert len(
                runtime_db.scalars(
                    select(AffectStateRow).where(AffectStateRow.user_id == 2)
                ).all()
            ) == 1
            assert len(
                runtime_db.scalars(
                    select(RuntimeThread).where(RuntimeThread.user_id == 2)
                ).all()
            ) == 1
            assert len(
                runtime_db.scalars(
                    select(ProfileUpdateCandidate).where(
                        ProfileUpdateCandidate.user_id == 2
                    )
                ).all()
            ) == 1
            assert len(
                runtime_db.scalars(
                    select(RuntimeDocument).where(RuntimeDocument.user_id == 2)
                ).all()
            ) == 1
            assert len(
                runtime_db.scalars(
                    select(RuntimeDocumentChunk).where(RuntimeDocumentChunk.user_id == 2)
                ).all()
            ) == 1
            assert len(
                runtime_db.scalars(
                    select(RuntimeWorkflowRun).where(RuntimeWorkflowRun.user_id == 2)
                ).all()
            ) == 1
            assert len(
                runtime_db.scalars(
                    select(RuntimeWorkflowCheckpoint).where(
                        RuntimeWorkflowCheckpoint.workflow_run_id == 201
                    )
                ).all()
            ) == 1
    finally:
        engine.dispose()
