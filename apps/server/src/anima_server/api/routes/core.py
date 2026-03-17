from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_session
from anima_server.config import settings
from anima_server.db import get_db
from anima_server.schemas.core import CoreStatusResponse
from anima_server.services.core import get_manifest_path

router = APIRouter(prefix="/api/core", tags=["core"])


def _sqlcipher_available() -> bool:
    try:
        import sqlcipher3  # noqa: F401
        return True
    except ImportError:
        return False


def _read_encryption_mode() -> str:
    """Read encryption_mode from the manifest file."""
    path = get_manifest_path()
    if path.is_file():
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            return str(manifest.get("encryption_mode", "none"))
        except Exception:  # noqa: BLE001
            pass
    return "none"


@router.get("/status", response_model=CoreStatusResponse)
def get_core_status(
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    require_unlocked_session(request)
    has_passphrase = bool(settings.core_passphrase)
    sqlcipher_installed = _sqlcipher_available()

    # Unified mode: encryption is active if there's a wrapped SQLCipher key
    from anima_server.services.core import has_wrapped_sqlcipher_key

    unified_mode = has_wrapped_sqlcipher_key()
    encryption_active = (has_passphrase or unified_mode) and sqlcipher_installed

    return {
        "encryption_active": encryption_active,
        "sqlcipher_available": sqlcipher_installed,
        "passphrase_set": has_passphrase or unified_mode,
        "encryption_mode": _read_encryption_mode(),
    }
