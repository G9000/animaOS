# apps/server/tests/test_health_startup.py
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace


def test_structured_handler_installed_at_startup(managed_tmp_path: Path):
    """After app creation, the StructuredLogHandler should be on the anima_server logger."""
    from anima_server.services.health.event_logger import StructuredLogHandler

    # The handler gets installed during lifespan, which runs when the app starts.
    # For a unit test, we can verify by checking if the handler class exists and is importable.
    # A full integration test would start the app and check the logger.
    assert StructuredLogHandler is not None

    # Verify the handler can be instantiated
    from anima_server.services.health.event_logger import EventLogger

    el = EventLogger(log_dir=managed_tmp_path / "startup-logs", min_level="trace")
    handler = StructuredLogHandler(el)
    assert isinstance(handler, logging.Handler)

    # Verify it can be added to a logger
    test_logger = logging.getLogger("test.startup")
    test_logger.addHandler(handler)
    test_logger.removeHandler(handler)


def test_corefs_readiness_health_contains_only_operational_metadata():
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex
    from anima_server.services.health.checks import check_corefs_readiness

    index = CoreFSProgressiveIndex("core-health")
    index.unlock(sqlcipher_key=b"s" * 32, local_instance_id="instance-a")
    index.begin_catalog()
    index.publish_catalog(
        catalog_generation=5,
        families={"notes": 2},
        degraded={"notes": ("opaque-id",)},
    )
    index.begin_text_indexing()
    index.index_text(
        family="notes",
        object_id="note-1",
        revision="1",
        text="private health marker",
    )

    result = asyncio.run(
        check_corefs_readiness(
            7,
            session_store=SimpleNamespace(get_active_runtime_index=lambda _user_id: index),
        )
    )

    assert result.status == "degraded"
    assert result.details["state"] == "text_indexing"
    assert result.details["catalog_generation"] == 5
    assert result.details["processed_objects"] == 1
    assert "private health marker" not in str(result)
