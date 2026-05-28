from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from anima_server.api.routes import chat as chat_routes
from anima_server.config import settings
from anima_server.db.runtime import get_runtime_session_factory
from anima_server.models.runtime import RuntimeMessage
from anima_server.schemas.chat import ChatRequest, ChatRequestAttachment
from anima_server.services.agent import invalidate_agent_runtime_cache
from anima_server.services.agent.openai_compatible_client import OpenAICompatibleResponse
from anima_server.services.sessions import unlock_session_store
from conftest import managed_test_client
from fastapi.testclient import TestClient
from pydantic import ValidationError

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde"
)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _image_attachment(
    data: bytes = PNG_BYTES,
    *,
    mime_type: str = "image/png",
    filename: str = "pixel.png",
) -> dict[str, str]:
    return {
        "kind": "image",
        "filename": filename,
        "mimeType": mime_type,
        "data": _b64(data),
    }


@contextmanager
def _vision_agent_settings() -> Iterator[None]:
    original_provider = settings.agent_provider
    original_model = settings.agent_model
    original_base_url = settings.agent_base_url
    original_api_key = settings.agent_api_key

    try:
        settings.agent_provider = "openai"
        settings.agent_model = "gpt-4o-mini"
        settings.agent_base_url = "https://openai.test/v1"
        settings.agent_api_key = "test-key"
        invalidate_agent_runtime_cache()
        yield
    finally:
        settings.agent_provider = original_provider
        settings.agent_model = original_model
        settings.agent_base_url = original_base_url
        settings.agent_api_key = original_api_key
        invalidate_agent_runtime_cache()


def _register_user(client: TestClient, username: str = "vision-user") -> dict[str, object]:
    response = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "password": "pw123456",
            "name": "Vision User",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_chat_request_accepts_image_only_message() -> None:
    request = ChatRequest(
        message="",
        userId=7,
        attachments=[_image_attachment()],
    )

    assert request.message == ""
    assert len(request.attachments) == 1
    assert request.attachments[0].mimeType == "image/png"


def test_chat_request_rejects_empty_message_without_attachments() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(message="", userId=7)


def test_prepare_chat_attachments_saves_metadata_and_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent.attachments import prepare_chat_attachments

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    prepared = prepare_chat_attachments(
        user_id=7,
        attachments=[
            ChatRequestAttachment(
                kind="image",
                filename="pixel.png",
                mimeType="image/png",
                data=_b64(PNG_BYTES),
            )
        ],
    )

    assert len(prepared) == 1
    attachment = prepared[0]
    assert attachment.id.startswith("img_")
    assert attachment.kind == "image"
    assert attachment.mime_type == "image/png"
    assert attachment.filename == "pixel.png"
    assert attachment.size_bytes == len(PNG_BYTES)
    assert attachment.sha256
    assert attachment.storage_path.startswith("users/7/attachments/chat/img_")
    assert "storagePath" not in attachment.to_public_dict(message_id=123)
    assert (tmp_path / attachment.storage_path).read_bytes() == PNG_BYTES


def test_prepare_chat_attachments_uses_high_entropy_attachment_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import attachments as attachments_module

    requested_bytes: list[int] = []

    def fake_token_hex(num_bytes: int) -> str:
        requested_bytes.append(num_bytes)
        return "a" * (num_bytes * 2)

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(attachments_module.secrets, "token_hex", fake_token_hex)

    prepared = attachments_module.prepare_chat_attachments(
        user_id=7,
        attachments=[
            ChatRequestAttachment(
                kind="image",
                filename="pixel.png",
                mimeType="image/png",
                data=_b64(PNG_BYTES),
            )
        ],
    )

    assert requested_bytes == [8]
    assert prepared[0].id == "img_" + ("a" * 16)


def test_prepare_chat_attachments_rejects_mime_spoofing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent.attachments import (
        AttachmentValidationError,
        prepare_chat_attachments,
    )

    monkeypatch.setattr(settings, "data_dir", tmp_path)

    with pytest.raises(AttachmentValidationError, match="does not match"):
        prepare_chat_attachments(
            user_id=7,
            attachments=[
                ChatRequestAttachment(
                    kind="image",
                    filename="fake.png",
                    mimeType="image/png",
                    data=_b64(b"not a png"),
                )
            ],
        )


def test_prepare_chat_attachments_rejects_svg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent.attachments import (
        AttachmentValidationError,
        prepare_chat_attachments,
    )

    monkeypatch.setattr(settings, "data_dir", tmp_path)

    with pytest.raises(AttachmentValidationError, match="Unsupported image type"):
        prepare_chat_attachments(
            user_id=7,
            attachments=[
                ChatRequestAttachment(
                    kind="image",
                    filename="unsafe.svg",
                    mimeType="image/svg+xml",
                    data=_b64(b"<svg><script>alert(1)</script></svg>"),
                )
            ],
        )


