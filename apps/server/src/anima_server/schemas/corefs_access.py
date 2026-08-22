from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ClientScope = Literal["none", "read", "write", "manage"]
ClientInstallationStatus = Literal[
    "pending",
    "approved",
    "reapproval_required",
    "collision",
    "revoked",
]
ExcludeClientNoneScope = Literal["read", "write", "manage"]


class CoreFsClientPublisherResponse(BaseModel):
    identity: str
    verified: bool


class CoreFsClientGrantResponse(BaseModel):
    folderStableId: str
    scope: ExcludeClientNoneScope
    approvedDigest: str
    generation: int
    updatedAt: str
    lastUsedAt: str | None = None


class CoreFsClientInstallationResponse(BaseModel):
    installationId: str
    clientId: str
    packageId: str
    displayName: str
    packageVersion: str
    installDigest: str
    publisher: CoreFsClientPublisherResponse | None = None
    declaredRoles: list[str]
    declaredMetadataKeys: list[str]
    status: ClientInstallationStatus
    approvedDigest: str | None = None
    grantGeneration: int
    verifiedAt: str
    approvedAt: str | None = None
    lastUsedAt: str | None = None
    grants: list[CoreFsClientGrantResponse]


class CoreFsGrantFolderResponse(BaseModel):
    stableId: str
    path: str
    role: str | None = None


class CoreFsClientAccessResponse(BaseModel):
    coreId: str
    localInstanceId: str
    deviceLocal: Literal[True] = True
    reapprovalRequiredAfterTransfer: bool
    installations: list[CoreFsClientInstallationResponse]
    folders: list[CoreFsGrantFolderResponse]


class CoreFsInstallationApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: Literal[True]


class CoreFsGrantUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: ClientScope
    confirmed: bool = False


class CoreFsClientCapabilityIssueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    audience: str
    clientId: str
    installDigest: str
    userId: int = Field(ge=0)
    ttlSeconds: int = Field(default=15, ge=1, le=15)


class CoreFsClientCapabilityIssueResponse(BaseModel):
    capability: str
    expiresInSeconds: int
