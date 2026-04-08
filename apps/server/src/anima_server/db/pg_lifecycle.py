from __future__ import annotations

import atexit
import logging
import os
import subprocess
import sys
import time
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PG_START_RETRIES = 3
_PG_RETRY_DELAY = 3.0
_PGSERVER_WINDOWS_START_TIMEOUT = 60.0


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
        removed_stale_lockfile = self._recover_stale_lockfile()
        if removed_stale_lockfile:
            self._terminate_postgres_processes_for_data_dir()
        self._clear_stale_log()

        import pgserver
        self._patch_pgserver_windows_startup(pgserver)

        last_err: Exception | None = None
        for attempt in range(1, _PG_START_RETRIES + 1):
            try:
                self._discard_stale_pgserver_instance(pgserver)
                server = pgserver.get_server(
                    str(self._data_dir), cleanup_mode="stop")
                # pgserver keeps a process-global cache. Validate the returned
                # handle now so a half-started cached instance does not leak
                # into FastAPI startup and explode later on get_uri().
                server.get_uri()
                self._server = server
                self._started = True
                logger.info("Embedded PostgreSQL started in %s",
                            self._data_dir)
                return
            except Exception as exc:
                self._server = None
                self._started = False
                last_err = exc
                self._discard_stale_pgserver_instance(pgserver, force=True)
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
                    removed_stale_lockfile = self._recover_stale_lockfile()
                    if removed_stale_lockfile:
                        self._terminate_postgres_processes_for_data_dir()
                    self._clear_stale_log()
                    time.sleep(_PG_RETRY_DELAY)

        raise RuntimeError(
            f"Embedded PostgreSQL failed to start after {_PG_START_RETRIES} attempts"
        ) from last_err

    def _patch_pgserver_windows_startup(self, pgserver_module: Any) -> None:
        """Patch pgserver's Windows start path to tolerate recovery delays.

        The bundled pgserver package hardcodes ``timeout=10`` for ``pg_ctl
        start`` and writes its bootstrap log into ``PGDATA/log``. On Windows,
        crash recovery can take longer than 10 seconds and opening that log in
        place can fail with a sharing violation. We patch the package at runtime
        instead of vendoring or editing the dependency.
        """
        if os.name != "nt":
            return

        server_cls = getattr(pgserver_module, "PostgresServer", None)
        if server_cls is None or getattr(server_cls, "_anima_windows_startup_patched", False):
            return

        postgres_server_module = sys.modules.get("pgserver.postgres_server")
        if postgres_server_module is None:
            return

        original_ensure = getattr(server_cls, "ensure_postgres_running", None)
        original_pg_ctl = getattr(postgres_server_module, "pg_ctl", None)
        if not callable(original_ensure) or not callable(original_pg_ctl):
            return

        def patched_pg_ctl(args: list[str], pgdata: Path | None = None, **kwargs: Any) -> str:
            cmd_args = list(args)
            if cmd_args and cmd_args[-1] == "start":
                timeout = float(kwargs.get("timeout", 0) or 0)
                kwargs["timeout"] = max(
                    timeout, _PGSERVER_WINDOWS_START_TIMEOUT)
            return original_pg_ctl(cmd_args, pgdata=pgdata, **kwargs)

        def patched_ensure(server_self: Any) -> None:
            server_self.log = self._bootstrap_log_path()
            server_self.log.parent.mkdir(parents=True, exist_ok=True)
            original_ensure(server_self)

        postgres_server_module.pg_ctl = patched_pg_ctl
        server_cls.ensure_postgres_running = patched_ensure
        server_cls._anima_windows_startup_patched = True

    def _bootstrap_log_path(self) -> Path:
        return self._data_dir.parent / f"pg_bootstrap_{os.getpid()}.log"

    def _discard_stale_pgserver_instance(self, pgserver_module: Any, *, force: bool = False) -> None:
        instances = self._pgserver_instances(pgserver_module)
        if instances is None:
            return

        instance_key = self._data_dir.expanduser().resolve()
        server = instances.get(instance_key)
        if server is None:
            return
        if not force and self._pgserver_instance_is_ready(server):
            return

        cleanup = getattr(server, "cleanup", None)
        if callable(cleanup):
            try:
                cleanup()
            except Exception:
                logger.debug(
                    "Failed to clean up stale pgserver instance for %s",
                    instance_key,
                    exc_info=True,
                )

        instances.pop(instance_key, None)

    @staticmethod
    def _pgserver_instances(pgserver_module: Any) -> MutableMapping[Path, Any] | None:
        server_cls = getattr(pgserver_module, "PostgresServer", None)
        instances = getattr(server_cls, "_instances", None)
        if isinstance(instances, MutableMapping):
            return instances
        return None

    @staticmethod
    def _pgserver_instance_is_ready(server: Any) -> bool:
        get_uri = getattr(server, "get_uri", None)
        if not callable(get_uri):
            return False

        try:
            get_uri()
        except Exception:
            return False
        return True

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

        self._terminate_postgres_processes_for_data_dir()

    def _terminate_postgres_processes_for_data_dir(self) -> None:
        """Kill lingering postgres.exe processes still bound to this PGDATA."""
        try:
            import psutil
        except ImportError:
            return

        data_dir_str = str(self._data_dir.expanduser().resolve()).lower()
        for proc in psutil.process_iter(attrs=["name", "cmdline", "pid"]):
            try:
                name = str(proc.info.get("name") or "")
                cmdline = proc.info.get("cmdline") or []
            except (psutil.Error, OSError):
                continue

            if "postgres" not in name.lower():
                continue

            cmdline_text = " ".join(str(part) for part in cmdline).lower()
            if data_dir_str not in cmdline_text:
                continue

            try:
                proc.terminate()
                proc.wait(3)
            except psutil.TimeoutExpired:
                proc.kill()
            except (psutil.Error, OSError):
                logger.debug(
                    "Failed to terminate lingering postgres PID %s for %s",
                    proc.info.get("pid"),
                    self._data_dir,
                    exc_info=True,
                )

    @staticmethod
    def _probe_pid(pid: int) -> tuple[bool, bool]:
        """Return ``(exists, permission_denied)`` for ``pid``.

        ``os.kill(pid, 0)`` is a safe liveness probe on POSIX, but on Windows it
        can leave the current process in an interrupted state. Use the Win32 API
        there instead so stale-lock recovery does not destabilize pytest or app
        startup.
        """
        if pid <= 0:
            return False, False

        if os.name == "nt":
            try:
                import ctypes

                ERROR_ACCESS_DENIED = 5
                ERROR_INVALID_PARAMETER = 87
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                open_process = kernel32.OpenProcess
                open_process.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
                open_process.restype = ctypes.c_void_p
                close_handle = kernel32.CloseHandle
                close_handle.argtypes = [ctypes.c_void_p]
                close_handle.restype = ctypes.c_int

                handle = open_process(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
                if handle:
                    close_handle(handle)
                    return True, False

                error = ctypes.get_last_error()
                if error == ERROR_ACCESS_DENIED:
                    return True, True
                if error == ERROR_INVALID_PARAMETER:
                    return False, False

                logger.debug("OpenProcess failed while probing PID %s with Win32 error %s", pid, error)
                return False, False
            except Exception:
                logger.debug("Falling back to os.kill(pid, 0) while probing PID %s", pid, exc_info=True)

        try:
            os.kill(pid, 0)
        except PermissionError:
            return True, True
        except (ProcessLookupError, OSError):
            return False, False

        return True, False

    def _recover_stale_lockfile(self) -> bool:
        """Remove a stale postmaster.pid whose process no longer exists."""
        pid_file = self._data_dir / "postmaster.pid"
        if not pid_file.exists():
            return False

        try:
            pid = int(pid_file.read_text(encoding="utf-8").splitlines()[0])
        except (ValueError, IndexError):
            logger.warning(
                "Malformed postmaster.pid found at %s, removing", pid_file)
            pid_file.unlink(missing_ok=True)
            return True

        exists, permission_denied = self._probe_pid(pid)
        if permission_denied:
            # Process exists but is owned by another user — leave the
            # lockfile in place.  (Must come before the generic OSError
            # handler because PermissionError is a subclass of OSError.)
            logger.info(
                "postmaster.pid points to PID %d that exists but is owned by another user; "
                "leaving lockfile in place.",
                pid,
            )
            return False
        if not exists:
            # ProcessLookupError: PID does not exist (Unix).
            # OSError: PID does not exist or is invalid (Windows raises
            #          OSError / WinError instead of ProcessLookupError).
            logger.warning(
                "Stale postmaster.pid found (PID %d not running), removing",
                pid,
            )
            pid_file.unlink(missing_ok=True)
            return True

        return False

    def _clear_stale_log(self) -> None:
        """Truncate or remove stale PG bootstrap logs before restart."""
        for log_file in (self._data_dir / "log", self._bootstrap_log_path()):
            if not log_file.exists():
                continue
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
