"""Parsing-pack lifecycle: is the Docling quality parser present and warmed?

The pack has two parts: the docling extra (installed at build/setup time)
and its model weights (fetched once, on demand). ``ensure_parsing_pack``
prefetches weights in a background thread so ingest never blocks; documents
processed before the pack is ready stay at preview quality and are upgraded
by reparse (see reparse.py).
"""

from __future__ import annotations

import importlib.util
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from anima_server.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_download_thread: threading.Thread | None = None
_error: str | None = None


@dataclass(frozen=True, slots=True)
class ParsingPackStatus:
    state: str  # "absent" | "downloading" | "ready" | "error"
    progress: float | None = None
    error: str | None = None


def _marker_path() -> Path:
    return settings.data_dir / "parsing-pack.ready"


def _docling_installed() -> bool:
    return importlib.util.find_spec("docling") is not None


def _prefetch_models() -> None:
    """Download docling model weights by converting a trivial in-memory doc."""
    from docling.utils.model_downloader import download_models

    download_models()


def pack_status() -> ParsingPackStatus:
    if not _docling_installed():
        return ParsingPackStatus(state="absent")
    with _lock:
        if _error is not None:
            return ParsingPackStatus(state="error", error=_error)
        if _download_thread is not None and _download_thread.is_alive():
            return ParsingPackStatus(state="downloading")
    if _marker_path().exists():
        return ParsingPackStatus(state="ready")
    return ParsingPackStatus(state="absent")


def parsing_pack_ready() -> bool:
    return pack_status().state == "ready"


def ensure_parsing_pack() -> ParsingPackStatus:
    global _download_thread, _error
    if not _docling_installed():
        return ParsingPackStatus(state="absent")
    if _marker_path().exists():
        return ParsingPackStatus(state="ready")
    with _lock:
        if _download_thread is None or not _download_thread.is_alive():
            _error = None
            _download_thread = threading.Thread(
                target=_download_and_mark, name="parsing-pack-download", daemon=True
            )
            _download_thread.start()
    return pack_status()


def _download_and_mark() -> None:
    global _error
    try:
        _prefetch_models()
        _marker_path().parent.mkdir(parents=True, exist_ok=True)
        _marker_path().write_text("1")
    except Exception as exc:
        logger.warning("Parsing pack download failed", exc_info=True)
        with _lock:
            _error = str(exc)


def _reset_state_for_tests() -> None:
    global _download_thread, _error
    with _lock:
        _download_thread = None
        _error = None


def _wait_for_download_for_tests(timeout: float) -> None:
    thread = _download_thread
    if thread is not None:
        thread.join(timeout)


__all__ = ["ParsingPackStatus", "ensure_parsing_pack", "pack_status", "parsing_pack_ready"]
