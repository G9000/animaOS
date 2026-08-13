from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from anima_server.schemas.corefs import normalize_logical_path

CoreArchivePayloadKindValue = Literal["full", "soul", "fs"]
CoreTransferOperationState = Literal[
    "prepared",
    "running",
    "verifying",
    "completed",
    "cancelled",
    "failed",
]


class CoreTransferPayloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payloadKind: CoreArchivePayloadKindValue = "full"


class CoreTransferDestinationRequest(CoreTransferPayloadRequest):
    destination: str = Field(min_length=1, max_length=4096)


class CoreTransferPrepareRequest(CoreTransferDestinationRequest):
    finalName: str = Field(
        default="ANIMA-CORE.anima-core",
        min_length=1,
        max_length=255,
        pattern=r"^[^/\\]+$",
    )
    passphrase: str = Field(min_length=8, max_length=1024)


class CoreImportProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    archivePath: str = Field(min_length=1, max_length=4096)
    stagingParent: str = Field(min_length=1, max_length=4096)


class CoreImportPrepareRequest(CoreImportProbeRequest):
    passphrase: str = Field(min_length=8, max_length=1024)


class CoreTransferEstimateResponse(BaseModel):
    payloadKind: CoreArchivePayloadKindValue
    selectedBytes: int
    recordCount: int
    archiveBytes: int
    requiredCapacityBytes: int
    soulGeneration: int | None
    filesystemGeneration: int | None


class CoreTransferProbeResponse(CoreTransferEstimateResponse):
    destination: str
    availableBytes: int
    maximumSingleFileBytes: int | None
    publicationMode: Literal["single_file", "multipart"]
    partLimitBytes: int | None
    declaredVolumeCount: int


class CoreTransferOperationResponse(BaseModel):
    operationId: str
    payloadKind: CoreArchivePayloadKindValue
    state: CoreTransferOperationState
    phase: str
    selectedBytes: int
    bytesPublished: int
    progressPercent: int = Field(ge=0, le=100)
    publicationMode: Literal["single_file", "multipart"]
    declaredVolumeCount: int
    resultPath: str | None
    archiveId: str | None
    errorCode: str | None


class CoreImportProbeResponse(BaseModel):
    archiveBytes: int
    stagingParent: str
    availableBytes: int
    requiredCapacityBytes: int


class CoreImportOperationResponse(BaseModel):
    operationId: str
    state: CoreTransferOperationState
    phase: str
    archiveBytes: int
    bytesProcessed: int
    progressPercent: int = Field(ge=0, le=100)
    payloadKind: CoreArchivePayloadKindValue | None
    recoveryState: Literal["complete", "filesystem_missing", "recovery_only"] | None
    archiveId: str | None
    activationId: str | None
    restartRequired: bool
    credentialsReplaced: bool
    recoveryExportOperationId: str | None
    errorCode: str | None


class CoreFsRecoveryCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sourceCredentialKind: Literal["password", "recovery"]
    sourceCredential: SecretStr = Field(min_length=1, max_length=1024)
    newPassword: SecretStr = Field(min_length=8, max_length=1024)
    confirmed: Literal[True]


class CoreFsRecoveryCredentialResponse(BaseModel):
    scope: Literal["fs"]
    recoveryPhrase: str = Field(min_length=1)
    passwordGeneration: int = Field(ge=1)
    recoveryGeneration: int = Field(ge=1)
    operation: CoreImportOperationResponse


class CoreFsRecoveryExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination: str = Field(min_length=1, max_length=4096)
    finalName: str = Field(
        default="ANIMA-CORE-FS-recovered.anima",
        min_length=1,
        max_length=255,
        pattern=r"^[^/\\]+$",
    )
    passphrase: SecretStr = Field(min_length=8, max_length=1024)
    credentialKind: Literal["password", "recovery"]
    credential: SecretStr = Field(min_length=1, max_length=1024)


class CoreFsRecoveryBrowseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["stat", "list", "read"]
    credentialKind: Literal["password", "recovery"]
    credential: SecretStr = Field(min_length=1, max_length=1024)
    path: str = ""
    cursorAfter: str | None = None
    cursorGeneration: int | None = Field(default=None, ge=1)
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0, le=(1 << 64) - 1)
    maxBytes: int = Field(default=65_536, ge=1, le=1_048_576)
    responseBytes: int | None = Field(default=None, ge=1024, le=10_485_760)

    @field_validator("path", "cursorAfter")
    @classmethod
    def validate_logical_paths(cls, value: str | None, info: Any) -> str | None:
        return normalize_logical_path(value, field_name=str(info.field_name))

    @model_validator(mode="after")
    def validate_cursor(self) -> CoreFsRecoveryBrowseRequest:
        if self.operation != "list" and self.cursorAfter is not None:
            raise ValueError("cursorAfter is available only for list recovery browsing.")
        if self.cursorAfter is not None and self.cursorGeneration is None:
            raise ValueError("cursorGeneration is required with cursorAfter.")
        if self.cursorGeneration is not None and self.cursorAfter is None:
            raise ValueError("cursorGeneration requires cursorAfter.")
        return self


class CoreFsRecoveryBrowseResponse(BaseModel):
    operation: Literal["stat", "list", "read"]
    generation: int
    catalogHash: str
    result: dict[str, Any] | None


class CoreRollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: Literal[True]


class CoreActiveStatusResponse(BaseModel):
    generation: int
    activeCoreId: str
    retainedCoreId: str | None
    activationId: str
    rollbackScheduled: bool
