from __future__ import annotations

import asyncio
import hmac
import ipaddress
import re

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from anima_server.services.credentials import (
    CredentialCapabilityError,
    CredentialError,
    broker_bootstrap_reference,
    credential_capability_broker,
    credential_store,
)
from anima_server.services.sessions import active_unlock_sessions

router = APIRouter(prefix="/api/credentials", tags=["credentials"])
_BROKER_HEADER = "x-anima-credential-broker"
_MOD_AUDIENCE_PATTERN = re.compile(r"\Aanima-mod:[a-z0-9][a-z0-9.-]{0,126}\Z")


class CredentialCapabilityIssueRequest(BaseModel):
    audience: str
    userId: int = Field(ge=0)
    references: list[str] = Field(min_length=1, max_length=32)
    ttlSeconds: int = Field(default=15, ge=1, le=30)


class CredentialCapabilityIssueResponse(BaseModel):
    capability: str
    expiresInSeconds: int


class CredentialCapabilityRedeemRequest(BaseModel):
    audience: str
    userId: int = Field(ge=0)
    capability: str


class CredentialCapabilityRedeemResponse(BaseModel):
    secrets: dict[str, str]


class CredentialWriteRequest(BaseModel):
    audience: str
    reference: str
    secret: str = Field(min_length=1, max_length=1024 * 1024)


class CredentialDeleteRequest(BaseModel):
    audience: str
    reference: str


def _is_loopback_request(request: Request) -> bool:
    if request.client is None:
        return False
    try:
        return ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        return False


def _is_user_unlocked(user_id: int) -> bool:
    return user_id == 0 or bool(active_unlock_sessions(user_id))


def _validate_mod_audience(audience: str) -> None:
    if not _MOD_AUDIENCE_PATTERN.fullmatch(audience):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid credential broker audience.",
        )


def _authorize_broker(request: Request, supplied: str | None) -> None:
    if not _is_loopback_request(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Credential broker is available only on loopback.",
        )
    if not supplied:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credential broker authentication is required.",
        )
    try:
        expected = credential_store().get(broker_bootstrap_reference())
    except CredentialError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    if expected is None or not hmac.compare_digest(expected, supplied):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credential broker authentication failed.",
        )


@router.post("/capabilities", response_model=CredentialCapabilityIssueResponse)
async def issue_credential_capability(
    payload: CredentialCapabilityIssueRequest,
    request: Request,
    broker_secret: str | None = Header(default=None, alias=_BROKER_HEADER),
) -> CredentialCapabilityIssueResponse:
    await asyncio.to_thread(_authorize_broker, request, broker_secret)
    _validate_mod_audience(payload.audience)
    if not _is_user_unlocked(payload.userId):
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="The requested Core owner is locked.",
        )
    try:
        capability = credential_capability_broker.issue(
            audience=payload.audience,
            user_id=payload.userId,
            references=payload.references,
            ttl_seconds=payload.ttlSeconds,
        )
    except CredentialCapabilityError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return CredentialCapabilityIssueResponse(
        capability=capability.token,
        expiresInSeconds=capability.expires_in_seconds,
    )


@router.post("/redeem", response_model=CredentialCapabilityRedeemResponse)
async def redeem_credential_capability(
    payload: CredentialCapabilityRedeemRequest,
    request: Request,
    broker_secret: str | None = Header(default=None, alias=_BROKER_HEADER),
) -> CredentialCapabilityRedeemResponse:
    await asyncio.to_thread(_authorize_broker, request, broker_secret)
    _validate_mod_audience(payload.audience)
    if not _is_user_unlocked(payload.userId):
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="The requested Core owner is locked.",
        )
    try:
        secrets_by_reference = await asyncio.to_thread(
            credential_capability_broker.consume,
            token=payload.capability,
            audience=payload.audience,
            user_id=payload.userId,
            store=credential_store(),
        )
    except CredentialCapabilityError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    except CredentialError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return CredentialCapabilityRedeemResponse(secrets=dict(secrets_by_reference))


@router.post("/secrets", status_code=status.HTTP_204_NO_CONTENT)
async def store_credential(
    payload: CredentialWriteRequest,
    request: Request,
    broker_secret: str | None = Header(default=None, alias=_BROKER_HEADER),
) -> None:
    await asyncio.to_thread(_authorize_broker, request, broker_secret)
    _validate_mod_audience(payload.audience)
    try:
        await asyncio.to_thread(
            credential_store().put,
            payload.reference,
            payload.secret,
        )
    except CredentialError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


@router.delete("/secrets", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(
    payload: CredentialDeleteRequest,
    request: Request,
    broker_secret: str | None = Header(default=None, alias=_BROKER_HEADER),
) -> None:
    await asyncio.to_thread(_authorize_broker, request, broker_secret)
    _validate_mod_audience(payload.audience)
    try:
        await asyncio.to_thread(credential_store().delete, payload.reference)
    except CredentialError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
