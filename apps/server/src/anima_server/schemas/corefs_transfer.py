from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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
