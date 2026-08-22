from __future__ import annotations

import json
from pathlib import Path

from anima_server.config import settings
from anima_server.db.session import get_user_session_factory
from anima_server.models import DiscordLink, MemoryEpisode, SelfModelBlock, TelegramLink
from anima_server.services.data_crypto import df
from anima_server.services.regeneration_work import regeneration_work_ids
from conftest import managed_test_client
from sqlalchemy import select


def test_unlock_migrates_device_and_runtime_state_out_of_portable_soul() -> None:
    with managed_test_client("pcf007-device-state-") as client:
        registered = client.post(
            "/api/auth/register",
            json={
                "username": "device-state",
                "password": "pw123456",
                "name": "Device State",
            },
        )
        assert registered.status_code == 201, registered.text
        payload = registered.json()
        user_id = int(payload["id"])
        headers = {"x-anima-unlock": str(payload["unlockToken"])}
        legacy_soul = settings.data_dir / "users" / str(user_id) / "soul.md"
        legacy_soul.parent.mkdir(parents=True, exist_ok=True)
        legacy_soul.write_text("Preserve this exact legacy directive.", encoding="utf-8")

        with get_user_session_factory(user_id)() as db:
            episode = MemoryEpisode(
                user_id=user_id,
                date="2026-08-13",
                summary="Disposable regeneration source",
                significance_score=3,
                needs_regeneration=True,
            )
            block = SelfModelBlock(
                user_id=user_id,
                section="growth_log",
                content="Disposable regeneration block",
                needs_regeneration=True,
            )
            db.add_all(
                [
                    episode,
                    block,
                    TelegramLink(chat_id=813001, user_id=user_id),
                    DiscordLink(channel_id="channel-813", user_id=user_id),
                ]
            )
            db.commit()
            episode_id = int(episode.id)
            block_id = int(block.id)

        logged_out = client.post("/api/auth/logout", headers=headers)
        assert logged_out.status_code == 200, logged_out.text
        response = client.post(
            "/api/auth/login",
            json={"username": "device-state", "password": "pw123456"},
        )
        assert response.status_code == 200, response.text
        assert not legacy_soul.exists()

        with get_user_session_factory(user_id)() as db:
            episode = db.get(MemoryEpisode, episode_id)
            block = db.get(SelfModelBlock, block_id)
            assert episode is not None and episode.needs_regeneration is False
            assert block is not None and block.needs_regeneration is False
            directive = db.scalar(
                select(SelfModelBlock).where(
                    SelfModelBlock.user_id == user_id,
                    SelfModelBlock.section == "user_directive",
                )
            )
            assert directive is not None
            assert "Preserve this exact legacy directive." in df(
                user_id,
                directive.content,
                table="self_model_blocks",
                field="content",
            )
            assert db.scalars(select(TelegramLink)).all() == []
            assert db.scalars(select(DiscordLink)).all() == []

        assert episode_id in regeneration_work_ids(
            user_id=user_id,
            kind="memory_episode",
        )
        assert block_id in regeneration_work_ids(
            user_id=user_id,
            kind="self_model_block",
        )
        instance_root = Path(settings.runtime_instance_data_dir)
        links = json.loads(
            (instance_root / "config" / "integration-links.json").read_text(
                encoding="utf-8"
            )
        )
        assert links["links"] == [
            {"externalId": "channel-813", "provider": "discord", "userId": user_id},
            {"externalId": "813001", "provider": "telegram", "userId": user_id},
        ]
        assert not instance_root.is_relative_to(settings.data_dir)
