from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PortablePreferencesUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any] = Field(default_factory=dict)


class PortablePreferencesResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: int = Field(alias="userId")
    values: dict[str, Any]
