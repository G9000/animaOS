from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from anima_server.api.deps.unlock import require_unlocked_session
from anima_server.auth.policy import device_trust_store
from anima_server.services.sessions import unlock_session_store


router = APIRouter(prefix="/api/devices", tags=["devices"])


@router.get("")
async def list_devices(
    request: Request,
    include_revoked: bool = False,
) -> list[dict[str, Any]]:
    session = require_unlocked_session(request)
    user_id = session.user_id

    return [
        {
            "deviceId": record.device_id,
            "deviceName": record.device_name,
            "createdAt": record.created_at.isoformat(),
            "lastSeenAt": record.last_seen_at.isoformat(),
            "revoked": record.revoked,
            "secretPreview": device_trust_store.mask_device_secret(record.device_secret_hash),
        }
        for record in device_trust_store.list_devices(user_id, include_revoked=include_revoked)
    ]


@router.post("")
async def register_device(
    request: Request,
    payload: dict[str, Any],
) -> dict[str, Any]:
    session = require_unlocked_session(request)
    record, secret = device_trust_store.register_device(
        user_id=session.user_id,
        device_name=str(payload.get("deviceName", "desktop")).strip() or "desktop",
        secret=payload.get("deviceSecret"),
    )
    return {
        "deviceId": record.device_id,
        "deviceName": record.device_name,
        "createdAt": record.created_at.isoformat(),
        "lastSeenAt": record.last_seen_at.isoformat(),
        "revoked": record.revoked,
        "deviceSecret": secret,
        "secretPreview": device_trust_store.mask_device_secret(secret),
    }


@router.post("/{device_id}/rotate")
async def rotate_device_secret(
    device_id: str,
    request: Request,
) -> dict[str, Any]:
    session = require_unlocked_session(request)
    rotation = device_trust_store.rotate_secret(
        user_id=session.user_id,
        device_id=device_id,
    )
    if rotation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found",
        )
    record, secret = rotation
    return {
        "deviceId": record.device_id,
        "deviceName": record.device_name,
        "deviceSecret": secret,
        "secretPreview": device_trust_store.mask_device_secret(secret),
        "revoked": record.revoked,
    }


@router.delete("/{device_id}")
async def revoke_device(
    device_id: str,
    request: Request,
) -> dict[str, Any]:
    session = require_unlocked_session(request)
    if not device_trust_store.revoke_device(
        user_id=session.user_id,
        device_id=device_id,
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found",
        )
    unlock_session_store.revoke_user_device(session.user_id, device_id)
    return {"success": True, "deviceId": device_id}


@router.delete("")
async def clear_devices(
    request: Request,
) -> dict[str, Any]:
    session = require_unlocked_session(request)
    device_trust_store.clear_user_devices(session.user_id)
    unlock_session_store.clear_user_devices(session.user_id)
    return {"success": True, "userId": session.user_id}
