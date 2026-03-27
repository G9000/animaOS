from __future__ import annotations

import atexit
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class EmbeddedPG:
    """Manage an embedded PostgreSQL instance."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._server: Any | None = None
        self._started = False
        atexit.register(self.stop)

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    @property
    def running(self) -> bool:
        return self._started and self._server is not None

    @property
    def database_url(self) -> str:
        """Return the raw connection URL for the running instance."""
        if not self.running:
            raise RuntimeError("Embedded PG is not running")
        return self._server.get_uri()

    def start(self) -> None:
        """Start the embedded PostgreSQL instance."""
        if self.running:
            return

        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._recover_stale_lockfile()

        import pgserver

        self._server = pgserver.get_server(str(self._data_dir), cleanup_mode="stop")
        self._started = True
        logger.info("Embedded PostgreSQL started in %s", self._data_dir)

    def stop(self) -> None:
        """Stop the embedded PostgreSQL instance cleanly."""
        server = self._server
        self._server = None
        self._started = False

        if server is None:
            return

        cleanup = getattr(server, "cleanup", None)
        stop = getattr(server, "stop", None)
        if callable(cleanup):
            cleanup()
        elif callable(stop):
            stop()

        logger.info("Embedded PostgreSQL stopped")

    def _recover_stale_lockfile(self) -> None:
        """Remove a stale postmaster.pid whose process no longer exists."""
        pid_file = self._data_dir / "postmaster.pid"
        if not pid_file.exists():
            return

        try:
            pid = int(pid_file.read_text(encoding="utf-8").splitlines()[0])
        except (ValueError, IndexError):
            logger.warning("Malformed postmaster.pid found at %s, removing", pid_file)
            pid_file.unlink(missing_ok=True)
            return

        try:
            os.kill(pid, 0)
        except PermissionError:
            # Process exists but is owned by another user — leave the
            # lockfile in place.  (Must come before the generic OSError
            # handler because PermissionError is a subclass of OSError.)
            logger.info(
                "postmaster.pid points to PID %d that exists but is owned by another user; "
                "leaving lockfile in place.",
                pid,
            )
        except (ProcessLookupError, OSError):
            # ProcessLookupError: PID does not exist (Unix).
            # OSError: PID does not exist or is invalid (Windows raises
            #          OSError / WinError instead of ProcessLookupError).
            logger.warning(
                "Stale postmaster.pid found (PID %d not running), removing",
                pid,
            )
            pid_file.unlink(missing_ok=True)

    @staticmethod
    def to_sync_url(url: str) -> str:
        """Convert any PostgreSQL URL to ``postgresql+psycopg://`` format."""
        if "+psycopg" in url:
            return url
        if "+asyncpg" in url:
            return url.replace("+asyncpg", "+psycopg", 1)
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
