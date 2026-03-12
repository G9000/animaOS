from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)
    name: str = Field(min_length=1)
    personaTemplate: Literal["default", "alice"] = "default"


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class UserResponse(BaseModel):
    id: int
    username: str
    name: str
    createdAt: str | None = None
    updatedAt: str | None = None


class RegisterResponse(UserResponse):
    unlockToken: str


class LoginResponse(UserResponse):
    unlockToken: str
    message: str


class LogoutResponse(BaseModel):
    success: bool