def test_prepare_chat_attachments_rejects_too_large(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent.attachments import (
        AttachmentTooLargeError,
        prepare_chat_attachments,
    )

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "chat_image_max_size_bytes", 4)

    with pytest.raises(AttachmentTooLargeError, match="too large"):
        prepare_chat_attachments(
            user_id=7,
            attachments=[
                ChatRequestAttachment(
                    kind="image",
                    filename="pixel.png",
                    mimeType="image/png",
                    data=_b64(PNG_BYTES),
                )
            ],
        )


def test_prepare_chat_attachments_rejects_too_many(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent.attachments import (
        AttachmentValidationError,
        prepare_chat_attachments,
    )

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "chat_image_max_count", 1)

    with pytest.raises(AttachmentValidationError, match="at most 1 image"):
        prepare_chat_attachments(
            user_id=7,
            attachments=[
                ChatRequestAttachment(
                    kind="image",
                    filename="one.png",
                    mimeType="image/png",
                    data=_b64(PNG_BYTES),
                ),
                ChatRequestAttachment(
                    kind="image",
                    filename="two.png",
                    mimeType="image/png",
                    data=_b64(PNG_BYTES),
                ),
            ],
        )


def test_model_capability_helper_recognizes_vision_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.request

    from anima_server.services.agent.model_capabilities import supports_image_input

    def unavailable_ollama(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise OSError("offline")

    monkeypatch.setattr(urllib.request, "urlopen", unavailable_ollama)

    assert supports_image_input("openai", "gpt-4o-mini") is True
    assert supports_image_input("openrouter", "openai/gpt-4.1") is True
    assert supports_image_input("anthropic", "claude-haiku-4-5-20251001") is True
    assert supports_image_input("doubleword", "Qwen/Qwen3.6-35B-A3B-FP8") is True
    assert supports_image_input("doubleword", "Qwen/Qwen3-VL-30B-A3B-Instruct-FP8") is True
    assert supports_image_input("doubleword", "moonshotai/Kimi-K2.6") is True
    assert supports_image_input("ollama", "llama3.2-vision:11b") is True
    assert supports_image_input("ollama", "qwen2.5-vl:7b") is True
    assert supports_image_input("openai", "my-revision-model") is False
    assert supports_image_input("ollama", "llama3.2") is False


def test_ollama_model_capability_helper_reads_native_vision_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.request

    from anima_server.services.agent.model_capabilities import supports_image_input

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"capabilities":["completion","vision","tools"]}'

    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_urlopen(request: urllib.request.Request, *, timeout: float) -> FakeResponse:
        assert request.data is not None
        calls.append(
            (
                request.full_url,
                json.loads(request.data.decode("utf-8")),
            )
        )
        assert timeout <= 2.0
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    assert (
        supports_image_input(
            "ollama",
            "vaultbox/qwen3.5-uncensored:35b",
            base_url="http://ollama.test",
        )
        is True
    )
    assert calls == [
        (
            "http://ollama.test/api/show",
            {"name": "vaultbox/qwen3.5-uncensored:35b"},
        )
    ]


def test_image_attachment_support_uses_configured_agent_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent.service import ensure_image_attachments_supported

    original_provider = settings.agent_provider
    original_model = settings.agent_model
    original_base_url = settings.agent_base_url
    calls: list[tuple[str, str, str]] = []

    def fake_supports_image_input(provider: str, model: str, *, base_url: str = "") -> bool:
        calls.append((provider, model, base_url))
        return True

    monkeypatch.setattr(
        "anima_server.services.agent.service.supports_image_input",
        fake_supports_image_input,
    )

    try:
        settings.agent_provider = "ollama"
        settings.agent_model = "vaultbox/qwen3.5-uncensored:35b"
        settings.agent_base_url = "http://ollama.test"

        ensure_image_attachments_supported(
            [
                ChatRequestAttachment(
                    kind="image",
                    filename="pixel.png",
                    mimeType="image/png",
                    data=_b64(PNG_BYTES),
                )
            ]
        )
    finally:
        settings.agent_provider = original_provider
        settings.agent_model = original_model
        settings.agent_base_url = original_base_url

    assert calls == [
        (
            "ollama",
            "vaultbox/qwen3.5-uncensored:35b",
            "http://ollama.test",
        )
    ]


def test_unsupported_model_rejects_images_before_message_persistence() -> None:
    original_provider = settings.agent_provider
    original_model = settings.agent_model

    try:
        settings.agent_provider = "ollama"
        settings.agent_model = "llama3.2"
        invalidate_agent_runtime_cache()

        with managed_test_client("vision-unsupported-") as client:
            user = _register_user(client, username="unsupported-vision")
            headers = {"x-anima-unlock": str(user["unlockToken"])}
            user_id = int(user["id"])

            response = client.post(
                "/api/chat",
                headers=headers,
                json={
                    "message": "what is this?",
                    "userId": user_id,
                    "attachments": [_image_attachment()],
                },
            )

            rt_factory = get_runtime_session_factory()
            session = rt_factory()
            try:
                message_count = session.query(RuntimeMessage).count()
            finally:
                session.close()
    finally:
        settings.agent_provider = original_provider
        settings.agent_model = original_model
        invalidate_agent_runtime_cache()

    assert response.status_code == 503
    assert "cannot process image attachments" in response.json()["error"]
    assert message_count == 0


