from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from anima_server.api.deps.db_mode import require_sqlite_mode
from anima_server.api.deps.unlock import require_unlocked_session
from anima_server.db import get_db
from anima_server.models import User
from anima_server.schemas.vault import (
    VaultExportRequest,
    VaultExportResponse,
    VaultImportRequest,
    VaultImportResponse,
)
from anima_server.services.sessions import unlock_session_store
from anima_server.services.vault import export_vault, import_vault

router = APIRouter(prefix="/api/vault", tags=["vault"])


@router.post("/export", response_model=VaultExportResponse)
def export_encrypted_vault(
    payload: VaultExportRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    session = require_unlocked_session(request)
    try:
        return export_vault(
            db,
            payload.passphrase,
            user_id=session.user_id,
            scope=payload.scope,
            transfer_format=payload.format,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/import", response_model=VaultImportResponse)
def import_encrypted_vault(
    payload: VaultImportRequest,
    request: Request,
    _mode: None = Depends(require_sqlite_mode),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    session = require_unlocked_session(request)
    current_user = db.get(User, session.user_id)
    if current_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    original_password_hash = current_user.password_hash

    try:
        result = import_vault(
            db,
            payload.vault,
            payload.passphrase,
            user_id=session.user_id,
            transfer_format=payload.format,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    imported_user = db.get(User, session.user_id)
    if imported_user is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Imported vault did not restore the authenticated user.",
        )
    imported_user.password_hash = original_password_hash
    db.commit()

    unlock_session_store.clear()
    return {"status": "ok", **result}
