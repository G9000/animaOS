from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class CoreFSFamilyReadinessResponse(BaseModel):
    total: int
    processed: int
    failed: int
    degraded: bool


class CoreFSReadinessResponse(BaseModel):
    state: str
    catalogGeneration: int | None
    processedObjects: int
    capabilities: list[str]
    retryable: bool
    families: dict[str, CoreFSFamilyReadinessResponse]


class CoreFSRotationStatusResponse(BaseModel):
    activeFrkVersion: int
    pendingFrkVersion: int | None
    decryptOnlyFrkVersions: list[int]
    phase: Literal["idle", "prepared", "verifying"]
    blindIndexGeneration: int | None
    blindIndexPendingGeneration: int | None
    blindIndexProgress: int


class CoreFSSecurityStatusResponse(BaseModel):
    coreId: str
    readiness: CoreFSReadinessResponse
    rotation: CoreFSRotationStatusResponse


class CoreFSRotateRequest(BaseModel):
    currentPassword: str
    recoveryPhrase: str


class CoreFSRotateResponse(BaseModel):
    success: bool
    unlockToken: str
    activeFrkVersion: int
    committedCatalogGeneration: int
    resumed: bool
