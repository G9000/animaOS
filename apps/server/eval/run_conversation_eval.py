"""Run a lightweight conversational behavior eval against AnimaOS.

This suite is intentionally small and targeted. It checks whether replies stay
socially sane across a few opener and relationship-stage scenarios such as:

- first contact greetings
- first contact identity questions
- rough-day support
- light flirtation
- familiar-stage continuity

Usage:
    python run_conversation_eval.py --mode in-process
    python run_conversation_eval.py --mode http --base-url http://127.0.0.1:3031
  python run_conversation_eval.py --stage first_contact
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx

DATA_DIR = Path(__file__).parent / "data"
RESULTS_DIR = Path(__file__).parent / "results"
CASE_FILE = DATA_DIR / "conversation_eval_cases.json"
DEFAULT_HTTP_BASE_URL = "http://127.0.0.1:3031"

# In-process eval imports the server app directly. Disable the default
# encryption requirement for those ephemeral temporary databases.
os.environ.setdefault("ANIMA_CORE_REQUIRE_ENCRYPTION", "false")


@dataclass(frozen=True, slots=True)
class ConversationEvalCase:
    case_id: str
    stage: str
    description: str
    prompt: str
    prelude: tuple[str, ...] = ()
    required_any_of: tuple[str, ...] = ()
    forbidden_all_of: tuple[str, ...] = ()
    max_words: int | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> ConversationEvalCase:
        return cls(
            case_id=str(raw["id"]),
            stage=str(raw["stage"]),
            description=str(raw["description"]),
            prompt=str(raw["prompt"]),
            prelude=tuple(str(item) for item in raw.get("prelude", [])),
            required_any_of=tuple(
                str(item) for item in raw.get("required_any_of", [])
            ),
            forbidden_all_of=tuple(
                str(item) for item in raw.get("forbidden_all_of", [])
            ),
            max_words=int(raw["max_words"]) if raw.get(
                "max_words") is not None else None,
        )


@dataclass(frozen=True, slots=True)
class EvalUserSession:
    user_id: int
    unlock_token: str


def _response_snippet(response: httpx.Response, *, limit: int = 400) -> str:
    text = response.text.strip()
    return text[:limit] if text else "<empty>"


def _raise_unexpected_status(action: str, response: httpx.Response) -> None:
    raise RuntimeError(
        f"{action} failed with HTTP {response.status_code}: {_response_snippet(response)}"
    )


def _sanitize_case_id(case_id: str) -> str:
    sanitized = re.sub(r"[^a-z0-9]+", "-", case_id.lower()).strip("-")
    return sanitized[:24] or "case"


class SessionBoundAnimaClient:
    def __init__(self, client: httpx.AsyncClient, session: EvalUserSession):
        self._client = client
        self._session = session

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-anima-unlock": self._session.unlock_token,
        }

    async def send_message(self, text: str) -> str:
        response = await self._client.post(
            "/api/chat",
            json={"message": text, "userId": self._session.user_id},
            headers=self._headers(),
        )
        if response.status_code != 200:
            _raise_unexpected_status("Chat request", response)

        data = response.json()
        reply = str(data.get("response") or data.get("message") or "").strip()
        if not reply:
            raise RuntimeError("Chat response did not include assistant text.")
        return reply

    async def reset_memory(self) -> None:
        response = await self._client.post(
            "/api/chat/reset",
            json={"userId": self._session.user_id},
            headers=self._headers(),
        )
        if response.status_code != 200:
            _raise_unexpected_status("Chat reset", response)

    async def trigger_consolidation(self) -> None:
        response = await self._client.post(
            "/api/chat/consolidate",
            json={"userId": self._session.user_id},
            headers=self._headers(),
        )
        if response.status_code != 200:
            _raise_unexpected_status("Chat consolidation", response)


class HttpAnimaClient:
    def __init__(
        self,
        base_url: str,
        *,
        username: str | None,
        password: str | None,
        user_id: int | None,
        timeout: float = 120.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._username = username
        self._password = password
        self._user_id = user_id
        self._client = httpx.AsyncClient(
            base_url=self.base_url, timeout=timeout)
        self._session_client: SessionBoundAnimaClient | None = None

    async def _ensure_session_client(self) -> SessionBoundAnimaClient:
        if self._session_client is not None:
            return self._session_client

        if not self._username or not self._password:
            raise RuntimeError(
                "HTTP mode requires --username and --password for a real unlocked session."
            )

        response = await self._client.post(
            "/api/auth/login",
            json={"username": self._username, "password": self._password},
        )
        if response.status_code != 200:
            _raise_unexpected_status("Login", response)

        data = response.json()
        unlock_token = str(data.get("unlockToken") or "").strip()
        if not unlock_token:
            raise RuntimeError("Login response did not include unlockToken.")
        resolved_user_id = self._user_id if self._user_id is not None else int(
            data["id"])
        self._session_client = SessionBoundAnimaClient(
            self._client,
            EvalUserSession(user_id=resolved_user_id,
                            unlock_token=unlock_token),
        )
        return self._session_client

    async def send_message(self, text: str) -> str:
        session_client = await self._ensure_session_client()
        return await session_client.send_message(text)

    async def reset_memory(self) -> None:
        session_client = await self._ensure_session_client()
        await session_client.reset_memory()

    async def trigger_consolidation(self) -> None:
        session_client = await self._ensure_session_client()
        await session_client.trigger_consolidation()

    async def close(self) -> None:
        await self._client.aclose()


def _ensure_server_import_path() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    server_src = repo_root / "apps" / "server" / "src"
    server_src_str = str(server_src)
    if server_src_str not in sys.path:
        sys.path.insert(0, server_src_str)


def _ensure_sqlite_biginteger_support() -> None:
    if getattr(_ensure_sqlite_biginteger_support, "_configured", False):
        return

    from sqlalchemy import BigInteger
    from sqlalchemy.ext.compiler import compiles

    @compiles(BigInteger, "sqlite")
    def _compile_biginteger_sqlite(type_: BigInteger, compiler: object, **kw: object) -> str:
        del type_, compiler, kw
        return "INTEGER"

    _ensure_sqlite_biginteger_support._configured = True


async def _register_eval_user(
    client: httpx.AsyncClient,
    *,
    case_id: str,
) -> EvalUserSession:
    username = f"eval-{_sanitize_case_id(case_id)}-{uuid4().hex[:8]}"
    password = f"pw-{uuid4().hex}"
    response = await client.post(
        "/api/auth/register",
        json={
            "username": username,
            "password": password,
            "name": "Eval User",
        },
    )
    if response.status_code not in {200, 201}:
        _raise_unexpected_status("Register", response)

    data = response.json()
    unlock_token = str(data.get("unlockToken") or "").strip()
    if not unlock_token:
        raise RuntimeError("Register response did not include unlockToken.")
    return EvalUserSession(user_id=int(data["id"]), unlock_token=unlock_token)


@asynccontextmanager
async def build_in_process_client(
    case_id: str,
    *,
    timeout: float,
) -> AsyncGenerator[SessionBoundAnimaClient, None]:
    _ensure_server_import_path()
    _ensure_sqlite_biginteger_support()

    import anima_server.main as main_module
    from anima_server.config import settings
    from anima_server.db import dispose_cached_engines
    from anima_server.db import runtime as runtime_mod
    from anima_server.db.runtime_base import RuntimeBase
    from anima_server.models import runtime as _runtime_models  # noqa: F401
    from anima_server.models import (
        runtime_consciousness as _runtime_consciousness_models,  # noqa: F401
    )
    from anima_server.models import runtime_memory as _runtime_memory_models  # noqa: F401
    from anima_server.services.agent import invalidate_agent_runtime_cache
    from anima_server.services.agent.vector_store import reset_vector_store
    from anima_server.services.sessions import clear_sqlcipher_key, unlock_session_store
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    temp_root = Path(tempfile.mkdtemp(prefix="anima-conversation-eval-"))
    original_data_dir = settings.data_dir
    settings.data_dir = temp_root / "anima-data"

    dispose_cached_engines()
    unlock_session_store.clear()
    clear_sqlcipher_key()
    reset_vector_store()
    invalidate_agent_runtime_cache()

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection: object, connection_record: object) -> None:
        del connection_record
        cursor = dbapi_connection.cursor()  # type: ignore[union-attr]
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA busy_timeout = 30000")
        cursor.close()

    RuntimeBase.metadata.create_all(engine)
    runtime_mod._runtime_engine = engine
    runtime_mod._runtime_session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )

    patcher = patch.object(
        main_module, "_start_embedded_pg", return_value=None)
    app = None
    lifespan_cm = None
    client: httpx.AsyncClient | None = None

    try:
        patcher.start()
        app = main_module.create_app()
        lifespan_cm = app.router.lifespan_context(app)
        await lifespan_cm.__aenter__()

        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://anima.test",
            timeout=timeout,
        )
        session = await _register_eval_user(client, case_id=case_id)
        yield SessionBoundAnimaClient(client, session)
    finally:
        if client is not None:
            await client.aclose()
        if lifespan_cm is not None:
            await lifespan_cm.__aexit__(None, None, None)
        patcher.stop()

        runtime_mod._runtime_session_factory = None
        runtime_mod._runtime_engine = None
        engine.dispose()

        unlock_session_store.clear()
        clear_sqlcipher_key()
        reset_vector_store()
        dispose_cached_engines()
        settings.data_dir = original_data_dir
        invalidate_agent_runtime_cache()
        shutil.rmtree(temp_root, ignore_errors=True)


def load_cases(path: Path = CASE_FILE) -> list[ConversationEvalCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(
            "Conversation eval cases file must contain a JSON list.")
    return [ConversationEvalCase.from_dict(item) for item in raw]


def evaluate_response(case: ConversationEvalCase, response: str) -> dict[str, object]:
    word_count = len(re.findall(r"\b\w+\b", response))
    required_hits = [
        pattern for pattern in case.required_any_of if re.search(pattern, response, re.IGNORECASE)
    ]
    forbidden_hits = [
        pattern for pattern in case.forbidden_all_of if re.search(pattern, response, re.IGNORECASE)
    ]
    max_words_ok = case.max_words is None or word_count <= case.max_words
    passed = (
        (not case.required_any_of or bool(required_hits))
        and not forbidden_hits
        and max_words_ok
    )
    return {
        "passed": passed,
        "word_count": word_count,
        "required_hits": required_hits,
        "forbidden_hits": forbidden_hits,
        "max_words_ok": max_words_ok,
    }


def write_results(
    output_path: Path,
    *,
    mode: str,
    base_url: str | None,
    results: list[dict[str, object]],
    start_time: float,
) -> None:
    passed = sum(1 for item in results if item["evaluation"]["passed"])
    elapsed = time.time() - start_time
    summary = {
        "benchmark": "conversation_eval",
        "timestamp": datetime.now().isoformat(),
        "mode": mode,
        "base_url": base_url,
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "elapsed_seconds": round(elapsed, 2),
    }
    output = {"summary": summary, "results": results}
    output_path.write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def build_error_result(
    case: ConversationEvalCase,
    *,
    transcript: list[dict[str, str]],
    error: str,
) -> dict[str, object]:
    return {
        "id": case.case_id,
        "stage": case.stage,
        "description": case.description,
        "response": "",
        "transcript": transcript,
        "evaluation": {
            "passed": False,
            "word_count": 0,
            "required_hits": [],
            "forbidden_hits": [],
            "max_words_ok": case.max_words is None,
        },
        "error": error,
    }


async def run_case(
    client: SessionBoundAnimaClient | HttpAnimaClient,
    case: ConversationEvalCase,
    *,
    consolidate_prelude: bool,
) -> dict[str, object]:
    transcript: list[dict[str, str]] = []

    try:
        await client.reset_memory()

        for message in case.prelude:
            reply = await client.send_message(message)
            transcript.append({"user": message, "assistant": reply})

        if consolidate_prelude and case.prelude:
            await client.trigger_consolidation()
            await asyncio.sleep(0.25)

        response = await client.send_message(case.prompt)
        transcript.append({"user": case.prompt, "assistant": response})
    except Exception as exc:
        return build_error_result(case, transcript=transcript, error=str(exc))

    evaluation = evaluate_response(case, response)
    return {
        "id": case.case_id,
        "stage": case.stage,
        "description": case.description,
        "response": response,
        "transcript": transcript,
        "evaluation": evaluation,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the small conversation behavior eval")
    parser.add_argument(
        "--mode",
        choices=("http", "in-process"),
        default="http",
        help="Use an already running server over HTTP, or import the app directly.",
    )
    parser.add_argument("--base-url", default=DEFAULT_HTTP_BASE_URL)
    parser.add_argument("--username", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--stage", default=None)
    parser.add_argument("--case-id", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument(
        "--consolidate-prelude",
        action="store_true",
        help="Trigger /api/chat/consolidate after each case prelude.",
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    cases = load_cases()
    if args.stage:
        cases = [case for case in cases if case.stage == args.stage]
    if args.case_id:
        cases = [case for case in cases if case.case_id == args.case_id]
    if args.limit is not None:
        cases = cases[: args.limit]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = (
        Path(args.output)
        if args.output
        else RESULTS_DIR / "conversation_eval.json"
    )

    results: list[dict[str, object]] = []
    start = time.time()

    if args.mode == "http":
        client = HttpAnimaClient(
            args.base_url,
            username=args.username,
            password=args.password,
            user_id=args.user_id,
            timeout=args.timeout,
        )
        try:
            for index, case in enumerate(cases, start=1):
                result = await run_case(
                    client,
                    case,
                    consolidate_prelude=args.consolidate_prelude,
                )
                results.append(result)
                if result.get("error"):
                    status = "ERROR"
                else:
                    status = "PASS" if result["evaluation"]["passed"] else "FAIL"
                print(f"[{index}/{len(cases)}] {status} {case.case_id}")
                write_results(
                    output_path,
                    mode=args.mode,
                    base_url=args.base_url if args.mode == "http" else None,
                    results=results,
                    start_time=start,
                )
        finally:
            await client.close()
    else:
        for index, case in enumerate(cases, start=1):
            async with build_in_process_client(case.case_id, timeout=args.timeout) as client:
                result = await run_case(
                    client,
                    case,
                    consolidate_prelude=args.consolidate_prelude,
                )
            results.append(result)
            if result.get("error"):
                status = "ERROR"
            else:
                status = "PASS" if result["evaluation"]["passed"] else "FAIL"
            print(f"[{index}/{len(cases)}] {status} {case.case_id}")
            write_results(
                output_path,
                mode=args.mode,
                base_url=args.base_url if args.mode == "http" else None,
                results=results,
                start_time=start,
            )

    passed = sum(1 for item in results if item["evaluation"]["passed"])

    print(f"\nPassed: {passed}/{len(results)}")
    print(f"Results saved to: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
