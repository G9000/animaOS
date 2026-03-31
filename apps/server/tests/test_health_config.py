# apps/server/tests/test_health_config.py
from __future__ import annotations


def test_health_settings_defaults():
    from anima_server.config import Settings

    s = Settings(
        _env_file=None,
        core_require_encryption=False,
    )
    assert s.health_log_dir == ""
    assert s.health_log_retention_days == 7
    assert s.health_log_level == "info"
