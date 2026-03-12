from __future__ import annotations

from fastapi import HTTPException, Request, status

from anima_server.services.sessions import UnlockSession, unlock_session_store


def read_unlock_token(request: Request) -> str | None:
    token = request.headers.get("x-anima-unlock")
    if token is None:
        return None
    normalized = token.strip()
    return normalized or None


def require_unlocked_user(request: Request, user_id: int) -> UnlockSession:
    session = unlock_session_store.resolve(read_unlock_token(request))
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session locked. Please sign in again.",
        )
    if session.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Session user mismatch.",
        )
    return session
