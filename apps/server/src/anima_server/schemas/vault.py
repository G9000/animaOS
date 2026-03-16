from __future__ import annotations

from pydantic import BaseModel, Field


from typing import Literal


class VaultExportRequest(BaseModel):
    passphrase: str = Field(min_length=8)
    scope: Literal["full", "memories"] = "full"


class VaultImportRequest(BaseModel):
    passphrase: str = Field(min_length=8)
    vault: str = Field(min_length=1)


class VaultExportResponse(BaseModel):
    filename: str
    vault: str
    size: int


class VaultImportResponse(BaseModel):
    status: str
    restoredUsers: int
    restoredMemoryFiles: int
    requiresReauth: bool = True