def test_chat_history_returns_attachment_urls_and_file_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeVisionChatClient:
        def bind_tools(
            self,
            tools: list[object],
            *args: object,
            **kwargs: object,
        ) -> FakeVisionChatClient:
            del tools, args, kwargs
            return self

        async def ainvoke(self, input: list[object]) -> OpenAICompatibleResponse:
            assert input
            return OpenAICompatibleResponse(content="I can see it.")

    fake_client = FakeVisionChatClient()

    with _vision_agent_settings(), managed_test_client("vision-chat-") as client:
        monkeypatch.setattr(
            "anima_server.services.agent.adapters.openai_compatible.create_llm",
            lambda: fake_client,
        )
        user = _register_user(client, username="vision-history")
        headers = {"x-anima-unlock": str(user["unlockToken"])}
        user_id = int(user["id"])

        response = client.post(
            "/api/chat",
            headers=headers,
            json={
                "message": "what is this?",
                "userId": user_id,
                "attachments": [_image_attachment()],
            },
        )
        history = client.get(
            "/api/chat/history",
            headers=headers,
            params={"userId": user_id, "limit": 10},
        )

        assert response.status_code == 200
        assert history.status_code == 200
        user_message = history.json()[0]
        attachment = user_message["attachments"][0]
        file_response = client.get(attachment["url"], headers=headers)

    assert attachment["mimeType"] == "image/png"
    assert attachment["filename"] == "pixel.png"
    assert attachment["sizeBytes"] == len(PNG_BYTES)
    assert attachment["url"].startswith(
        f"/api/chat/messages/{user_message['id']}/attachments/img_"
    )
    assert "storagePath" not in attachment
    assert file_response.status_code == 200
    assert file_response.headers["content-type"].startswith("image/png")
    assert file_response.content == PNG_BYTES


def test_attachment_file_endpoint_enforces_user_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeVisionChatClient:
        def bind_tools(
            self,
            tools: list[object],
            *args: object,
            **kwargs: object,
        ) -> FakeVisionChatClient:
            del tools, args, kwargs
            return self

        async def ainvoke(self, input: list[object]) -> OpenAICompatibleResponse:
            assert input
            return OpenAICompatibleResponse(content="I can see it.")

    with _vision_agent_settings(), managed_test_client("vision-owner-") as client:
        monkeypatch.setattr(
            "anima_server.services.agent.adapters.openai_compatible.create_llm",
            lambda: FakeVisionChatClient(),
        )
        owner = _register_user(client, username="vision-owner")
        owner_headers = {"x-anima-unlock": str(owner["unlockToken"])}
        other_token = unlock_session_store.create(int(owner["id"]) + 1, {"memories": b"other"})
        other_headers = {"x-anima-unlock": other_token}

        client.post(
            "/api/chat",
            headers=owner_headers,
            json={
                "message": "what is this?",
                "userId": int(owner["id"]),
                "attachments": [_image_attachment()],
            },
        )
        history = client.get(
            "/api/chat/history",
            headers=owner_headers,
            params={"userId": int(owner["id"]), "limit": 10},
        )
        attachment_url = history.json()[0]["attachments"][0]["url"]
        forbidden = client.get(attachment_url, headers=other_headers)

    assert forbidden.status_code == 404


@pytest.mark.asyncio
async def test_chat_route_forwards_attachments_to_run_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def _fake_run_agent(*args: object, **kwargs: object):
        from anima_server.services.agent.state import AgentResult

        captured["args"] = args
        captured["kwargs"] = kwargs
        return AgentResult(response="ok", model="gpt-4o-mini", provider="openai")

    monkeypatch.setattr(chat_routes, "run_agent", _fake_run_agent)

    from anima_server.services.sessions import unlock_session_store
    from starlette.requests import Request

    token = unlock_session_store.create(42, {"memories": b"unit-test-dek"})
    request = Request(
        {
            "type": "http",
            "headers": [(b"x-anima-unlock", token.encode("utf-8"))],
        }
    )

    try:
        response = await chat_routes.send_message(
            ChatRequest(
                message="describe this",
                userId=42,
                attachments=[_image_attachment()],
            ),
            request,
            db=None,
            runtime_db=None,
        )
    finally:
        unlock_session_store.revoke(token)

    assert response.response == "ok"
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert len(kwargs["attachments"]) == 1
