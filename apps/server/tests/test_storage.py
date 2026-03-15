"""Tests for the storage utility module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from anima_server.services.storage import get_user_data_dir


def test_get_user_data_dir_returns_correct_path() -> None:
    with patch("anima_server.services.storage.settings") as mock_settings:
        mock_settings.data_dir = Path("/tmp/anima-data")
        result = get_user_data_dir(42)
        assert result == Path("/tmp/anima-data/users/42")


def test_get_user_data_dir_different_users() -> None:
    with patch("anima_server.services.storage.settings") as mock_settings:
        mock_settings.data_dir = Path("/data")
        path_1 = get_user_data_dir(1)
        path_2 = get_user_data_dir(2)
        assert path_1 != path_2
        assert path_1 == Path("/data/users/1")
        assert path_2 == Path("/data/users/2")
