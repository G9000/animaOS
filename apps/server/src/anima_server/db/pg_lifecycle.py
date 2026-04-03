from __future__ import annotations

import atexit
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PG_START_RETRIES = 3
_PG_RETRY_DELAY = 3.0


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
        self._clear_stale_log()

        import pgserver

        last_err: Exception | None = None
        for attempt in range(1, _PG_START_RETRIES + 1):
            try:
                self._server = pgserver.get_server(
                    str(self._data_dir), cleanup_mode="stop")
                self._started = True
                logger.info("Embedded PostgreSQL started in %s",
                            self._data_dir)
                return
            except Exception as exc:
                last_err = exc
                logger.warning(
                    "Embedded PostgreSQL start attempt %d/%d failed: %s",
                    attempt, _PG_START_RETRIES, exc,
                )
                if attempt < _PG_START_RETRIES:
                    # A timed-out pg_ctl start leaves postgres running in the
                    # background (e.g. doing crash recovery).  We must stop it
                    # before retrying, otherwise the lockfile is valid, the log
                    # file is locked, and the next attempt will also fail.
                    self._force_stop_pg()
                    self._recover_stale_lockfile()
                    self._clear_stale_log()
                    time.sleep(_PG_RETRY_DELAY)

        raise RuntimeError(
            f"Embedded PostgreSQL failed to start after {_PG_START_RETRIES} attempts"
        ) from last_err

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

    def _force_stop_pg(self) -> None:
        """Attempt to stop a running postgres instance via pg_ctl stop.

        After a timed-out start, the postgres process may still be alive
        (doing crash recovery).  This asks it to shut down so the next
        start attempt gets a clean slate.
        """
        try:
            from pgserver._commands import POSTGRES_BIN_PATH
            pg_ctl = str(POSTGRES_BIN_PATH /
                         ("pg_ctl.exe" if os.name == "nt" else "pg_ctl"))
        except (ImportError, AttributeError):
            pg_ctl = "pg_ctl"

        try:
            subprocess.run(
                [pg_ctl, "-D", str(self._data_dir), "-m",
                 "fast", "-w", "stop"],
                timeout=15,
                capture_output=True,
                text=True,
            )
            logger.info("Stopped zombie PostgreSQL process in %s",
                        self._data_dir)
        except subprocess.TimeoutExpired:
            # pg_ctl stop itself timed out — try immediate mode
            try:
                subprocess.run(
                    [pg_ctl, "-D", str(self._data_dir), "-m",
                     "immediate", "-w", "stop"],
                    timeout=10,
                    capture_output=True,
                    text=True,
                )
                logger.info(
                    "Stopped zombie PostgreSQL (immediate) in %s", self._data_dir)
            except Exception as exc:
                logger.warning("Could not stop zombie PostgreSQL: %s", exc)
        except Exception as exc:
            logger.debug(
                "pg_ctl stop returned: %s (may not have been running)", exc)

    def _recover_stale_lockfile(self) -> None:
        """Remove a stale postmaster.pid whose process no longer exists."""
        pid_file = self._data_dir / "postmaster.pid"
        if not pid_file.exists():
            return

        try:
            pid = int(pid_file.read_text(encoding="utf-8").splitlines()[0])
        except (ValueError, IndexError):
            logger.warning(
                "Malformed postmaster.pid found at %s, removing", pid_file)
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

    def _clear_stale_log(self) -> None:
        """Truncate or remove the PG log file to prevent sharing-violation hangs.

        After an unclean shutdown on Windows, antivirus or backup software may
        hold the log file open, causing pg_ctl to hang when it tries to open
        it for writing.  Removing or truncating the file before start avoids
        this.
        """
        log_file = self._data_dir / "log"
        if not log_file.exists():
            return
        try:
            log_file.write_text("", encoding="utf-8")
            logger.debug("Truncated stale PG log at %s", log_file)
        except OSError:
            try:
                log_file.unlink()
                logger.debug("Removed stale PG log at %s", log_file)
            except OSError as exc:
                logger.warning(
                    "Could not clear stale PG log at %s: %s", log_file, exc)

    @staticmethod
    def to_sync_url(url: str) -> str:
        """Convert any PostgreSQL URL to ``postgresql+psycopg://`` format."""
        if "+psycopg" in url:
            return url
        if "+asyncpg" in url:
            return url.replace("+asyncpg", "+psycopg", 1)
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
