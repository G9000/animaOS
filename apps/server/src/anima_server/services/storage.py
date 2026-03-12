from __future__ import annotations

from pathlib import Path

from anima_server.config import settings


def get_user_data_dir(user_id: int) -> Path:
    return settings.data_dir / "users" / str(user_id)
