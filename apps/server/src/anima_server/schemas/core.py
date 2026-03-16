from __future__ import annotations

from pydantic import BaseModel


class CoreStatusResponse(BaseModel):
    encryption_active: bool
    sqlcipher_available: bool
    passphrase_set: bool
    encryption_mode: str = "none"
