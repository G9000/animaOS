"""Shared client helpers for AnimaOS eval runners."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_HTTP_BASE_URL = "http://127.0.0.1:3031"
DEFAULT_EVAL_USERNAME = "eval"
DEFAULT_EVAL_PASSWORD = "eval-password"
DEFAULT_EVAL_DISPLAY_NAME = "Eval User"


@dataclass(frozen=True, slots=True)
class EvalUserSession:
    user_id: int
    unlock_token: str


def response_snippet(response: httpx.Response, *, limit: int = 400) -> str:
    text = response.text.strip()
    return text[:limit] if text else "<empty>"


def raise_unexpected_status(action: str, response: httpx.Response) -> None:
    raise RuntimeError(
        f"{action} failed with HTTP {response.status_code}: {response_snippet(response)}"
    )


class SessionBoundAnimaClient:
    """An AnimaOS API client bound to one unlocked user session."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        session: EvalUserSession,
        *,
        reset_endpoint: str = "/api/chat/reset",
    ) -> None:
        self._client = client
        self._session = session
        self._reset_endpoint = reset_endpoint

    @property
    def user_id(self) -> int:
        return self._session.user_id

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-anima-unlock": self._session.unlock_token,
        }

    async def send_message_data(
        self,
        text: str,
        thread_id: int | None = None,
    ) -> dict[str, object]:
        payload: dict[str, Any] = {"message": text, "userId": self._session.user_id}
        if thread_id is not None:
            payload["threadId"] = thread_id

        response = await self._client.post(
            "/api/chat",
            json=payload,
            headers=self._headers(),
        )
        if response.status_code != 200:
            raise_unexpected_status("Chat request", response)

        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("Chat response was not a JSON object.")
        reply = str(data.get("response") or data.get("message") or "").strip()
        if not reply:
            raise RuntimeError("Chat response did not include assistant text.")
        return data

    async def send_message(self, text: str, thread_id: int | None = None) -> str:
        data = await self.send_message_data(text, thread_id=thread_id)
        reply = str(data.get("response") or data.get("message") or "").strip()
        return reply

    async def reset_memory(self) -> None:
        response = await self._client.post(
            self._reset_endpoint,
            json={"userId": self._session.user_id},
            headers=self._headers(),
        )
        if response.status_code != 200:
            if self._reset_endpoint == "/api/eval/reset":
                raise RuntimeError(
                    "Eval reset is unavailable. Run against a disposable eval server "
                    "with ANIMA_EVAL_RESET_ENABLED=true, or use in-process eval mode. "
                    f"Server response: HTTP {response.status_code}: {response_snippet(response)}"
                )
            raise_unexpected_status("Chat reset", response)

    async def trigger_consolidation(self) -> None:
        response = await self._client.post(
            "/api/chat/consolidate",
            json={"userId": self._session.user_id},
            headers=self._headers(),
        )
        if response.status_code != 200:
            raise_unexpected_status("Chat consolidation", response)

    async def import_transcript_sessions(
        self,
        sessions: list[dict[str, object]],
        *,
        extraction_mode: str = "llm_pairs",
        embed_raw_chunks: bool = False,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "userId": self._session.user_id,
            "sessions": sessions,
        }
        if extraction_mode:
            payload["extractionMode"] = extraction_mode
        if embed_raw_chunks:
            payload["embedRawChunks"] = True

        response = await self._client.post(
            "/api/eval/import-transcript",
            json=payload,
            headers=self._headers(),
        )
        if response.status_code != 200:
            raise_unexpected_status("Eval transcript import", response)
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("Eval transcript import response was not a JSON object.")
        return data


class HttpAnimaClient:
    """Client for benchmark scripts that run against a live AnimaOS server."""

    def __init__(
        self,
        base_url: str = DEFAULT_HTTP_BASE_URL,
        *,
        username: str | None = None,
        password: str | None = None,
        user_id: int | None = None,
        create_user: bool = False,
        display_name: str = DEFAULT_EVAL_DISPLAY_NAME,
        reset_endpoint: str = "/api/eval/reset",
        timeout: float = 600.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._username = username
        self._password = password
        self._user_id = user_id
        self._create_user = create_user
        self._display_name = display_name
        self._reset_endpoint = reset_endpoint
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
        )
        self._session_client: SessionBoundAnimaClient | None = None

    async def _ensure_session_client(self) -> SessionBoundAnimaClient:
        if self._session_client is not None:
            return self._session_client

        if not self._username or not self._password:
            raise RuntimeError("HTTP mode requires --username and --password.")

        login_response = await self._client.post(
            "/api/auth/login",
            json={"username": self._username, "password": self._password},
        )
        if login_response.status_code == 200:
            session = self._session_from_auth_payload(login_response.json())
        elif self._create_user:
            session = await self._register_eval_user()
        else:
            raise_unexpected_status("Login", login_response)

        self._session_client = SessionBoundAnimaClient(
            self._client,
            session,
            reset_endpoint=self._reset_endpoint,
        )
        return self._session_client

    def _session_from_auth_payload(self, data: dict[str, object]) -> EvalUserSession:
        unlock_token = str(data.get("unlockToken") or "").strip()
        if not unlock_token:
            raise RuntimeError("Auth response did not include unlockToken.")

        resolved_user_id = self._user_id
        if resolved_user_id is None:
            raw_id = data.get("id")
            if raw_id is None:
                raise RuntimeError("Auth response did not include user id.")
            resolved_user_id = int(raw_id)

        return EvalUserSession(user_id=resolved_user_id, unlock_token=unlock_token)

    async def _register_eval_user(self) -> EvalUserSession:
        assert self._username is not None
        assert self._password is not None
        response = await self._client.post(
            "/api/auth/register",
            json={
                "username": self._username,
                "password": self._password,
                "name": self._display_name,
            },
        )
        if response.status_code not in {200, 201}:
            raise_unexpected_status("Register eval user", response)
        return self._session_from_auth_payload(response.json())

    async def send_message(self, text: str, thread_id: int | None = None) -> str:
        session_client = await self._ensure_session_client()
        return await session_client.send_message(text, thread_id=thread_id)

    async def send_message_data(
        self,
        text: str,
        thread_id: int | None = None,
    ) -> dict[str, object]:
        session_client = await self._ensure_session_client()
        return await session_client.send_message_data(text, thread_id=thread_id)

    async def reset_memory(self) -> None:
        session_client = await self._ensure_session_client()
        await session_client.reset_memory()

    async def trigger_consolidation(self) -> None:
        session_client = await self._ensure_session_client()
        await session_client.trigger_consolidation()

    async def import_transcript_sessions(
        self,
        sessions: list[dict[str, object]],
        *,
        extraction_mode: str = "llm_pairs",
        embed_raw_chunks: bool = False,
    ) -> dict[str, object]:
        session_client = await self._ensure_session_client()
        return await session_client.import_transcript_sessions(
            sessions,
            extraction_mode=extraction_mode,
            embed_raw_chunks=embed_raw_chunks,
        )

    async def close(self) -> None:
        await self._client.aclose()
