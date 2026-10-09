from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class MaintenanceCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currentPassword: SecretStr
    recoveryPhrase: SecretStr


class MaintenanceParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["prune", "resume", "cancel", "purge", "retire-key", "refresh-checkpoint"]
    targetStableId: str | None = Field(default=None, pattern=r"^[0-9A-HJKMNP-TV-Z]{26}$")
    expectedRevision: int | None = Field(default=None, gt=0)
    retiringFrkVersion: int | None = Field(default=None, gt=0)
    waiveRestoreHistory: bool = False
    acknowledgeBackupCopies: bool = False

    @model_validator(mode="after")
    def require_action_parameters(self):
        if self.action == "purge":
            if self.targetStableId is None or self.expectedRevision is None:
                raise ValueError("Purge requires a trash ID and its exact revision.")
            if not self.waiveRestoreHistory or not self.acknowledgeBackupCopies:
                raise ValueError(
                    "Purge requires explicit restore-history and backup acknowledgments."
                )
        elif (
            self.targetStableId is not None
            or self.expectedRevision is not None
            or self.waiveRestoreHistory
        ):
            raise ValueError("Trash parameters are valid only for purge.")
        if (self.action == "retire-key") != (self.retiringFrkVersion is not None):
            raise ValueError("Key retirement requires one specific old FRK version.")
        return self


class MaintenancePrepareRequest(MaintenanceCredentials):
    parameters: MaintenanceParameters
    backupPath: str = Field(min_length=1, max_length=4096)
    backupPassphrase: SecretStr


class MaintenanceExecuteRequest(MaintenancePrepareRequest):
    confirmation: str = Field(min_length=32, max_length=128)
    inventoryDigest: str = Field(pattern=r"^[0-9a-f]{64}$")
    observedAtMs: int = Field(gt=0)


class MaintenanceOperationResponse(BaseModel):
    id: str
    state: str
    quarantinedCount: int = Field(ge=0)
    sweptCount: int = Field(ge=0)
    totalCount: int = Field(ge=0)
    sourceHeadGeneration: int | None = None
    sourceHeadCatalogHash: str | None = None
    kind: Literal["prune", "purge", "reanchor"] | None = None
    expectedInventoryDigest: str | None = None


class MaintenanceInventoryResponse(BaseModel):
    version: int = 1
    retentionDays: int = 30
    headGeneration: int = Field(gt=0)
    headCatalogHash: str
    inventoryDigest: str
    retainedCatalogCount: int = Field(default=0, ge=0)
    catalogCount: int = Field(default=0, ge=0)
    objectCount: int = Field(default=0, ge=0)
    candidateCatalogCount: int = Field(default=0, ge=0)
    candidateObjectCount: int = Field(default=0, ge=0)
    candidatePreparationCount: int = Field(default=0, ge=0)
    candidateBytes: int = Field(default=0, ge=0)
    retainedUnknownAgeCount: int = Field(default=0, ge=0)
    requiredFrkVersions: list[int]
    blockers: list[str]
    warnings: list[str] = Field(default_factory=list)
    operation: MaintenanceOperationResponse | None = None
    resumeAuthorized: bool = False
    cancelAuthorized: bool = False
    trashExpiresAutomatically: bool = False
    externalCopiesErased: bool = False
    deletionLimits: str
    targetStableId: str | None = None
    expectedRevision: int | None = None
    recursiveInventoryDigest: str | None = None
    waivedGenerations: list[int] = Field(default_factory=list)
    targetObjectCount: int | None = None


class MaintenancePrepareResponse(BaseModel):
    confirmation: str
    expiresInSeconds: int = 120
    expiresAtMs: int
    inventory: MaintenanceInventoryResponse
    observedAtMs: int
    parameters: MaintenanceParameters
    backupVerified: bool = True


class MaintenanceResultResponse(BaseModel):
    success: bool
    inventory: MaintenanceInventoryResponse
    unlockToken: str | None = None
    retiredFrkVersion: int | None = None


class MaintenanceStatusResponse(BaseModel):
    retentionDays: int = 30
    unlockRequired: bool
    lastRun: MaintenanceInventoryResponse | None = None
