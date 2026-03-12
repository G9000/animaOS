from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class UserUpdateRequest(BaseModel):
    username: str | None = Field(default=None, min_length=1)
    name: str | None = Field(default=None, min_length=1)
    gender: Literal["male", "female", "other"] | None = None
    age: int | None = Field(default=None, gt=0, le=150)
    birthday: str | None = None


class DeleteUserResponse(BaseModel):
    message: str
