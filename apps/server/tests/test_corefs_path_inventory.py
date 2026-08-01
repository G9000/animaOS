from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_server_runtime_defaults_have_no_portable_core_writer_fallbacks() -> None:
    main_source = (
        REPO_ROOT / "apps/server/src/anima_server/main.py"
    ).read_text(encoding="utf-8")
    config_source = (
        REPO_ROOT / "apps/server/src/anima_server/config.py"
    ).read_text(encoding="utf-8")
    health_source = (
        REPO_ROOT / "apps/server/src/anima_server/services/health/event_logger.py"
    ).read_text(encoding="utf-8")

    assert 'settings.data_dir / "runtime" / "pg_data"' not in main_source
    assert "return settings.data_dir / RUNTIME_SETTINGS_FILENAME" not in config_source
    assert 'settings.data_dir / "logs"' not in health_source


def test_desktop_daemon_default_never_falls_back_under_dot_anima() -> None:
    tauri_source = (
        REPO_ROOT / "apps/desktop/src-tauri/src/lib.rs"
    ).read_text(encoding="utf-8")

    assert 'join(".anima").join("runtime-daemon")' not in tauri_source
