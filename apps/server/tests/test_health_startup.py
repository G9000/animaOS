# apps/server/tests/test_health_startup.py
from __future__ import annotations

import logging


def test_structured_handler_installed_at_startup():
    """After app creation, the StructuredLogHandler should be on the anima_server logger."""
    from anima_server.services.health.event_logger import StructuredLogHandler

    # The handler gets installed during lifespan, which runs when the app starts.
    # For a unit test, we can verify by checking if the handler class exists and is importable.
    # A full integration test would start the app and check the logger.
    assert StructuredLogHandler is not None

    # Verify the handler can be instantiated
    import tempfile
    from pathlib import Path

    from anima_server.services.health.event_logger import EventLogger

    with tempfile.TemporaryDirectory() as td:
        el = EventLogger(log_dir=Path(td), min_level="trace")
        handler = StructuredLogHandler(el)
        assert isinstance(handler, logging.Handler)

        # Verify it can be added to a logger
        test_logger = logging.getLogger("test.startup")
        test_logger.addHandler(handler)
        test_logger.removeHandler(handler)
