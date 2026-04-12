from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


VaultTransferFormat = Literal["vault_json", "anima_capsule"]


class VaultExportRequest(BaseModel):
    passphrase: str = Field(min_length=8)
    scope: Literal["full", "memories"] = "full"
    format: VaultTransferFormat = "vault_json"


class VaultImportRequest(BaseModel):
    passphrase: str = Field(min_length=8)
    vault: str = Field(min_length=1)
    format: VaultTransferFormat = "vault_json"


class VaultExportResponse(BaseModel):
    filename: str
    vault: str
    size: int
    format: VaultTransferFormat = "vault_json"


class VaultImportResponse(BaseModel):
    status: str
    restoredUsers: int
    restoredMemoryFiles: int
    requiresReauth: bool = True
    format: VaultTransferFormat = "vault_json"
